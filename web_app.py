"""
Flask Web 服务 - 板块竞价数据可视化面板
"""

import os
import json
from pathlib import Path
from datetime import datetime

from flask import Flask, render_template, jsonify, request

from config import WEB_HOST, WEB_PORT
from board_data import load_board_map
from auction_monitor import (
    fetch_auction_data,
    aggregate_board_amount,
    format_amount,
    get_previous_trade_day,
    FOCUS_BOARDS,
    TOP_N,
)

app = Flask(__name__)

DATA_DIR = Path(__file__).parent / "data"
SUMMARY_DIR = DATA_DIR / "board_summary"
AUCTION_DIR = DATA_DIR / "auction"
WATCHLIST_FILE = DATA_DIR / "my_watchlist.json"

# ========== 股票名称缓存 ==========
_stock_name_cache = None  # {ts_code: name}
_stock_name_cache_file = DATA_DIR / "stock_name_cache.json"


def get_stock_name_map():
    """获取股票代码→名称映射（带文件缓存）"""
    global _stock_name_cache
    if _stock_name_cache is not None:
        return _stock_name_cache

    # 尝试从本地缓存加载
    if _stock_name_cache_file.exists():
        try:
            _stock_name_cache = json.loads(_stock_name_cache_file.read_text(encoding="utf-8"))
            return _stock_name_cache
        except Exception:
            pass

    # 从 Tushare 获取
    try:
        import tushare as ts
        from config import TUSHARE_TOKEN
        pro = ts.pro_api(TUSHARE_TOKEN)
        df = pro.stock_basic(exchange="", list_status="L", fields="ts_code,name")
        if df is not None and not df.empty:
            _stock_name_cache = dict(zip(df["ts_code"], df["name"]))
            # 保存到本地缓存
            try:
                _stock_name_cache_file.write_text(
                    json.dumps(_stock_name_cache, ensure_ascii=False),
                    encoding="utf-8",
                )
            except Exception:
                pass
            return _stock_name_cache
    except Exception as e:
        print(f"[警告] 获取股票名称失败: {e}")

    _stock_name_cache = {}
    return _stock_name_cache


def get_stock_name(ts_code):
    """根据股票代码获取名称"""
    name_map = get_stock_name_map()
    return name_map.get(ts_code, "")


@app.route("/")
def index():
    """主页面"""
    # 加载可用的历史数据日期列表
    available_dates = []
    if SUMMARY_DIR.exists():
        for f in sorted(SUMMARY_DIR.glob("board_auction_*.csv"), reverse=True):
            date_str = f.stem.replace("board_auction_", "")
            available_dates.append(date_str)

    # 查找今天的汇总数据
    today_str = datetime.now().strftime("%Y%m%d")
    today_summary = None
    today_csv = SUMMARY_DIR / f"board_auction_{today_str}.csv"
    if today_csv.exists():
        today_summary = load_summary_csv(today_csv)

    return render_template(
        "index.html",
        available_dates=available_dates,
        today_summary=today_summary,
        today_date=today_str,
        focus_boards=FOCUS_BOARDS,
    )


def get_previous_trade_date(date_str):
    """上一交易日（按交易所日历，非自然日「昨天」、非「本地最近有文件的日期」）"""
    return get_previous_trade_day(date_str)


def calculate_amount_change(current_df, prev_date_str):
    """计算当前数据相比前一天的成交额变化"""
    if not prev_date_str:
        return {}

    prev_csv = SUMMARY_DIR / f"board_auction_{prev_date_str}.csv"
    if not prev_csv.exists():
        return {}

    try:
        import pandas as pd
        prev_df = pd.read_csv(prev_csv, encoding="utf-8-sig")
        if "排名" in prev_df.columns:
            prev_df = prev_df.set_index("排名")

        # 构建板块名称到成交额的映射
        change_map = {}
        for idx, row in prev_df.iterrows():
            change_map[row["板块名称"]] = _safe_float(row["成交额(元)"])
        return change_map
    except Exception:
        return {}


def _safe_float(val, default=0.0):
    """安全转换浮点数，NaN/None/inf 返回默认值"""
    import math
    if val is None:
        return default
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except (ValueError, TypeError):
        return default


def _safe_int(val, default=0):
    """安全转换整数"""
    try:
        f = float(val)
        import math
        if math.isnan(f) or math.isinf(f):
            return default
        return int(f)
    except (ValueError, TypeError):
        return default


def _compute_market_stats(df):
    """计算单个市场的竞价统计数据"""
    total_amount = _safe_float(df["amount"].sum())
    valid = df.dropna(subset=["price", "pre_close"])
    valid = valid[valid["pre_close"] != 0]
    if not valid.empty:
        changes = (valid["price"] - valid["pre_close"]) / valid["pre_close"] * 100
        avg_change = round(_safe_float(changes.mean()), 2)
    else:
        avg_change = 0.0
    return {
        "auction_amount": total_amount,
        "avg_change_pct": avg_change,
        "stock_count": len(df),
    }


def compute_market_data(auction_df):
    """从竞价数据计算各市场汇总（全A/上证/深证/创业板）"""
    import pandas as pd
    result = {}

    # 全A：所有股票
    result["全A"] = _compute_market_stats(auction_df)

    # 上证：6开头 + .SH（主板+科创板）
    sh_df = auction_df[auction_df["ts_code"].str.match(r"^6\d+\.SH$", na=False)]
    result["上证"] = _compute_market_stats(sh_df)

    # 深证：0开头 + .SZ（主板+中小板）
    sz_main_df = auction_df[auction_df["ts_code"].str.match(r"^0\d+\.SZ$", na=False)]
    result["深证"] = _compute_market_stats(sz_main_df)

    # 创业板：3开头 + .SZ
    cyb_df = auction_df[auction_df["ts_code"].str.match(r"^3\d+\.SZ$", na=False)]
    result["创业板"] = _compute_market_stats(cyb_df)

    return result


def build_board_rows(summary_df, prev_date):
    """构建板块数据行"""
    change_map = calculate_amount_change(summary_df, prev_date) if prev_date else {}

    rows = []
    for idx, row in summary_df.iterrows():
        board_name = str(row["板块名称"])
        current_amount = _safe_float(row["成交额(元)"])
        prev_amount = change_map.get(board_name, None)

        if prev_amount is not None:
            amount_change = current_amount - prev_amount
            amount_change_display = format_amount(abs(amount_change))
            if amount_change > 0:
                amount_change_display = f"+{amount_change_display}"
            elif amount_change < 0:
                amount_change_display = f"-{amount_change_display}"
            else:
                amount_change_display = "0"
        else:
            amount_change = 0
            amount_change_display = "--"

        avg_per_stock = _safe_float(row["平均每只成交额"])

        rows.append({
            "rank": _safe_int(idx, idx+1) if isinstance(idx, int) else idx + 1,
            "name": board_name,
            "focus": str(row.get("关注", "")),
            "amount": current_amount,
            "amount_display": format_amount(current_amount),
            "amount_change": amount_change,
            "amount_change_display": amount_change_display,
            "avg_amount": format_amount(avg_per_stock),
            "stock_count": _safe_int(row["统计股数"]),
            "avg_change": _safe_float(row["平均涨幅%"]),
            "rise": _safe_int(row["上涨数"]),
            "fall": _safe_int(row["下跌数"]),
            "prev_date": prev_date,
        })

    return rows


@app.route("/api/fetch", methods=["POST"])
def api_fetch():
    """实时采集竞价数据"""
    try:
        trade_date = request.json.get("date", "today")
        board_name = request.json.get("board_name", "")  # 可选：指定板块名称
        page = int(request.json.get("page", 1))
        page_size = int(request.json.get("page_size", 9999))
        source = request.json.get("source", "auto")      # tushare / eastmoney / auto

        auction_df = fetch_auction_data(trade_date, source=source)

        if auction_df.empty:
            return jsonify({"success": False, "message": "未获取到竞价数据"})

        board_map = load_board_map()
        if not board_map:
            return jsonify({
                "success": False,
                "message": "板块映射为空，请先初始化板块数据",
            })

        summary_df = aggregate_board_amount(auction_df, board_map, FOCUS_BOARDS)
        if summary_df.empty:
            return jsonify({"success": False, "message": "汇总失败"})

        actual_date = auction_df["trade_date"].iloc[0]

        # 保存
        from auction_monitor import save_summary
        save_summary(summary_df, actual_date, source=source)

        # 如果指定了板块名称，只返回该板块数据
        if board_name:
            summary_df = summary_df[summary_df["板块名称"].str.contains(board_name, na=False)]

        # 计算与前一天的变化
        prev_date = get_previous_trade_date(actual_date)

        # 分页
        total = len(summary_df)
        start = (page - 1) * page_size
        end = start + page_size
        paged_df = summary_df.iloc[start:end]

        rows = build_board_rows(paged_df, prev_date)

        return jsonify({
            "success": True,
            "date": actual_date,
            "prev_date": prev_date,
            "source": source,
            "total_amount": float(auction_df["amount"].sum()),
            "total_stocks": len(auction_df),
            "total_boards": len(summary_df),
            "total_count": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
            "data": rows,
        })

    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/history/<date_str>")
def api_history(date_str):
    """查看历史汇总数据（支持分页和板块过滤）"""
    csv_path = SUMMARY_DIR / f"board_auction_{date_str}.csv"
    if not csv_path.exists():
        return jsonify({"success": False, "message": f"未找到 {date_str} 的数据"})

    summary_df = load_summary_csv(csv_path)
    if summary_df is None or summary_df.empty:
        return jsonify({"success": False, "message": "数据为空"})

    # 获取请求参数
    board_name = request.args.get("board_name", "")
    page = int(request.args.get("page", 1))
    page_size = int(request.args.get("page_size", 9999))

    # 获取全A成交额 + 总股票数（从auction文件）
    total_amount = 0
    total_stocks = 0
    auction_csv = AUCTION_DIR / f"auction_{date_str}.csv"
    if auction_csv.exists():
        try:
            import pandas as pd
            auction_df = pd.read_csv(auction_csv, encoding="utf-8-sig")
            total_amount = float(auction_df["amount"].sum())
            total_stocks = len(auction_df)
        except Exception:
            pass

    # 如果指定了板块名称，过滤
    if board_name:
        summary_df = summary_df[summary_df["板块名称"].str.contains(board_name, na=False)]

    # 计算与前一天的变化
    prev_date = get_previous_trade_date(date_str)

    # 分页
    total = len(summary_df)
    start = (page - 1) * page_size
    end = start + page_size
    paged_df = summary_df.iloc[start:end]

    rows = build_board_rows(paged_df, prev_date)

    # 读取数据源信息
    data_source = ""
    if "data_source" in summary_df.columns:
        source_vals = summary_df["data_source"].dropna().unique()
        data_source = source_vals[0] if len(source_vals) > 0 else ""

    return jsonify({
        "success": True,
        "date": date_str,
        "prev_date": prev_date,
        "source": data_source,
        "total_amount": total_amount,
        "total_stocks": total_stocks,
        "total_boards": total,
        "total_count": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size,
        "data": rows,
    })


@app.route("/api/board_stocks/<date_str>/<board_name>")
def api_board_stocks(date_str, board_name):
    """获取指定板块的个股详情"""
    try:
        page = int(request.args.get("page", 1))
        page_size = int(request.args.get("page_size", 0))  # 默认0=返回全部

        # 加载板块映射
        board_map = load_board_map()
        if not board_map or board_name not in board_map:
            return jsonify({"success": False, "message": f"未找到板块: {board_name}"})

        # 获取该板块的股票代码列表
        stock_codes = board_map.get(board_name, [])
        if not stock_codes:
            return jsonify({"success": False, "message": f"板块 {board_name} 无成分股"})

        # 加载当天的竞价数据
        auction_csv = AUCTION_DIR / f"auction_{date_str}.csv"
        if not auction_csv.exists():
            return jsonify({"success": False, "message": f"未找到 {date_str} 的竞价数据"})

        import pandas as pd
        auction_df = pd.read_csv(auction_csv, encoding="utf-8-sig")

        # 过滤出该板块的个股
        board_stocks = auction_df[auction_df["ts_code"].isin(stock_codes)]

        if board_stocks.empty:
            return jsonify({"success": False, "message": "无个股数据"})

        # 预加载股票名称映射
        name_map = get_stock_name_map()

        # 计算前一天的数据
        prev_date = get_previous_trade_date(date_str)
        prev_stock_data = {}
        if prev_date:
            prev_auction_csv = AUCTION_DIR / f"auction_{prev_date}.csv"
            if prev_auction_csv.exists():
                prev_df = pd.read_csv(prev_auction_csv, encoding="utf-8-sig")
                for idx, row in prev_df.iterrows():
                    prev_stock_data[row["ts_code"]] = {
                        "amount": _safe_float(row["amount"]),
                        "price": _safe_float(row["price"]),
                        "pre_close": _safe_float(row["pre_close"]),
                    }

        # 构建返回数据
        all_rows = []
        for idx, row in board_stocks.iterrows():
            ts_code = str(row["ts_code"])
            amount = _safe_float(row["amount"])
            price = _safe_float(row["price"])
            pre_close = _safe_float(row["pre_close"])
            vol = _safe_float(row.get("vol", 0))

            # 计算涨跌幅
            if pre_close != 0:
                change_pct = (price - pre_close) / pre_close * 100
                change_price = price - pre_close
            else:
                change_pct = 0
                change_price = 0

            # 对比前一天
            prev_info = prev_stock_data.get(ts_code, None)
            if prev_info:
                amount_change = amount - prev_info["amount"]
                amount_change_display = format_amount(abs(amount_change))
                if amount_change > 0:
                    amount_change_display = f"+{amount_change_display}"
                elif amount_change < 0:
                    amount_change_display = f"-{amount_change_display}"
                else:
                    amount_change_display = "0"

                # 前一天涨跌幅
                prev_pre_close = prev_info["pre_close"]
                prev_price = prev_info["price"]
                if prev_pre_close != 0:
                    prev_change_pct = (prev_price - prev_pre_close) / prev_pre_close * 100
                else:
                    prev_change_pct = 0
            else:
                amount_change = 0
                amount_change_display = "--"
                prev_change_pct = None

            all_rows.append({
                "ts_code": ts_code,
                "name": name_map.get(ts_code, ""),
                "amount": amount,
                "amount_display": format_amount(amount),
                "amount_change": amount_change_display,
                "change_pct": round(change_pct, 2),
                "change_price": round(change_price, 2),
                "price": price,
                "pre_close": pre_close,
                "vol": int(vol) if vol > 0 else 0,
                "prev_change_pct": round(prev_change_pct, 2) if prev_change_pct is not None else None,
            })

        # 按成交额排序
        all_rows.sort(key=lambda x: x["amount"], reverse=True)

        # 分页（page_size=0 返回全部）
        total = len(all_rows)
        if page_size > 0:
            start = (page - 1) * page_size
            end = start + page_size
            paged_rows = all_rows[start:end]
        else:
            paged_rows = all_rows

        return jsonify({
            "success": True,
            "date": date_str,
            "board_name": board_name,
            "prev_date": prev_date,
            "total_count": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size if page_size > 0 else 1,
            "data": paged_rows,
        })

    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/boards")
def api_boards():
    """获取所有板块名称列表"""
    board_map = load_board_map()
    if not board_map:
        return jsonify({"success": False, "message": "板块映射为空"})
    return jsonify({
        "success": True,
        "boards": sorted(board_map.keys()),
    })


@app.route("/api/history_dates")
def api_history_dates():
    """获取所有有数据的历史日期列表"""
    dates = []
    if SUMMARY_DIR.exists():
        for f in sorted(SUMMARY_DIR.glob("board_auction_*.csv"), reverse=True):
            date_str = f.stem.replace("board_auction_", "")
            dates.append(date_str)
    return jsonify({
        "success": True,
        "dates": dates,
    })


def load_summary_csv(filepath) -> "pd.DataFrame | None":
    """加载汇总CSV"""
    import pandas as pd
    try:
        df = pd.read_csv(filepath, encoding="utf-8-sig")
        if "排名" in df.columns:
            df = df.set_index("排名")
        return df
    except Exception:
        return None


@app.route("/api/market_overview")
def api_market_overview():
    """大盘数据对比：全A竞价成交额趋势 + 各市场每日数据"""
    import pandas as pd

    try:
        days = int(request.args.get("days", 15))
    except (ValueError, TypeError):
        days = 7

    # 支持日期范围查询
    start_date = request.args.get("start_date", "")
    end_date = request.args.get("end_date", "")

    if not AUCTION_DIR.exists():
        return jsonify({"success": False, "message": "暂无竞价数据"})

    # 扫描所有 auction CSV 文件，计算各市场数据
    market_data = []
    auction_files = sorted(AUCTION_DIR.glob("auction_*.csv"))

    for f in auction_files:
        date_str = f.stem.replace("auction_", "")
        try:
            df = pd.read_csv(f, encoding="utf-8-sig")
            if df.empty:
                continue
            stats = compute_market_data(df)
            market_data.append({
                "date": date_str,
                **stats,
            })
        except Exception:
            pass

    if not market_data:
        return jsonify({"success": False, "message": "未找到有效的竞价数据文件"})

    # 如果指定了日期范围，进行过滤
    if start_date or end_date:
        filtered_data = []
        for d in market_data:
            if start_date and d["date"] < start_date:
                continue
            if end_date and d["date"] > end_date:
                continue
            filtered_data.append(d)
        market_data = filtered_data

    if not market_data:
        return jsonify({"success": False, "message": "指定日期范围内未找到数据"})

    # 图表数据：从旧到新排列（Chart.js 需要时间顺序），取最近 days 个交易日
    if days > 0:
        chart_data = market_data[-days:] if len(market_data) >= days else market_data
    else:
        # days <= 0 时返回全部数据
        chart_data = market_data

    # 表格数据：从新到旧排列
    table_data = list(reversed(chart_data))

    return jsonify({
        "success": True,
        "chart_data": chart_data,
        "table_data": table_data,
    })


# ========== 我的关注：板块关注列表管理 ==========

def _load_watchlist():
    """加载关注列表，返回 list[str]"""
    if WATCHLIST_FILE.exists():
        try:
            data = json.loads(WATCHLIST_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except Exception:
            pass
    return []


def _save_watchlist(watchlist):
    """保存关注列表"""
    WATCHLIST_FILE.parent.mkdir(parents=True, exist_ok=True)
    WATCHLIST_FILE.write_text(
        json.dumps(watchlist, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


@app.route("/api/watchlist", methods=["GET"])
def api_get_watchlist():
    """获取关注板块列表"""
    return jsonify({
        "success": True,
        "watchlist": _load_watchlist(),
    })


@app.route("/api/watchlist", methods=["POST"])
def api_add_watchlist():
    """添加板块到关注列表（支持批量）"""
    data = request.get_json(silent=True) or {}
    board_name = data.get("board_name", "").strip()

    if not board_name:
        return jsonify({"success": False, "message": "板块名称不能为空"})

    watchlist = _load_watchlist()

    # 验证板块名称是否存在于板块映射中
    board_map = load_board_map()
    if board_map and board_name not in board_map:
        # 尝试模糊匹配提示
        similar = [b for b in board_map if board_name in b]
        if similar:
            return jsonify({
                "success": False,
                "message": f"未找到板块「{board_name}」，您是否要添加：{', '.join(similar[:5])}",
            })
        return jsonify({"success": False, "message": f"未找到板块「{board_name}」"})

    if board_name in watchlist:
        return jsonify({"success": False, "message": f"「{board_name}」已在关注列表中"})

    watchlist.append(board_name)
    _save_watchlist(watchlist)
    return jsonify({"success": True, "message": f"已添加「{board_name}」", "watchlist": watchlist})


@app.route("/api/watchlist", methods=["DELETE"])
def api_remove_watchlist():
    """从关注列表移除板块"""
    data = request.get_json(silent=True) or {}
    board_name = data.get("board_name", "").strip()

    if not board_name:
        return jsonify({"success": False, "message": "板块名称不能为空"})

    watchlist = _load_watchlist()
    if board_name not in watchlist:
        return jsonify({"success": False, "message": f"「{board_name}」不在关注列表中"})

    watchlist.remove(board_name)
    _save_watchlist(watchlist)
    return jsonify({"success": True, "message": f"已移除「{board_name}」", "watchlist": watchlist})


@app.route("/api/watchlist/data")
def api_watchlist_data():
    """获取关注板块在指定日期的竞价数据"""
    date_str = request.args.get("date", "")
    if not date_str:
        return jsonify({"success": False, "message": "请指定日期"})

    watchlist = _load_watchlist()
    if not watchlist:
        return jsonify({"success": True, "data": [], "watchlist": []})

    # 加载当天的汇总数据
    csv_path = SUMMARY_DIR / f"board_auction_{date_str}.csv"
    if not csv_path.exists():
        return jsonify({"success": False, "message": f"未找到 {date_str} 的数据"})

    summary_df = load_summary_csv(csv_path)
    if summary_df is None or summary_df.empty:
        return jsonify({"success": False, "message": "数据为空"})

    # 过滤出关注板块
    watch_df = summary_df[summary_df["板块名称"].isin(watchlist)]

    # 计算与前一天的变化
    prev_date = get_previous_trade_date(date_str)

    rows = build_board_rows(watch_df, prev_date)

    # 按成交额降序排列
    rows.sort(key=lambda x: x["amount"], reverse=True)

    return jsonify({
        "success": True,
        "date": date_str,
        "prev_date": prev_date,
        "watchlist": watchlist,
        "data": rows,
    })


# ========== 美股板块 ==========

@app.route("/api/us_sectors")
def api_us_sectors():
    """获取美股板块成交额数据（缓存优先）"""
    try:
        from us_sector import get_us_sector_data, load_us_sector_data
        force = request.args.get("refresh", "0") == "1"
        data = get_us_sector_data(force_refresh=force)
        if data:
            return jsonify({"success": True, **data})
        # 降级：加载过期缓存
        data = load_us_sector_data()
        if data:
            return jsonify({"success": True, "from_cache": True, **data})
        return jsonify({"success": False, "message": "数据获取失败"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/us_sectors/refresh", methods=["POST"])
def api_us_sectors_refresh():
    """后台强制刷新美股板块数据"""
    def do_refresh():
        from us_sector import get_us_sector_data
        get_us_sector_data(force_refresh=True)

    import threading
    t = threading.Thread(target=do_refresh)
    t.start()
    return jsonify({"success": True, "message": "正在后台刷新数据，请稍后刷新页面"})


# ========== 港股板块 ==========

@app.route("/api/hk_sectors")
def api_hk_sectors():
    """获取港股板块成交额数据（缓存优先）"""
    try:
        from hk_sector import get_hk_sector_data, load_hk_sector_data
        force = request.args.get("refresh", "0") == "1"
        data = get_hk_sector_data(force_refresh=force)
        if data:
            return jsonify({"success": True, **data})
        data = load_hk_sector_data()
        if data:
            return jsonify({"success": True, "from_cache": True, **data})
        return jsonify({"success": False, "message": "数据获取失败"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/hk_sectors/refresh", methods=["POST"])
def api_hk_sectors_refresh():
    """后台强制刷新港股板块数据"""
    def do_refresh():
        from hk_sector import get_hk_sector_data
        get_hk_sector_data(force_refresh=True)

    import threading
    t = threading.Thread(target=do_refresh)
    t.start()
    return jsonify({"success": True, "message": "正在后台刷新数据，请稍后刷新页面"})


if __name__ == "__main__":
    print(f"板块竞价监控 Web 服务启动中...")
    print(f"访问 http://{WEB_HOST}:{WEB_PORT} 查看面板")
    app.run(host=WEB_HOST, port=WEB_PORT, debug=True)
