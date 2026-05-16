"""
Flask Web 服务 - 板块竞价数据可视化面板
"""

import os
import json
import hashlib
from pathlib import Path
from datetime import datetime

from flask import Flask, render_template, jsonify, request

from config import WEB_HOST, WEB_PORT
from board_data import (
    load_board_map,
    load_board_map_for_category,
    load_concept_board_map,
    load_industry_board_map,
    board_maps_available,
)
from board_concept_intro import get_board_concept_intro
from stock_concept_map import (
    build_stock_concept_index,
    build_stock_concept_index_for_codes,
    format_concept_preview,
)
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
# 板块「近7日竞价趋势」接口的本地 JSON 缓存（点击加载后写入，下次直接读文件）
BOARD_TREND_CACHE_DIR = DATA_DIR / "board_trend_cache"
BOARD_TREND_CACHE_VERSION = 1
# 我的关注表格数据缓存（按日期 + 关注列表内容哈希）
WATCHLIST_DATA_CACHE_DIR = DATA_DIR / "watchlist_data_cache"
WATCHLIST_DATA_CACHE_VERSION = 3
# 板块竞价列表页 /api/history 与采集成功后的列表 JSON（按日期+分类+筛选+分页）
BOARD_HISTORY_CACHE_DIR = DATA_DIR / "board_history_cache"
BOARD_HISTORY_CACHE_VERSION = 1
# 板块成分股弹窗 /api/board_stocks 响应缓存
BOARD_STOCKS_CACHE_DIR = DATA_DIR / "board_stocks_cache"
BOARD_STOCKS_CACHE_VERSION = 1

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


_PAGE_TABS = frozenset({"board", "market", "watchlist", "usSector", "hkSector"})


def _normalize_initial_tab(tab: object) -> str:
    if tab is None:
        return "board"
    t = str(tab).strip()
    return t if t in _PAGE_TABS else "board"


def _render_index(initial_tab: str = "board"):
    """主页面：initial_tab 为前端首屏 Tab（与 URL 路由一致）"""
    available_dates = []
    if SUMMARY_DIR.exists():
        for f in sorted(SUMMARY_DIR.glob("board_auction_*.csv"), reverse=True):
            date_str = f.stem.replace("board_auction_", "")
            available_dates.append(date_str)

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
        initial_tab=_normalize_initial_tab(initial_tab),
    )


@app.route("/")
def index():
    return _render_index("board")


@app.route("/index")
@app.route("/board")
def index_board_aliases():
    """首页：与根路径相同"""
    return _render_index("board")


@app.route("/market")
@app.route("/dapan")
def index_market():
    """大盘数据对比"""
    return _render_index("market")


@app.route("/watchlist")
def index_watchlist():
    """我的关注"""
    return _render_index("watchlist")


@app.route("/us-sector")
def index_us_sector():
    """美股板块"""
    return _render_index("usSector")


@app.route("/hk-sector")
def index_hk_sector():
    """港股竞价"""
    return _render_index("hkSector")


_prev_trade_date_cache: dict = {}


def get_previous_trade_date(date_str):
    """上一交易日（按交易所日历，非自然日「昨天」、非「本地最近有文件的日期」）"""
    if date_str in _prev_trade_date_cache:
        return _prev_trade_date_cache[date_str]
    prev = get_previous_trade_day(date_str)
    _prev_trade_date_cache[date_str] = prev
    return prev


def _default_board_category() -> str:
    return "concept" if load_concept_board_map() else "industry"


def _normalize_board_category_arg(arg) -> str:
    want = (arg or "").strip().lower()
    has_c = bool(load_concept_board_map())
    has_i = bool(load_industry_board_map())
    if want == "industry" and has_i:
        return "industry"
    if want == "concept" and has_c:
        return "concept"
    if has_c:
        return "concept"
    if has_i:
        return "industry"
    return "concept"


def _infer_board_category(name: str) -> str:
    cm = load_concept_board_map()
    im = load_industry_board_map()
    in_c = bool(cm and name in cm)
    in_i = bool(im and name in im)
    if in_c and not in_i:
        return "concept"
    if in_i and not in_c:
        return "industry"
    if in_c:
        return "concept"
    return _default_board_category()


def _summary_csv_path_for_category(date_str: str, category: str):
    """当日汇总 CSV：优先分类文件，否则兼容旧版无后缀文件"""
    cat = _normalize_board_category_arg(category)
    tagged = SUMMARY_DIR / f"board_auction_{date_str}_{cat}.csv"
    if tagged.exists():
        return tagged
    legacy = SUMMARY_DIR / f"board_auction_{date_str}.csv"
    if legacy.exists():
        return legacy
    return None


def _board_amount_map_from_prev_date(prev_date_str: str, category: str) -> dict:
    """上一交易日各板块成交额，用于日环比（与当前分类对齐）"""
    if not prev_date_str:
        return {}
    cat = _normalize_board_category_arg(category)
    candidates = [
        SUMMARY_DIR / f"board_auction_{prev_date_str}_{cat}.csv",
        SUMMARY_DIR / f"board_auction_{prev_date_str}.csv",
    ]
    for p in candidates:
        if not p.exists():
            continue
        prev_df = load_summary_csv(p)
        if prev_df is None or prev_df.empty:
            continue
        return {
            str(row["板块名称"]): _safe_float(row["成交额(元)"])
            for idx, row in prev_df.iterrows()
        }
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


def _board_history_source_mtime(date_str: str, category: str) -> float:
    """列表依赖的本地文件变更时间（任一更新则缓存失效）。含当日 MA5 缓存，便于渐进补全后重算板块五日线。"""
    mt = 0.0
    auction_csv = AUCTION_DIR / f"auction_{date_str}.csv"
    if auction_csv.exists():
        mt = max(mt, auction_csv.stat().st_mtime)
    sp = _summary_csv_path_for_category(date_str, category)
    if sp and sp.exists():
        mt = max(mt, sp.stat().st_mtime)
    ma5p = DATA_DIR / "ma5_cache" / f"{date_str}.json"
    if ma5p.exists():
        mt = max(mt, ma5p.stat().st_mtime)
    return mt


def _board_history_cache_key_hex(
    date_str: str, category: str, board_name: str, page: int, page_size: int
) -> str:
    key = [BOARD_HISTORY_CACHE_VERSION, date_str, category, board_name or "", page, page_size]
    raw = json.dumps(key, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _try_load_board_history_cache(
    date_str: str, category: str, board_name: str, page: int, page_size: int,
):
    path = BOARD_HISTORY_CACHE_DIR / f"{_board_history_cache_key_hex(date_str, category, board_name, page, page_size)}.json"
    if not path.is_file():
        return None
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if blob.get("version") != BOARD_HISTORY_CACHE_VERSION:
        return None
    if blob.get("date_str") != date_str or blob.get("category") != category:
        return None
    if blob.get("board_name") != (board_name or "") or int(blob.get("page", -1)) != page or int(blob.get("page_size", -1)) != page_size:
        return None
    want_mt = _board_history_source_mtime(date_str, category)
    if abs(float(blob.get("source_mtime", 0)) - want_mt) > 1e-6:
        return None
    payload = blob.get("payload")
    if not isinstance(payload, dict) or not payload.get("success"):
        return None
    return blob


def _save_board_history_cache(
    date_str: str, category: str, board_name: str, page: int, page_size: int, payload: dict,
) -> None:
    BOARD_HISTORY_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = BOARD_HISTORY_CACHE_DIR / f"{_board_history_cache_key_hex(date_str, category, board_name, page, page_size)}.json"
    clean = {k: v for k, v in payload.items() if k not in ("from_cache", "cached_at")}
    blob = {
        "version": BOARD_HISTORY_CACHE_VERSION,
        "date_str": date_str,
        "category": category,
        "board_name": board_name or "",
        "page": page,
        "page_size": page_size,
        "source_mtime": _board_history_source_mtime(date_str, category),
        "cached_at": datetime.now().isoformat(timespec="seconds"),
        "payload": clean,
    }
    path.write_text(json.dumps(blob, ensure_ascii=False, indent=2), encoding="utf-8")


def _board_row_price_display(row, col_name: str) -> str:
    """板块汇总表中的价格列展示（缺列或非数值为 --）"""
    import math

    if col_name not in row.index:
        return "--"
    v = row[col_name]
    try:
        if v is None:
            return "--"
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return "--"
        return f"{f:.2f}"
    except (TypeError, ValueError):
        return "--"


def build_board_rows(summary_df, prev_date, category: str = None):
    """构建板块数据行；category 用于与上一交易日的分类汇总对齐（日环比）"""
    cat = _normalize_board_category_arg(category) if category else _default_board_category()
    change_map = _board_amount_map_from_prev_date(prev_date, cat) if prev_date else {}

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
            "latest_price_display": _board_row_price_display(row, "竞价均价(元)"),
            "ma5_price_display": _board_row_price_display(row, "五日线均价(元)"),
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
        category = _normalize_board_category_arg(request.json.get("category"))

        auction_df = fetch_auction_data(trade_date, source=source)

        if auction_df.empty:
            return jsonify({
                "success": False,
                "message": "未获取到竞价数据。请确认当日为交易日且网络正常；自动模式会依次尝试 Tushare、东财多节点与 AkShare。",
            })

        board_map = load_board_map_for_category(category)
        if not board_map:
            return jsonify({
                "success": False,
                "message": "板块映射为空，请先初始化板块数据",
            })

        from ma5_daily import get_ma5_map_progressive

        actual_date = str(auction_df["trade_date"].iloc[0])
        ma5_map = get_ma5_map_progressive(
            auction_df["ts_code"].astype(str).unique().tolist(),
            actual_date,
        )
        summary_df = aggregate_board_amount(auction_df, board_map, FOCUS_BOARDS, ma5_map=ma5_map)
        if summary_df.empty:
            return jsonify({"success": False, "message": "汇总失败"})

        from auction_monitor import save_summary

        concept_m = load_concept_board_map()
        industry_m = load_industry_board_map()
        if concept_m:
            save_summary(
                aggregate_board_amount(auction_df, concept_m, FOCUS_BOARDS, ma5_map=ma5_map),
                actual_date,
                source=source,
                category_suffix="concept",
            )
        if industry_m:
            save_summary(
                aggregate_board_amount(auction_df, industry_m, FOCUS_BOARDS, ma5_map=ma5_map),
                actual_date,
                source=source,
                category_suffix="industry",
            )
        primary_m = load_board_map()
        if primary_m:
            save_summary(
                aggregate_board_amount(auction_df, primary_m, FOCUS_BOARDS, ma5_map=ma5_map),
                actual_date,
                source=source,
                category_suffix=None,
            )

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

        rows = build_board_rows(paged_df, prev_date, category)

        payload = {
            "success": True,
            "date": actual_date,
            "prev_date": prev_date,
            "source": source,
            "board_category": category,
            "maps": board_maps_available(),
            "total_amount": float(auction_df["amount"].sum()),
            "total_stocks": len(auction_df),
            "total_boards": len(summary_df),
            "total_count": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
            "data": rows,
        }
        _save_board_history_cache(actual_date, category, board_name, page, page_size, payload)
        out = dict(payload)
        out["from_cache"] = False
        return jsonify(out)

    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/history/<date_str>")
def api_history(date_str):
    """查看历史汇总数据（支持分页、板块过滤、概念/行业分类）"""
    import pandas as pd

    category = _normalize_board_category_arg(request.args.get("category"))
    board_map = load_board_map_for_category(category)
    if not board_map:
        return jsonify({"success": False, "message": "板块映射为空，请先初始化板块数据"})

    board_name = request.args.get("board_name", "")
    page = int(request.args.get("page", 1))
    page_size = int(request.args.get("page_size", 9999))
    refresh = str(request.args.get("refresh", "")).strip().lower() in ("1", "true", "yes")

    if not refresh:
        hit = _try_load_board_history_cache(date_str, category, board_name, page, page_size)
        if hit is not None:
            out = dict(hit["payload"])
            out["from_cache"] = True
            out["cached_at"] = hit.get("cached_at")
            return jsonify(out)

    auction_csv = AUCTION_DIR / f"auction_{date_str}.csv"
    summary_df = None

    if auction_csv.exists():
        try:
            auction_df = pd.read_csv(auction_csv, encoding="utf-8-sig")
            from ma5_daily import get_ma5_map_progressive

            ma5_map = get_ma5_map_progressive(
                auction_df["ts_code"].astype(str).unique().tolist(),
                date_str,
            )
            summary_df = aggregate_board_amount(auction_df, board_map, FOCUS_BOARDS, ma5_map=ma5_map)
        except Exception:
            summary_df = None

    if summary_df is None or summary_df.empty:
        csv_path = _summary_csv_path_for_category(date_str, category)
        if csv_path is None:
            return jsonify({"success": False, "message": f"未找到 {date_str} 的数据"})
        summary_df = load_summary_csv(csv_path)
        if summary_df is None or summary_df.empty:
            return jsonify({"success": False, "message": "数据为空"})

    total_amount = 0
    total_stocks = 0
    if auction_csv.exists():
        try:
            auction_df = pd.read_csv(auction_csv, encoding="utf-8-sig")
            total_amount = float(auction_df["amount"].sum())
            total_stocks = len(auction_df)
        except Exception:
            pass

    if board_name:
        summary_df = summary_df[summary_df["板块名称"].str.contains(board_name, na=False)]

    prev_date = get_previous_trade_date(date_str)

    total = len(summary_df)
    start = (page - 1) * page_size
    end = start + page_size
    paged_df = summary_df.iloc[start:end]

    rows = build_board_rows(paged_df, prev_date, category)

    data_source = ""
    if "data_source" in summary_df.columns:
        source_vals = summary_df["data_source"].dropna().unique()
        data_source = source_vals[0] if len(source_vals) > 0 else ""

    payload = {
        "success": True,
        "date": date_str,
        "board_category": category,
        "maps": board_maps_available(),
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
    }
    _save_board_history_cache(date_str, category, board_name, page, page_size, payload)
    out = dict(payload)
    out["from_cache"] = False
    return jsonify(out)


def _board_stocks_cache_key_hex(date_str: str, category: str, board_name: str) -> str:
    raw = json.dumps(
        [BOARD_STOCKS_CACHE_VERSION, date_str, category, board_name],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _board_stocks_source_mtime(date_str: str) -> float:
    mt = 0.0
    auction_csv = AUCTION_DIR / f"auction_{date_str}.csv"
    if auction_csv.exists():
        mt = max(mt, auction_csv.stat().st_mtime)
    ma5p = DATA_DIR / "ma5_cache" / f"{date_str}.json"
    if ma5p.exists():
        mt = max(mt, ma5p.stat().st_mtime)
    return mt


def _try_load_board_stocks_cache(date_str: str, category: str, board_name: str):
    path = BOARD_STOCKS_CACHE_DIR / f"{_board_stocks_cache_key_hex(date_str, category, board_name)}.json"
    if not path.is_file():
        return None
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if blob.get("version") != BOARD_STOCKS_CACHE_VERSION:
        return None
    if blob.get("date_str") != date_str or blob.get("category") != category:
        return None
    if blob.get("board_name") != board_name:
        return None
    if blob.get("source_mtime") != _board_stocks_source_mtime(date_str):
        return None
    payload = blob.get("payload")
    if not isinstance(payload, dict):
        return None
    return blob


def _save_board_stocks_cache(date_str: str, category: str, board_name: str, payload: dict) -> None:
    BOARD_STOCKS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = BOARD_STOCKS_CACHE_DIR / f"{_board_stocks_cache_key_hex(date_str, category, board_name)}.json"
    blob = {
        "version": BOARD_STOCKS_CACHE_VERSION,
        "date_str": date_str,
        "category": category,
        "board_name": board_name,
        "source_mtime": _board_stocks_source_mtime(date_str),
        "cached_at": datetime.now().isoformat(timespec="seconds"),
        "payload": payload,
    }
    path.write_text(json.dumps(blob, ensure_ascii=False, indent=2), encoding="utf-8")


def _resolve_board_stocks_ma5_fill() -> int:
    """ma5_fill 查询参数；默认仅读缓存（0），不阻塞列表。"""
    from config import MA5_BOARD_STOCKS_DEFAULT_FILL, MA5_BOARD_STOCKS_MAX_FILL

    raw = request.args.get("ma5_fill")
    if raw is None or str(raw).strip() == "":
        return int(MA5_BOARD_STOCKS_DEFAULT_FILL or 0)
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return int(MA5_BOARD_STOCKS_DEFAULT_FILL or 0)
    if n < 0:
        return 0
    cap = MA5_BOARD_STOCKS_MAX_FILL
    if cap is not None:
        try:
            return min(n, int(cap))
        except (TypeError, ValueError):
            pass
    return n


def _build_board_stock_rows(
    board_stocks,
    date_str: str,
    ma5_fill: int,
    name_map: dict,
    stock_concept_idx: dict,
) -> list:
    import pandas as pd

    from ma5_daily import get_ma5_map_progressive

    codes_for_ma5 = board_stocks["ts_code"].astype(str).unique().tolist()
    ma5_map = get_ma5_map_progressive(codes_for_ma5, date_str, max_fill=ma5_fill)

    code_set = set(codes_for_ma5)
    prev_date = get_previous_trade_date(date_str)
    prev_stock_data = {}
    if prev_date:
        prev_auction_csv = AUCTION_DIR / f"auction_{prev_date}.csv"
        if prev_auction_csv.exists():
            try:
                prev_df = pd.read_csv(prev_auction_csv, encoding="utf-8-sig")
                prev_df = prev_df[prev_df["ts_code"].astype(str).isin(code_set)]
                for _, row in prev_df.iterrows():
                    ts = str(row["ts_code"])
                    prev_stock_data[ts] = {
                        "amount": _safe_float(row["amount"]),
                        "price": _safe_float(row["price"]),
                        "pre_close": _safe_float(row["pre_close"]),
                    }
            except Exception:
                prev_stock_data = {}

    all_rows = []
    for _, row in board_stocks.iterrows():
        ts_code = str(row["ts_code"])
        amount = _safe_float(row["amount"])
        price = _safe_float(row["price"])
        pre_close = _safe_float(row["pre_close"])
        vol = _safe_float(row.get("vol", 0))

        if pre_close != 0:
            change_pct = (price - pre_close) / pre_close * 100
            change_price = price - pre_close
        else:
            change_pct = 0
            change_price = 0

        prev_info = prev_stock_data.get(ts_code)
        if prev_info:
            amount_change = amount - prev_info["amount"]
            amount_change_display = format_amount(abs(amount_change))
            if amount_change > 0:
                amount_change_display = f"+{amount_change_display}"
            elif amount_change < 0:
                amount_change_display = f"-{amount_change_display}"
            else:
                amount_change_display = "0"
            prev_pre_close = prev_info["pre_close"]
            prev_price = prev_info["price"]
            if prev_pre_close != 0:
                prev_change_pct = (prev_price - prev_pre_close) / prev_pre_close * 100
            else:
                prev_change_pct = 0
        else:
            amount_change_display = "--"
            prev_change_pct = None

        concept_names = stock_concept_idx.get(ts_code, [])
        mv = ma5_map.get(ts_code) if ma5_map else None
        try:
            ma5_f = float(mv) if mv is not None else None
        except (TypeError, ValueError):
            ma5_f = None
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
            "latest_price_display": f"{price:.2f}" if price is not None else "--",
            "ma5": ma5_f,
            "ma5_display": f"{ma5_f:.2f}" if ma5_f is not None else "--",
            "concept_names": concept_names,
            "concept_count": len(concept_names),
            "concept_preview": format_concept_preview(concept_names),
        })

    all_rows.sort(key=lambda x: x["change_pct"], reverse=True)
    return all_rows


@app.route("/api/board_stocks/<date_str>/<board_name>")
def api_board_stocks(date_str, board_name):
    """获取指定板块的个股详情（默认不拉网络五日线，优先本地缓存秒开）"""
    try:
        page = int(request.args.get("page", 1))
        page_size = int(request.args.get("page_size", 0))  # 默认0=返回全部
        refresh = str(request.args.get("refresh", "")).strip().lower() in ("1", "true", "yes")
        ma5_fill = _resolve_board_stocks_ma5_fill()

        category = _normalize_board_category_arg(request.args.get("category"))
        board_map = load_board_map_for_category(category)
        if not board_map or board_name not in board_map:
            return jsonify({"success": False, "message": f"未找到板块: {board_name}"})

        stock_codes = board_map.get(board_name, [])
        if not stock_codes:
            return jsonify({"success": False, "message": f"板块 {board_name} 无成分股"})

        if not refresh:
            hit = _try_load_board_stocks_cache(date_str, category, board_name)
            if hit is not None:
                if ma5_fill == 0:
                    out = dict(hit["payload"])
                    out["from_cache"] = True
                    out["cached_at"] = hit.get("cached_at")
                    out["ma5_fill"] = 0
                    return jsonify(out)
                if ma5_fill > 0:
                    from ma5_daily import get_ma5_map_progressive

                    payload = dict(hit["payload"])
                    rows = payload.get("data") or []
                    codes = [str(r.get("ts_code", "")) for r in rows if r.get("ts_code")]
                    ma5_map = get_ma5_map_progressive(codes, date_str, max_fill=ma5_fill)
                    for r in rows:
                        ts = str(r.get("ts_code", ""))
                        mv = ma5_map.get(ts) if ma5_map else None
                        try:
                            ma5_f = float(mv) if mv is not None else None
                        except (TypeError, ValueError):
                            ma5_f = None
                        r["ma5"] = ma5_f
                        r["ma5_display"] = f"{ma5_f:.2f}" if ma5_f is not None else "--"
                    payload["data"] = rows
                    payload["ma5_fill"] = ma5_fill
                    _save_board_stocks_cache(date_str, category, board_name, payload)
                    out = dict(payload)
                    out["from_cache"] = True
                    out["cached_at"] = hit.get("cached_at")
                    out["ma5_partial"] = True
                    return jsonify(out)

        auction_csv = AUCTION_DIR / f"auction_{date_str}.csv"
        if not auction_csv.exists():
            return jsonify({"success": False, "message": f"未找到 {date_str} 的竞价数据"})

        import pandas as pd

        auction_df = pd.read_csv(auction_csv, encoding="utf-8-sig")
        code_set = {str(c) for c in stock_codes}
        board_stocks = auction_df[auction_df["ts_code"].astype(str).isin(code_set)]

        if board_stocks.empty:
            return jsonify({"success": False, "message": "无个股数据"})

        name_map = get_stock_name_map()
        idx_map = load_concept_board_map() or board_map
        stock_concept_idx = build_stock_concept_index_for_codes(idx_map, code_set)

        all_rows = _build_board_stock_rows(
            board_stocks, date_str, ma5_fill, name_map, stock_concept_idx
        )
        prev_date = get_previous_trade_date(date_str)

        total = len(all_rows)
        if page_size > 0:
            start = (page - 1) * page_size
            end = start + page_size
            paged_rows = all_rows[start:end]
        else:
            paged_rows = all_rows

        payload = {
            "success": True,
            "date": date_str,
            "board_name": board_name,
            "board_category": category,
            "prev_date": prev_date,
            "total_count": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size if page_size > 0 else 1,
            "data": paged_rows,
            "ma5_fill": ma5_fill,
        }
        _save_board_stocks_cache(date_str, category, board_name, payload)

        out = dict(payload)
        out["from_cache"] = False
        return jsonify(out)

    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/stock_concepts_detail/<path:ts_code>")
def api_stock_concepts_detail(ts_code):
    """单只股票：所属全部概念/题材 + 各题材同花顺简介（按需加载，带概念级缓存）"""
    try:
        board_map = load_concept_board_map() or load_board_map()
        if not board_map:
            return jsonify({"success": False, "message": "板块映射为空"})

        idx = build_stock_concept_index(board_map)
        names = idx.get(ts_code, [])
        stock_name = get_stock_name(ts_code)

        if not names:
            return jsonify({
                "success": True,
                "ts_code": ts_code,
                "name": stock_name,
                "concept_total": 0,
                "truncated": False,
                "sections": [],
            })

        max_sections = 40
        capped = names[:max_sections]
        sections = []
        for cn in capped:
            intro_full, _ = get_board_concept_intro(cn)
            intro_full = (intro_full or "").strip()
            sections.append({
                "concept_name": cn,
                "intro": intro_full if intro_full else None,
                "has_intro": bool(intro_full),
            })

        return jsonify({
            "success": True,
            "ts_code": ts_code,
            "name": stock_name,
            "concept_total": len(names),
            "truncated": len(names) > max_sections,
            "sections": sections,
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/boards")
def api_boards():
    """获取所有板块名称列表（可选 category=concept|industry）"""
    cat = request.args.get("category")
    if cat:
        board_map = load_board_map_for_category(_normalize_board_category_arg(cat))
    else:
        board_map = load_board_map()
    if not board_map:
        return jsonify({"success": False, "message": "板块映射为空"})
    return jsonify({
        "success": True,
        "boards": sorted(board_map.keys()),
    })


@app.route("/api/board_category_meta")
def api_board_category_meta():
    """概念/行业映射是否可用及默认分类（供前端子 Tab）"""
    return jsonify({
        "success": True,
        "maps": board_maps_available(),
        "default_category": _default_board_category(),
    })


@app.route("/api/history_dates")
def api_history_dates():
    """获取所有有数据的历史日期列表（YYYYMMDD，去重）"""
    dates_set = set()
    if SUMMARY_DIR.exists():
        for f in SUMMARY_DIR.glob("board_auction_*.csv"):
            rest = f.stem.replace("board_auction_", "", 1)
            if rest.endswith("_concept"):
                dates_set.add(rest[: -len("_concept")])
            elif rest.endswith("_industry"):
                dates_set.add(rest[: -len("_industry")])
            elif len(rest) == 8 and rest.isdigit():
                dates_set.add(rest)
    dates = sorted(dates_set, reverse=True)
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


def _board_row_amount_from_summary(date_str: str, board_name: str, category: str):
    """从某日板块汇总 CSV 读取指定板块的竞价成交额(元)；无文件或无行返回 None"""
    p = _summary_csv_path_for_category(date_str, category)
    if not p:
        return None
    df = load_summary_csv(p)
    if df is None or df.empty or "板块名称" not in df.columns or "成交额(元)" not in df.columns:
        return None
    m = df[df["板块名称"].astype(str) == str(board_name)]
    if m.empty:
        return None
    return _safe_float(m.iloc[0]["成交额(元)"])


def _format_signed_amount_diff(diff: float) -> str:
    """日环比差额展示（与板块列表「较前日」风格一致）"""
    import math
    if diff is None or (isinstance(diff, float) and (math.isnan(diff) or math.isinf(diff))):
        return "--"
    if diff == 0:
        return "0"
    disp = format_amount(abs(diff))
    if diff > 0:
        return f"+{disp}"
    return f"-{disp}"


def _board_trend_cache_file_stem(anchor_date: str, board_name: str, category: str) -> str:
    raw = f"{BOARD_TREND_CACHE_VERSION}|{anchor_date}|{category}|{board_name}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _board_trend_cache_path(anchor_date: str, board_name: str, category: str) -> Path:
    return BOARD_TREND_CACHE_DIR / f"{_board_trend_cache_file_stem(anchor_date, board_name, category)}.json"


def _try_load_board_trend_cache(anchor_date: str, board_name: str, category: str):
    """命中缓存则返回 dict（含 points 等），否则 None"""
    path = _board_trend_cache_path(anchor_date, board_name, category)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if data.get("version") != BOARD_TREND_CACHE_VERSION:
        return None
    if (
        data.get("anchor_date") != anchor_date
        or data.get("board_name") != board_name
        or data.get("board_category") != category
    ):
        return None
    if not isinstance(data.get("points"), list):
        return None
    return data


def _save_board_trend_cache(anchor_date: str, board_name: str, category: str, points: list) -> None:
    BOARD_TREND_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _board_trend_cache_path(anchor_date, board_name, category)
    payload = {
        "version": BOARD_TREND_CACHE_VERSION,
        "anchor_date": anchor_date,
        "board_name": board_name,
        "board_category": category,
        "points": points,
        "cached_at": datetime.now().isoformat(timespec="seconds"),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


@app.route("/api/board_auction_trend/<date_str>/<path:board_name>")
def api_board_auction_trend(date_str, board_name):
    """某板块：以 date_str 为截止日，近 7 个交易日的竞价成交额序列（本地汇总 CSV）"""
    try:
        category = _normalize_board_category_arg(request.args.get("category"))
        refresh = str(request.args.get("refresh", "")).strip().lower() in ("1", "true", "yes")

        board_map = load_board_map_for_category(category)
        if not board_map:
            return jsonify({"success": False, "message": "板块映射为空"})
        if board_name not in board_map:
            return jsonify({"success": False, "message": f"当前分类下未找到板块: {board_name}"})

        if not refresh:
            cached = _try_load_board_trend_cache(date_str, board_name, category)
            if cached is not None:
                return jsonify({
                    "success": True,
                    "board_name": board_name,
                    "anchor_date": date_str,
                    "board_category": category,
                    "points": cached["points"],
                    "from_cache": True,
                    "cached_at": cached.get("cached_at"),
                })

        days = []
        d = date_str
        for _ in range(7):
            if not d:
                break
            days.append(d)
            d = get_previous_trade_day(d)
        days.reverse()

        if not days:
            return jsonify({"success": False, "message": "无法解析交易日历"})

        points = []
        for td in days:
            amt = _board_row_amount_from_summary(td, board_name, category)
            prev_td = get_previous_trade_day(td)
            prev_amt = (
                _board_row_amount_from_summary(prev_td, board_name, category)
                if prev_td
                else None
            )
            vs = None
            if amt is not None and prev_amt is not None:
                vs = amt - prev_amt
            points.append({
                "date": td,
                "amount": amt,
                "amount_display": format_amount(amt) if amt is not None else "--",
                "prev_trade_date": prev_td,
                "vs_prev": vs,
                "vs_prev_display": _format_signed_amount_diff(vs) if vs is not None else "--",
            })

        _save_board_trend_cache(date_str, board_name, category, points)

        return jsonify({
            "success": True,
            "board_name": board_name,
            "anchor_date": date_str,
            "board_category": category,
            "points": points,
            "from_cache": False,
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


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
    """加载关注列表，返回 list[{"name","category"}]；旧版字符串列表会自动迁移"""
    if not WATCHLIST_FILE.exists():
        return []
    try:
        data = json.loads(WATCHLIST_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, list) or not data:
        return []
    if isinstance(data[0], dict):
        out = []
        for item in data:
            nm = str(item.get("name", "")).strip()
            if not nm:
                continue
            cat = _normalize_board_category_arg(item.get("category"))
            out.append({"name": nm, "category": cat})
        return out
    migrated = []
    for n in data:
        nm = str(n).strip()
        if not nm:
            continue
        migrated.append({"name": nm, "category": _infer_board_category(nm)})
    _save_watchlist(migrated)
    return migrated


def _save_watchlist(watchlist):
    """保存关注列表（统一为 {name, category}）"""
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
    """添加板块到关注列表；可选 category=concept|industry（默认按当前映射推断）"""
    data = request.get_json(silent=True) or {}
    board_name = data.get("board_name", "").strip()
    category = _normalize_board_category_arg(data.get("category"))

    if not board_name:
        return jsonify({"success": False, "message": "板块名称不能为空"})

    watchlist = _load_watchlist()
    if any(x["name"] == board_name for x in watchlist):
        return jsonify({"success": False, "message": f"「{board_name}」已在关注列表中"})

    board_map = load_board_map_for_category(category)
    if not board_map or board_name not in board_map:
        similar = [b for b in board_map if board_name in b] if board_map else []
        if similar:
            return jsonify({
                "success": False,
                "message": f"未找到板块「{board_name}」，您是否要添加：{', '.join(similar[:5])}",
            })
        return jsonify({"success": False, "message": f"未找到板块「{board_name}」"})

    watchlist.append({"name": board_name, "category": category})
    _save_watchlist(watchlist)
    return jsonify({"success": True, "message": f"已添加「{board_name}」", "watchlist": watchlist})


@app.route("/api/watchlist", methods=["DELETE"])
def api_remove_watchlist():
    """从关注列表移除板块（按名称）"""
    data = request.get_json(silent=True) or {}
    board_name = data.get("board_name", "").strip()

    if not board_name:
        return jsonify({"success": False, "message": "板块名称不能为空"})

    watchlist = _load_watchlist()
    new_wl = [x for x in watchlist if x["name"] != board_name]
    if len(new_wl) == len(watchlist):
        return jsonify({"success": False, "message": f"「{board_name}」不在关注列表中"})

    _save_watchlist(new_wl)
    return jsonify({"success": True, "message": f"已移除「{board_name}」", "watchlist": new_wl})


def _watchlist_stable_hash(watchlist: list) -> str:
    """关注列表规范化后做哈希，列表变更则缓存键变化"""
    norm = []
    for x in watchlist:
        if not isinstance(x, dict):
            continue
        nm = str(x.get("name", "")).strip()
        if not nm:
            continue
        cat = _normalize_board_category_arg(x.get("category"))
        norm.append([nm, cat])
    norm.sort(key=lambda t: (t[0], t[1]))
    raw = json.dumps(norm, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _watchlist_data_cache_path(date_str: str, wl_hash: str) -> Path:
    return WATCHLIST_DATA_CACHE_DIR / f"{WATCHLIST_DATA_CACHE_VERSION}_{date_str}_{wl_hash}.json"


def _try_load_watchlist_data_cache(date_str: str, wl_hash: str):
    path = _watchlist_data_cache_path(date_str, wl_hash)
    if not path.is_file():
        return None
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if blob.get("version") != WATCHLIST_DATA_CACHE_VERSION:
        return None
    if blob.get("date") != date_str or blob.get("watchlist_hash") != wl_hash:
        return None
    payload = blob.get("payload")
    if not isinstance(payload, dict):
        return None
    return blob


def _save_watchlist_data_cache(date_str: str, wl_hash: str, payload: dict) -> None:
    WATCHLIST_DATA_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _watchlist_data_cache_path(date_str, wl_hash)
    blob = {
        "version": WATCHLIST_DATA_CACHE_VERSION,
        "date": date_str,
        "watchlist_hash": wl_hash,
        "cached_at": datetime.now().isoformat(timespec="seconds"),
        "payload": payload,
    }
    path.write_text(json.dumps(blob, ensure_ascii=False, indent=2), encoding="utf-8")


def _watchlist_group_by_category(watchlist: list) -> dict:
    """{category: [板块名称, ...]}"""
    by_cat: dict = {}
    for entry in watchlist:
        if not isinstance(entry, dict):
            continue
        nm = str(entry.get("name", "")).strip()
        if not nm:
            continue
        cat = _normalize_board_category_arg(entry.get("category"))
        by_cat.setdefault(cat, []).append(nm)
    return by_cat


def _watchlist_rows_from_summary(date_str: str, watchlist: list) -> list:
    """从本地板块汇总 CSV 提取关注行（不拉全市场 MA5、不遍历全部板块）。"""
    prev_date = get_previous_trade_date(date_str)
    rows_out = []
    for cat, names in _watchlist_group_by_category(watchlist).items():
        sp = _summary_csv_path_for_category(date_str, cat)
        if not sp:
            continue
        summary_df = load_summary_csv(sp)
        if summary_df is None or summary_df.empty or "板块名称" not in summary_df.columns:
            continue
        name_set = {str(n) for n in names}
        matched = summary_df[summary_df["板块名称"].astype(str).isin(name_set)]
        if matched.empty:
            continue
        for r in build_board_rows(matched, prev_date, cat):
            r["board_category"] = cat
            rows_out.append(r)
    rows_out.sort(key=lambda x: x["amount"], reverse=True)
    return rows_out


def _watchlist_rows_from_auction(date_str: str, entries: list) -> list:
    """无汇总 CSV 时：仅对指定关注板块做竞价聚合；MA5 仅拉关注成分股。"""
    import pandas as pd

    if not entries:
        return []

    auction_csv = AUCTION_DIR / f"auction_{date_str}.csv"
    if not auction_csv.exists():
        return []

    try:
        auction_df = pd.read_csv(auction_csv, encoding="utf-8-sig")
    except Exception:
        return []
    if auction_df.empty:
        return []

    prev_date = get_previous_trade_date(date_str)
    by_cat: dict = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        nm = str(entry.get("name", "")).strip()
        if not nm:
            continue
        cat = _normalize_board_category_arg(entry.get("category"))
        by_cat.setdefault(cat, []).append(nm)

    all_codes: set = set()
    board_maps: dict = {}
    for cat, names in by_cat.items():
        bm = load_board_map_for_category(cat)
        if not bm:
            continue
        board_maps[cat] = bm
        for nm in names:
            all_codes.update(str(c) for c in bm.get(nm, []) if c)

    ma5_map = None
    if all_codes:
        try:
            from config import MA5_BOARD_STOCKS_MAX_FILL
            from ma5_daily import get_ma5_map_progressive

            cap = len(all_codes)
            if MA5_BOARD_STOCKS_MAX_FILL is not None:
                cap = min(cap, int(MA5_BOARD_STOCKS_MAX_FILL))
            ma5_map = get_ma5_map_progressive(list(all_codes), date_str, max_fill=cap)
        except Exception:
            ma5_map = None

    rows_out = []
    for cat, names in by_cat.items():
        bm = board_maps.get(cat)
        if not bm:
            continue
        subset = {n: bm[n] for n in names if n in bm}
        if not subset:
            continue
        try:
            summary_df = aggregate_board_amount(
                auction_df, subset, FOCUS_BOARDS, ma5_map=ma5_map
            )
        except Exception:
            continue
        if summary_df is None or summary_df.empty:
            continue
        for r in build_board_rows(summary_df, prev_date, cat):
            r["board_category"] = cat
            rows_out.append(r)

    rows_out.sort(key=lambda x: x["amount"], reverse=True)
    return rows_out


@app.route("/api/watchlist/data")
def api_watchlist_data():
    """获取关注板块在指定日期的竞价数据（优先读汇总 CSV，毫秒级）"""
    date_str = request.args.get("date", "")
    if not date_str:
        return jsonify({"success": False, "message": "请指定日期"})

    refresh = str(request.args.get("refresh", "")).strip().lower() in ("1", "true", "yes")

    watchlist = _load_watchlist()
    if not watchlist:
        return jsonify({"success": True, "data": [], "watchlist": []})

    wl_hash = _watchlist_stable_hash(watchlist)

    if not refresh:
        cached = _try_load_watchlist_data_cache(date_str, wl_hash)
        if cached is not None:
            out = dict(cached["payload"])
            out["from_cache"] = True
            out["cached_at"] = cached.get("cached_at")
            return jsonify(out)

    has_any = bool(_summary_csv_path_for_category(date_str, "concept")) or bool(
        _summary_csv_path_for_category(date_str, "industry")
    )
    if not has_any and not (SUMMARY_DIR / f"board_auction_{date_str}.csv").exists():
        auction_csv = AUCTION_DIR / f"auction_{date_str}.csv"
        if not auction_csv.exists():
            return jsonify({"success": False, "message": f"未找到 {date_str} 的数据"})

    prev_date = get_previous_trade_date(date_str)
    rows_out = _watchlist_rows_from_summary(date_str, watchlist)

    found_names = {r["name"] for r in rows_out}
    missing_entries = [
        e for e in watchlist
        if isinstance(e, dict) and str(e.get("name", "")).strip() not in found_names
    ]
    if missing_entries:
        rows_out.extend(_watchlist_rows_from_auction(date_str, missing_entries))
        rows_out.sort(key=lambda x: x["amount"], reverse=True)

    payload = {
        "success": True,
        "date": date_str,
        "prev_date": prev_date,
        "watchlist": watchlist,
        "data": rows_out,
    }
    _save_watchlist_data_cache(date_str, wl_hash, payload)

    out = dict(payload)
    out["from_cache"] = False
    return jsonify(out)


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
        from hk_sector import (
            get_hk_sector_data,
            load_hk_sector_data,
            format_hk_amount_change_display,
            get_hk_fetch_last_error,
        )
        force = request.args.get("refresh", "0") == "1"
        data = get_hk_sector_data(force_refresh=force)
        if not data:
            data = load_hk_sector_data()
            cached = True
        else:
            cached = False
        if not data:
            err = get_hk_fetch_last_error()
            return jsonify({
                "success": False,
                "message": err or "数据获取失败（无本地缓存）",
            })
        sectors = data.get("sectors") or []
        for s in sectors:
            amt = _safe_float(s.get("amount"))
            prev_amt = _safe_float(s.get("prev_amount", 0))
            chg = _safe_float(s.get("amount_change", amt - prev_amt))
            s["prev_amount"] = prev_amt
            s["amount_change"] = chg
            s["amount_display"] = format_amount(amt) if amt else "-"
            s["prev_amount_display"] = format_amount(prev_amt) if prev_amt else "-"
            if data.get("prev_date"):
                s["amount_change_display"] = format_hk_amount_change_display(chg)
            else:
                s["amount_change_display"] = "--"
        payload = {"success": True, **data}
        if cached:
            payload["from_cache"] = True
        return jsonify(payload)
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/hk_board_stocks/<path:sector_name>")
def api_hk_board_stocks(sector_name):
    """港股某行业/板块成分股（成交额、涨跌幅、较前一日成交额变化）"""
    try:
        from urllib.parse import unquote
        from hk_sector import get_constituents_for_sector, format_hk_amount_change_display

        name = unquote(sector_name)
        rows, prev_date = get_constituents_for_sector(name)
        out = []
        for r in rows:
            achg = _safe_float(r.get("amount_change"))
            if prev_date:
                chg_disp = format_hk_amount_change_display(achg)
            else:
                chg_disp = "--"
            out.append({
                "code": r["code"],
                "name": r.get("name", ""),
                "amount": _safe_float(r.get("amount")),
                "amount_display": format_amount(_safe_float(r.get("amount"))),
                "price": _safe_float(r.get("price")),
                "pre_close": _safe_float(r.get("pre_close")),
                "change_pct": r.get("change_pct"),
                "prev_amount": _safe_float(r.get("prev_amount")),
                "amount_change_display": chg_disp,
            })
        return jsonify({
            "success": True,
            "sector_name": name,
            "prev_date": prev_date,
            "total_count": len(out),
            "data": out,
        })
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
