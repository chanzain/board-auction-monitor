"""
核心模块：集合竞价数据采集 + 板块成交额汇总
支持双数据源：Tushare（默认）/ 东方财富（备用/补充）
"""

import tushare as ts
import pandas as pd
import os
import time
import sys
import importlib
from pathlib import Path
from typing import Optional
from datetime import date as date_type, datetime, time as dt_time, timedelta
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor, as_completed

from config import (
    TUSHARE_TOKEN,
    FOCUS_BOARDS,
    REQUEST_INTERVAL,
    MAX_CONCURRENT,
    TOP_N,
)

DATA_DIR = Path(__file__).parent / "data"
AUCTION_DIR = DATA_DIR / "auction"
SUMMARY_DIR = DATA_DIR / "board_summary"

CN_TZ = ZoneInfo("Asia/Shanghai")

# 初始化 Tushare
pro = ts.pro_api(TUSHARE_TOKEN)


def shanghai_now() -> datetime:
    """当前上海时区时间（带 tzinfo）"""
    return datetime.now(CN_TZ)


def is_call_auction_realtime_window(now: Optional[datetime] = None) -> bool:
    """
    是否处于集合竞价可观测实时成交额时段（上交所规则：9:15–9:30，不含 9:30 连续竞价起点）。
    该时段内东方财富 push 接口的成交额为盘中累积，适合轮询采集。
    """
    if now is None:
        now = shanghai_now()
    elif now.tzinfo is None:
        now = now.replace(tzinfo=CN_TZ)
    else:
        now = now.astimezone(CN_TZ)
    t = now.time()
    return dt_time(9, 15) <= t < dt_time(9, 30)


def is_same_calendar_day_as_shanghai_today(trade_date: str) -> bool:
    """trade_date 是否为 YYYYMMDD 或与 'today' 对应的上海日历当日"""
    if trade_date == "today":
        return True
    return trade_date == shanghai_now().strftime("%Y%m%d")


_AK_TRADE_DATES_DF = None


def _akshare_trade_dates_df():
    """懒加载全历史 A 股交易日（新浪），作 Tushare trade_cal 无权限时的回退"""
    global _AK_TRADE_DATES_DF
    if _AK_TRADE_DATES_DF is None:
        import akshare as ak
        _AK_TRADE_DATES_DF = ak.tool_trade_date_hist_sina()
    return _AK_TRADE_DATES_DF


def _previous_trade_day_akshare(ref_d: date_type) -> Optional[str]:
    try:
        df = _akshare_trade_dates_df()
        sub = df[df["trade_date"] < ref_d]
        if sub.empty:
            return None
        return sub["trade_date"].iloc[-1].strftime("%Y%m%d")
    except Exception as e:
        print(f"  AkShare 交易日历回退失败: {e}")
        return None


def get_previous_trade_day(ref_date: str) -> Optional[str]:
    """
    交易所视角的上一交易日（非自然日「昨天」）。
    ref_date: YYYYMMDD
    优先 Tushare trade_cal；无权限或失败时用 AkShare 新浪交易日历。
    """
    try:
        ref_d = datetime.strptime(ref_date, "%Y%m%d").date()
    except ValueError:
        return None
    start_date = (ref_d - timedelta(days=400)).strftime("%Y%m%d")
    end_date = ref_date
    try:
        cal = pro.trade_cal(
            exchange="SSE",
            start_date=start_date,
            end_date=end_date,
            is_open="1",
        )
        if cal is not None and not cal.empty:
            cal = cal.copy()
            cal["cal_date"] = cal["cal_date"].astype(str)
            before = cal[cal["cal_date"] < ref_date]
            if not before.empty:
                return str(before["cal_date"].iloc[-1])
    except Exception:
        pass

    return _previous_trade_day_akshare(ref_d)


# ── 东方财富数据源 ──────────────────────────────────────────────
def _fetch_from_eastmoney(trade_date: str) -> pd.DataFrame:
    """调用东方财富接口获取竞价数据，返回与 Tushare 格式对齐的 DataFrame"""
    try:
        from eastmoney_auction import get_auction_data_from_em, CACHE_FILE as EASTMONEY_CACHE_FILE
    except ImportError:
        print("[东方财富] eastmoney_auction 模块未找到，跳过")
        return pd.DataFrame()

    data = get_auction_data_from_em(trade_date)
    if not data or not data.get("stocks"):
        print("[东方财富] 未获取到数据")
        return pd.DataFrame()

    stocks = data["stocks"]
    df = pd.DataFrame(stocks)

    # 统一字段名，与 Tushare stk_auction 接口对齐
    # Tushare 字段: ts_code, trade_date, price, vol, amount, pre_close
    # 东方财富已返回: ts_code, name, trade_date, price, pre_close, volume, amount
    if "volume" in df.columns:
        df = df.rename(columns={"volume": "vol"})
    if "amount" not in df.columns:
        df["amount"] = 0.0

    # 强制转换数值列为 float，避免 object 类型导致 sum 报错
    for col in ["price", "vol", "amount", "pre_close"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    # 选取/补全 needed 字段
    needed = ["ts_code", "trade_date", "price", "vol", "amount", "pre_close", "name"]
    for col in needed:
        if col not in df.columns:
            df[col] = "" if col in ["ts_code", "trade_date", "name"] else 0.0

    df = df[needed]
    print(f"[东方财富] 获取到 {len(df)} 条竞价数据")

    # 与 Tushare 路径一致，落盘便于历史接口与个股明细读取
    if not df.empty and "trade_date" in df.columns:
        td = str(df["trade_date"].iloc[0])
        AUCTION_DIR.mkdir(parents=True, exist_ok=True)
        out = AUCTION_DIR / f"auction_{td}.csv"
        df.to_csv(out, index=False, encoding="utf-8-sig")
        print(f"  数据已保存: {out}")

    return df


def _fetch_from_akshare_spot_em(trade_date: str) -> pd.DataFrame:
    """
    最后备用：AkShare 封装的东财沪深京实时表（与 stock_zh_a_spot_em 一致）。
    非竞价时段 Tushare stk_auction 常为空；直连东财仍失败时用此路径。
    """
    try:
        import akshare as ak
        from eastmoney_auction import _em_code_to_tushare
    except ImportError:
        print("[AkShare] 未安装 akshare，跳过备用源")
        return pd.DataFrame()

    try:
        raw_df = ak.stock_zh_a_spot_em()
    except Exception as e:
        print(f"[AkShare] stock_zh_a_spot_em 失败: {e}")
        return pd.DataFrame()

    if raw_df is None or raw_df.empty:
        return pd.DataFrame()

    rows = []
    for _, row in raw_df.iterrows():
        code = str(row.get("代码", "")).strip()
        ts_code = _em_code_to_tushare(code)
        if not ts_code:
            continue
        vol_hand = float(pd.to_numeric(row.get("成交量"), errors="coerce") or 0)
        amount = float(pd.to_numeric(row.get("成交额"), errors="coerce") or 0)
        if vol_hand == 0 and amount == 0:
            continue
        rows.append({
            "ts_code": ts_code,
            "name": str(row.get("名称", "") or ""),
            "trade_date": trade_date,
            "price": float(pd.to_numeric(row.get("最新价"), errors="coerce") or 0),
            "pre_close": float(pd.to_numeric(row.get("昨收"), errors="coerce") or 0),
            "vol": vol_hand * 100.0,
            "amount": amount,
        })

    df = pd.DataFrame(rows)
    if df.empty:
        print("[AkShare] 解析后无有效成交行")
        return pd.DataFrame()

    print(f"[AkShare] 东财全市场行情 {len(df)} 条（备用源）")
    AUCTION_DIR.mkdir(parents=True, exist_ok=True)
    out = AUCTION_DIR / f"auction_{trade_date}.csv"
    df.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"  数据已保存: {out}")
    return df


def fetch_auction_data(trade_date: str, source: str = "tushare") -> pd.DataFrame:
    """
    获取指定日期的全市场集合竞价数据
    参数:
        trade_date: 交易日期（YYYYMMDD 或 "today"）
        source: 数据源，"tushare"（默认）/ "eastmoney" / "auto"
    返回: DataFrame[ts_code, trade_date, vol, price, amount, pre_close]
    """
    original_arg = trade_date
    if trade_date == "today":
        trade_date = shanghai_now().strftime("%Y%m%d")

    # ── auto 模式 ──
    # 当日 9:15–9:30：优先东方财富实时全市场行情（累积竞价成交额），Tushare  stk_auction 往往滞后或尚未就绪
    # 其他时段：先试 Tushare，失败再东方财富
    if source == "auto":
        print(f"正在获取 {trade_date} 集合竞价数据（自动模式）...")
        prefer_em = is_call_auction_realtime_window() and is_same_calendar_day_as_shanghai_today(
            original_arg
        )
        if prefer_em:
            print("  当前为集合竞价实时窗口(9:15-9:30)，优先使用东方财富实时源...")
            df = _fetch_from_eastmoney(trade_date)
            if df is not None and not df.empty:
                return df
            print("  [auto] 东方财富无数据，改试 Tushare...")
        df = _fetch_from_tushare(trade_date)
        if df is not None and not df.empty:
            return df
        print("[auto] Tushare 无数据，切换为东方财富源...")
        df = _fetch_from_eastmoney(trade_date)
        if df is not None and not df.empty:
            return df
        print("[auto] 东方财富仍无数据，尝试 AkShare 东财全市场行情（备用）...")
        return _fetch_from_akshare_spot_em(trade_date)

    if source == "eastmoney":
        df = _fetch_from_eastmoney(trade_date)
        if df is not None and not df.empty:
            return df
        print("[eastmoney] 直连东财无数据，尝试 AkShare 备用...")
        return _fetch_from_akshare_spot_em(trade_date)

    df = _fetch_from_tushare(trade_date)
    if df is not None and not df.empty:
        return df
    print("[tushare] 无数据，尝试东方财富...")
    df = _fetch_from_eastmoney(trade_date)
    if df is not None and not df.empty:
        return df
    print("[tushare] 东财无数据，尝试 AkShare 备用...")
    return _fetch_from_akshare_spot_em(trade_date)


def _fetch_from_tushare(trade_date: str) -> pd.DataFrame:
    """Tushare stk_auction 接口获取数据"""
    print(f"正在从 Tushare 获取 {trade_date} 集合竞价数据...")
    all_data = []
    offset = 0
    limit = 3000

    while True:
        try:
            df = pro.stk_auction(
                trade_date=trade_date,
                limit=limit,
                offset=offset,
            )
            if df is None or df.empty:
                break

            all_data.append(df)
            count = len(df)
            print(f"  已获取 {offset + count} 条...")

            if count < limit:
                break

            offset += limit
            time.sleep(REQUEST_INTERVAL * 2)

        except Exception as e:
            print(f"  Tushare 请求失败: {e}")
            break

    if not all_data:
        print("  Tushare 未获取到竞价数据")
        return pd.DataFrame()

    result = pd.concat(all_data, ignore_index=True)
    print(f"  Tushare 共获取 {len(result)} 条竞价数据")

    # 保存原始数据
    AUCTION_DIR.mkdir(parents=True, exist_ok=True)
    filepath = AUCTION_DIR / f"auction_{trade_date}.csv"
    result.to_csv(filepath, index=False, encoding="utf-8-sig")
    print(f"  数据已保存: {filepath}")

    return result


def aggregate_board_amount(
    auction_df: pd.DataFrame,
    board_map: dict,
    focus_boards: list = None,
    ma5_map: Optional[dict] = None,
) -> pd.DataFrame:
    """
    将个股竞价数据按板块汇总
    参数:
        auction_df: 个股竞价数据 DataFrame
        board_map: {板块名称: [stock_code, ...]}
        focus_boards: 关注的板块列表（优先展示）；为空时从 board_map 自动加载
    返回: 汇总后的 DataFrame，按成交额降序排列
    """
    if auction_df.empty:
        return pd.DataFrame()

    print("\n正在按板块汇总竞价成交额...")

    # 如果 focus_boards 为空，自动加载全部板块
    if not focus_boards:
        focus_boards = list(board_map.keys())

    results = []

    for board_name, stock_codes in board_map.items():
        # 过滤出该板块的成分股
        board_auction = auction_df[auction_df["ts_code"].isin(stock_codes)]

        if board_auction.empty:
            continue

        total_amount = board_auction["amount"].sum()
        total_vol = board_auction["vol"].sum()
        stock_count = len(board_auction)
        avg_amount = total_amount / stock_count if stock_count > 0 else 0

        amt_sum = float(pd.to_numeric(board_auction["amount"], errors="coerce").fillna(0).sum())
        if amt_sum > 0:
            auction_avg_price = float(
                (
                    pd.to_numeric(board_auction["price"], errors="coerce").fillna(0)
                    * pd.to_numeric(board_auction["amount"], errors="coerce").fillna(0)
                ).sum()
                / amt_sum
            )
        else:
            auction_avg_price = float(pd.to_numeric(board_auction["price"], errors="coerce").mean() or 0)

        board_ma5_vals = []
        if ma5_map:
            for ts in board_auction["ts_code"].astype(str):
                mv = ma5_map.get(ts)
                if mv is not None:
                    try:
                        board_ma5_vals.append(float(mv))
                    except (TypeError, ValueError):
                        pass
        board_ma5_price = (
            float(sum(board_ma5_vals) / len(board_ma5_vals)) if board_ma5_vals else float("nan")
        )

        # 计算板块平均涨幅（基于开盘价 vs 昨收）
        board_auction_valid = board_auction.dropna(subset=["price", "pre_close"])
        if not board_auction_valid.empty and (board_auction_valid["pre_close"] != 0).any():
            change_pct = (
                (board_auction_valid["price"] - board_auction_valid["pre_close"])
                / board_auction_valid["pre_close"]
                * 100
            )
            avg_change = change_pct.mean()
            rise_count = (change_pct > 0).sum()
            fall_count = (change_pct < 0).sum()
            flat_count = (change_pct == 0).sum()
        else:
            avg_change = 0
            rise_count = 0
            fall_count = 0
            flat_count = stock_count

        results.append({
            "板块名称": board_name,
            "成交额(元)": total_amount,
            "成交量(股)": total_vol,
            "统计股数": stock_count,
            "平均每只成交额": avg_amount,
            "竞价均价(元)": auction_avg_price,
            "五日线均价(元)": board_ma5_price,
            "平均涨幅%": round(avg_change, 2),
            "上涨数": rise_count,
            "下跌数": fall_count,
            "平盘数": flat_count,
        })

    if not results:
        return pd.DataFrame()

    result_df = pd.DataFrame(results)

    # 标记是否为关注板块（仅标记，不影响排序）
    focus_set = set(focus_boards) if focus_boards else set()
    result_df["关注"] = result_df["板块名称"].apply(lambda x: "⭐" if x in focus_set else "")

    # 排序：全部按成交额降序
    result_df = result_df.sort_values(
        by=["成交额(元)"], ascending=[False]
    )
    result_df = result_df.reset_index(drop=True)
    result_df.index = result_df.index + 1  # 排名从1开始
    result_df.index.name = "排名"

    return result_df


def save_summary(
    summary_df: pd.DataFrame,
    trade_date: str,
    source: str = "tushare",
    category_suffix: Optional[str] = None,
):
    """
    保存汇总数据为 CSV（同时记录数据源）。
    category_suffix: 为 concept / industry 时写入 board_auction_{date}_{suffix}.csv；
    为 None 时写入 board_auction_{date}.csv 并额外写 Excel（主文件）。
    """
    SUMMARY_DIR.mkdir(parents=True, exist_ok=True)

    summary_df = summary_df.copy()
    summary_df["data_source"] = source

    extra = f"_{category_suffix}" if category_suffix else ""
    csv_path = SUMMARY_DIR / f"board_auction_{trade_date}{extra}.csv"
    summary_df.to_csv(csv_path, encoding="utf-8-sig")
    print(f"  CSV 已保存: {csv_path}")

    if not category_suffix:
        excel_path = SUMMARY_DIR / f"board_auction_{trade_date}.xlsx"
        summary_df.to_excel(excel_path, engine="openpyxl")
        print(f"  Excel 已保存: {excel_path}")


def run_analysis(trade_date: str = "today", top_n: int = TOP_N, source: str = "auto"):
    """
    执行完整的竞价数据分析流程
    参数:
        trade_date: 交易日期
        top_n: 显示前N个板块
        source: 数据源（tushare/eastmoney/auto）
    返回: (原始竞价数据, 板块汇总数据)
    """
    # 1. 获取竞价数据
    auction_df = fetch_auction_data(trade_date, source=source)
    if auction_df.empty:
        return pd.DataFrame(), pd.DataFrame()

    actual_date = auction_df["trade_date"].iloc[0]

    # 2. 加载板块映射
    from board_data import load_board_map
    board_map = load_board_map()
    if not board_map:
        print("板块映射为空，请先运行 refresh_board_data() 更新板块成分股")
        print("  python main.py --init-boards")
        return auction_df, pd.DataFrame()

    # 3. 汇总
    summary_df = aggregate_board_amount(auction_df, board_map, FOCUS_BOARDS)

    if not summary_df.empty:
        # 4. 保存（带数据源信息）
        save_summary(summary_df, actual_date, source=source)

        # 5. 打印 Top N
        display_df = summary_df.head(top_n).copy()
        display_df["成交额"] = display_df["成交额(元)"].apply(format_amount)
        display_df["平均每只"] = display_df["平均每只成交额"].apply(format_amount)

        print(f"\n{'='*60}")
        print(f"  板块集合竞价成交额 Top {top_n}")
        print(f"  日期: {actual_date}")
        print(f"{'='*60}")
        cols = ["板块名称", "关注", "成交额", "平均每只", "统计股数", "平均涨幅%", "上涨数", "下跌数"]
        print(display_df[cols].to_string())
        print(f"{'='*60}")

    return auction_df, summary_df


def fetch_history_days(days: int = 5, source: str = "auto"):
    """
    采集最近N个交易日的竞价数据
    """
    # 获取最近的交易日历
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=days + 10)).strftime("%Y%m%d")

    print(f"正在获取最近 {days} 个交易日的日历...")
    try:
        cal = pro.trade_cal(
            exchange="SSE",
            start_date=start_date,
            end_date=end_date,
            is_open="1",
        )
        trade_dates = cal["cal_date"].tolist()[-days:]
    except Exception as e:
        print(f"获取交易日历失败: {e}")
        return

    print(f"将采集以下日期: {trade_dates}")

    from board_data import load_board_map
    board_map = load_board_map()
    if not board_map:
        print("板块映射为空，请先运行: python main.py --init-boards")
        return

    for date in trade_dates:
        print(f"\n{'#'*60}")
        print(f"  采集 {date}")
        print(f"{'#'*60}")

        auction_df = fetch_auction_data(date, source=source)
        if not auction_df.empty:
            summary_df = aggregate_board_amount(auction_df, board_map, FOCUS_BOARDS)
            if not summary_df.empty:
                save_summary(summary_df, date, source=source)

        time.sleep(1)


def format_amount(amount: float) -> str:
    """格式化金额显示"""
    if pd.isna(amount) or amount == 0:
        return "-"
    if amount >= 1e8:
        return f"{amount / 1e8:.2f}亿"
    elif amount >= 1e4:
        return f"{amount / 1e4:.2f}万"
    else:
        return f"{amount:.0f}"
