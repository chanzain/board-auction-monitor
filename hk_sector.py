"""
港股板块成交额：东方财富全市场港股行情，按行业/板块字段动态聚合（覆盖主板+创业板等，不再用手动 10 板块列表）。
说明：港股无 A 股式集合竞价，成交额为当日（或实时）累计成交额；与上一交易日保存的快照对比得到「昨日成交额」及增减。
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

DATA_DIR = Path(__file__).parent / "data"
OUTPUT_FILE = DATA_DIR / "hk_sector_data.json"
CACHE_FILE = DATA_DIR / "hk_stock_cache.json"
HK_HISTORY_DIR = DATA_DIR / "hk_sector_history"
CACHE_MAX_AGE_HOURS = 2

# 最近一次拉取失败原因（供 API 返回给前端）
HK_LAST_FETCH_ERROR: Optional[str] = None

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://quote.eastmoney.com/center/gridlist.html",
}

# 与 AkShare「stock_hk_spot_em」一致：全港股一条 fs（注意用空格，不是 +）
HK_FS_UNIFIED = "m:128 t:3,m:128 t:4,m:128 t:1,m:128 t:2"

# 东财 push2 多节点轮询（不同网络环境下可用节点不同）
EM_PUSH_BASES = [
    "https://72.push2.eastmoney.com/api/qt/clist/get",
    "https://81.push2.eastmoney.com/api/qt/clist/get",
    "https://33.push2.eastmoney.com/api/qt/clist/get",
    "https://22.push2.eastmoney.com/api/qt/clist/get",
    "https://42.push2.eastmoney.com/api/qt/clist/get",
    "https://92.push2.eastmoney.com/api/qt/clist/get",
    "https://push2.eastmoney.com/api/qt/clist/get",
]

# 备用 fs（+ 号写法，部分环境仍使用）
HK_FS_FALLBACK = [
    "m:128+t:3",
    "m:128+t:4",
    "m:116+t:3",
]


def get_hk_fetch_last_error() -> Optional[str]:
    return HK_LAST_FETCH_ERROR


def _to_float(val) -> float:
    if val is None or val == "-" or val == "":
        return 0.0
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


def _pick_industry_label(stock: dict) -> str:
    """东财个股所属行业/板块：优先 f100，其次 f127"""
    for key in ("f100", "f127"):
        raw = stock.get(key)
        if raw is None:
            continue
        s = str(raw).strip()
        if s and s not in ("-", "0", "—"):
            return s
    return "其他(未分类)"


def _fetch_hk_clist_page(base_url: str, fs: str, page: int, page_size: int) -> dict:
    params = {
        "pn": str(page),
        "pz": str(page_size),
        "po": "1",
        "np": "1",
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": "2",
        "invt": "2",
        "fid": "f12",
        "fs": fs,
        "fields": "f12,f14,f2,f3,f4,f5,f6,f15,f16,f17,f18,f100,f127",
        "_": str(int(time.time() * 1000)),
    }
    resp = requests.get(base_url, params=params, headers=HEADERS, timeout=28)
    resp.raise_for_status()
    return resp.json()


def _fetch_all_pages_for_base_fs(base_url: str, fs: str) -> List[dict]:
    all_stocks: List[dict] = []
    page = 1
    while True:
        data = _fetch_hk_clist_page(base_url, fs, page, 500)
        diff = data.get("data", {}).get("diff") or []
        if not diff:
            break
        all_stocks.extend(diff)
        total = int(data.get("data", {}).get("total") or 0)
        if total and len(all_stocks) >= total:
            break
        page += 1
        time.sleep(0.2)
    return all_stocks


def _fetch_via_akshare_spot_em() -> List[dict]:
    import akshare as ak

    df = ak.stock_hk_spot_em()
    out: List[dict] = []
    for _, row in df.iterrows():
        code = str(row["代码"]).strip()
        if code.isdigit():
            code = code.zfill(5)
        out.append({
            "f12": code,
            "f14": row.get("名称", "") or "",
            "f2": row.get("最新价"),
            "f3": row.get("涨跌幅"),
            "f5": row.get("成交量"),
            "f6": row.get("成交额"),
            "f18": row.get("昨收"),
            "f100": None,
            "f127": None,
        })
    return out


def _fetch_via_sina_spot() -> List[dict]:
    import akshare as ak

    df = ak.stock_hk_spot()
    out: List[dict] = []
    for _, row in df.iterrows():
        code = str(row.get("代码", "")).strip()
        if code.isdigit():
            code = code.zfill(5)
        out.append({
            "f12": code,
            "f14": row.get("中文名称", "") or "",
            "f2": row.get("最新价"),
            "f3": row.get("涨跌幅"),
            "f5": row.get("成交量"),
            "f6": row.get("成交额"),
            "f18": row.get("昨收"),
            "f100": None,
            "f127": None,
        })
    return out


def fetch_merged_hk_raw() -> List[dict]:
    """东财多节点 → 备用 fs → AkShare → 新浪"""
    errors: List[str] = []

    for base in EM_PUSH_BASES:
        try:
            raw = _fetch_all_pages_for_base_fs(base, HK_FS_UNIFIED)
            if raw:
                print(f"[港股] 东财全市场拉取成功 {base} 共 {len(raw)} 条")
                return raw
        except Exception as e:
            errors.append(f"{base.split('/')[2]}:{type(e).__name__}")

    for fs in HK_FS_FALLBACK:
        for base in EM_PUSH_BASES[:5]:
            try:
                raw = _fetch_all_pages_for_base_fs(base, fs)
                if raw:
                    print(f"[港股] 东财备用 fs={fs} {base} 共 {len(raw)} 条")
                    return raw
            except Exception as e:
                errors.append(f"{fs}@{type(e).__name__}")

    try:
        raw = _fetch_via_akshare_spot_em()
        if raw:
            print(f"[港股] 使用 AkShare stock_hk_spot_em 备用源，共 {len(raw)} 条（无行业字段，将归入未分类）")
            return raw
    except Exception as e:
        errors.append(f"akshare:{e}")

    try:
        raw = _fetch_via_sina_spot()
        if raw:
            print(f"[港股] 使用新浪港股列表备用源，共 {len(raw)} 条")
            return raw
    except Exception as e:
        errors.append(f"sina:{e}")

    detail = "; ".join(errors[:16]) if errors else "各源无返回"
    raise RuntimeError(
        "全部数据源失败或无有效港股列表。详情："
        + detail
        + "。请检查网络/代理，或稍后重试。"
    )


def raw_to_stock_map(raw: List[dict]) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for stock in raw:
        code = str(stock.get("f12", "")).strip()
        if not code:
            continue
        if code.isdigit():
            code = code.zfill(5)
        industry = _pick_industry_label(stock)
        price = _to_float(stock.get("f2"))
        pre_close = _to_float(stock.get("f18"))
        volume = _to_float(stock.get("f5"))
        amount = _to_float(stock.get("f6"))
        change_pct = _to_float(stock.get("f3"))

        result[code] = {
            "code": code,
            "name": stock.get("f14", "") or "",
            "price": price,
            "pre_close": pre_close,
            "volume": volume,
            "amount": amount,
            "change_pct": change_pct,
            "industry": industry,
        }
    return result


def get_hk_stock_map(force_refresh: bool = False) -> Optional[Dict[str, Dict[str, Any]]]:
    global HK_LAST_FETCH_ERROR
    HK_LAST_FETCH_ERROR = None

    if not force_refresh and CACHE_FILE.exists():
        mtime = datetime.fromtimestamp(CACHE_FILE.stat().st_mtime)
        if datetime.now() - mtime < timedelta(hours=CACHE_MAX_AGE_HOURS):
            try:
                with open(CACHE_FILE, "r", encoding="utf-8") as f:
                    cached = json.load(f)
                sample = next(iter(cached.values()), {})
                if "industry" not in sample:
                    print("[港股] 缓存无行业字段，将重新拉取…")
                else:
                    for _c, info in cached.items():
                        for key in ("price", "pre_close", "volume", "amount", "change_pct"):
                            if key in info and not isinstance(info[key], (int, float)):
                                info[key] = float(info[key] or 0)
                    print("[港股] 使用本地缓存（<2h）")
                    return cached
            except Exception:
                pass

    print("[港股] 正在拉取全市场港股行情（多市场合并）...")
    try:
        raw = fetch_merged_hk_raw()
    except Exception as e:
        print(f"[港股] 拉取失败: {e}")
        HK_LAST_FETCH_ERROR = str(e)
        if CACHE_FILE.exists():
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        return None

    stock_map = raw_to_stock_map(raw)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(stock_map, f, ensure_ascii=False)
    print(f"[港股] 已缓存 {len(stock_map)} 只股票: {CACHE_FILE}")
    return stock_map


def _history_sector_path(d: str) -> Path:
    return HK_HISTORY_DIR / f"sector_{d}.json"


def _history_stocks_path(d: str) -> Path:
    return HK_HISTORY_DIR / f"stocks_{d}.json"


def find_prev_hk_snapshot_date(today_str: str) -> Optional[str]:
    """在已落盘历史中找早于 today 的最近一日（近似上一港股交易日）"""
    HK_HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    start = datetime.strptime(today_str, "%Y-%m-%d")
    for i in range(1, 21):
        dt = start - timedelta(days=i)
        ds = dt.strftime("%Y-%m-%d")
        if _history_sector_path(ds).exists():
            return ds
    return None


def load_prev_sector_amount_map(prev_date: Optional[str]) -> Dict[str, float]:
    if not prev_date:
        return {}
    p = _history_sector_path(prev_date)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out: Dict[str, float] = {}
    for s in data.get("sectors", []):
        name = s.get("name")
        if name:
            out[str(name)] = float(s.get("amount") or 0)
    return out


def load_prev_stock_amount_map(prev_date: Optional[str]) -> Dict[str, float]:
    if not prev_date:
        return {}
    p = _history_stocks_path(prev_date)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    stocks = data.get("stocks", {})
    return {str(k): float(v.get("amount") or 0) for k, v in stocks.items()}


def aggregate_sectors(
    stock_map: Dict[str, Dict[str, Any]],
    prev_sector_amount: Dict[str, float],
) -> List[Dict[str, Any]]:
    buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    sector_codes: Dict[str, List[str]] = defaultdict(list)

    for code, info in stock_map.items():
        ind = info.get("industry") or "其他(未分类)"
        buckets[ind].append(info)
        sector_codes[ind].append(code)

    results: List[Dict[str, Any]] = []
    for sector_name, stocks in buckets.items():
        total_amount = sum(float(s.get("amount") or 0) for s in stocks)
        changes = [float(s.get("change_pct") or 0) for s in stocks]
        avg_change = round(sum(changes) / len(changes), 2) if changes else 0.0
        rise = sum(1 for c in changes if c > 0)
        fall = sum(1 for c in changes if c < 0)

        prev_amt = float(prev_sector_amount.get(sector_name, 0) or 0)
        amt_change = total_amount - prev_amt

        results.append({
            "name": sector_name,
            "amount": total_amount,
            "prev_amount": prev_amt,
            "amount_change": amt_change,
            "stock_count": len(stocks),
            "avg_change": avg_change,
            "rise": rise,
            "fall": fall,
            "codes": sorted(sector_codes[sector_name]),
        })

    results.sort(key=lambda x: x["amount"], reverse=True)
    return results


def save_daily_snapshots(trade_date: str, sectors: List[Dict[str, Any]], stock_map: Dict[str, Dict[str, Any]]):
    HK_HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    slim_sectors = []
    for s in sectors:
        slim_sectors.append({
            "name": s["name"],
            "amount": s["amount"],
            "stock_count": s["stock_count"],
            "avg_change": s["avg_change"],
            "rise": s["rise"],
            "fall": s["fall"],
            "codes": s.get("codes", []),
        })
    sector_payload = {"date": trade_date, "sectors": slim_sectors}
    _history_sector_path(trade_date).write_text(
        json.dumps(sector_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    stocks_payload = {
        "date": trade_date,
        "stocks": {
            code: {
                "amount": float(info.get("amount") or 0),
                "name": info.get("name", ""),
                "change_pct": float(info.get("change_pct") or 0),
            }
            for code, info in stock_map.items()
        },
    }
    _history_stocks_path(trade_date).write_text(
        json.dumps(stocks_payload, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"[港股] 已写入历史快照: {trade_date}")


def get_hk_sector_data(force_refresh: bool = False) -> Optional[Dict[str, Any]]:
    stock_map = get_hk_stock_map(force_refresh=force_refresh)
    if not stock_map:
        return None

    trade_date = datetime.now().strftime("%Y-%m-%d")
    prev_date = find_prev_hk_snapshot_date(trade_date)
    prev_sector_amt = load_prev_sector_amount_map(prev_date)

    sectors = aggregate_sectors(stock_map, prev_sector_amt)

    save_daily_snapshots(trade_date, sectors, stock_map)

    # 返回给前端：去掉 codes 过大时可保留（成分股 API 用 industry 重算）
    sectors_out = []
    for s in sectors:
        sectors_out.append({
            "name": s["name"],
            "amount": s["amount"],
            "prev_amount": s["prev_amount"],
            "amount_change": s["amount_change"],
            "stock_count": s["stock_count"],
            "avg_change": s["avg_change"],
            "rise": s["rise"],
            "fall": s["fall"],
        })

    out = {
        "date": trade_date,
        "prev_date": prev_date,
        "sectors": sectors_out,
    }
    save_hk_sector_data(out)
    return out


def save_hk_sector_data(data: Dict[str, Any]):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_hk_sector_data() -> Optional[Dict[str, Any]]:
    if not OUTPUT_FILE.exists():
        return None
    with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def get_constituents_for_sector(
    sector_name: str,
    stock_map: Optional[Dict[str, Dict[str, Any]]] = None,
    prev_stock_amount: Optional[Dict[str, float]] = None,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """某行业板块下全部成分股（用于弹窗）"""
    if stock_map is None:
        stock_map = get_hk_stock_map(force_refresh=False) or {}
    trade_date = datetime.now().strftime("%Y-%m-%d")
    prev_date = find_prev_hk_snapshot_date(trade_date)
    if prev_stock_amount is None:
        prev_stock_amount = load_prev_stock_amount_map(prev_date)

    rows: List[Dict[str, Any]] = []
    for code, info in stock_map.items():
        if (info.get("industry") or "") != sector_name:
            continue
        amt = float(info.get("amount") or 0)
        prev_amt = float(prev_stock_amount.get(code, 0) or 0)
        chg = amt - prev_amt
        prev_pct = info.get("change_pct")
        rows.append({
            "code": code,
            "name": info.get("name", ""),
            "amount": amt,
            "price": float(info.get("price") or 0),
            "pre_close": float(info.get("pre_close") or 0),
            "change_pct": round(float(info.get("change_pct") or 0), 2),
            "prev_amount": prev_amt,
            "amount_change": chg,
        })

    rows.sort(key=lambda x: x["change_pct"], reverse=True)
    return rows, prev_date


def format_hk_amount_change_display(amt_change: float) -> str:
    if amt_change == 0:
        return "0"
    a = abs(amt_change)
    if a >= 1e8:
        disp = f"{a / 1e8:.2f}亿"
    elif a >= 1e4:
        disp = f"{a / 1e4:.2f}万"
    else:
        disp = f"{a:.0f}"
    return ("+" if amt_change > 0 else "-") + disp
