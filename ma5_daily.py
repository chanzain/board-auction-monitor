"""
日线收盘价 MA5（最近 5 个交易日收盘价的算术平均）。
按交易日缓存到 data/ma5_cache/{trade_date}.json；单次请求仅补全部分缺漏，避免全市场首次加载过久。
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from config import MA5_MAX_FILL_PER_REQUEST, REQUEST_INTERVAL, TUSHARE_TOKEN

DATA_DIR = Path(__file__).resolve().parent / "data"
MA5_CACHE_DIR = DATA_DIR / "ma5_cache"


def _cache_path(trade_date: str) -> Path:
    return MA5_CACHE_DIR / f"{trade_date}.json"


def _load_cache(trade_date: str) -> Dict[str, Any]:
    p = _cache_path(trade_date)
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cache(trade_date: str, cache: Dict[str, Any]) -> None:
    MA5_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path(trade_date).write_text(
        json.dumps(cache, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _fetch_ma5_one(pro, ts_code: str, end_date: str, start_date: str) -> Optional[float]:
    try:
        df = pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
        if df is None or df.empty or len(df) < 5:
            return None
        df = df.sort_values("trade_date")
        closes = pd.to_numeric(df["close"], errors="coerce").tail(5)
        if closes.isna().any() or len(closes) < 5:
            return None
        return float(closes.mean())
    except Exception:
        return None


def get_ma5_map_progressive(
    ts_codes: List[str],
    trade_date: str,
    max_fill: Optional[int] = None,
) -> Dict[str, Optional[float]]:
    """
    返回 {ts_code: ma5 或 None}。
    已缓存的直接读出；缺漏的在本次最多补 max_fill 只（其余留 None，下次请求继续补）。
    max_fill 为 None 时用 config.MA5_MAX_FILL_PER_REQUEST。
    """
    if max_fill is None:
        max_fill = MA5_MAX_FILL_PER_REQUEST

    uniq = sorted({str(c).strip() for c in ts_codes if c and str(c).strip()})
    if not uniq:
        return {}

    cache = _load_cache(trade_date)
    out: Dict[str, Optional[float]] = {}
    missing: List[str] = []

    for c in uniq:
        if c not in cache:
            missing.append(c)
            out[c] = None
            continue
        raw = cache[c]
        if raw is None:
            out[c] = None
        else:
            try:
                out[c] = float(raw)
            except (TypeError, ValueError):
                out[c] = None

    if not missing or max_fill <= 0:
        return out

    import tushare as ts

    pro = ts.pro_api(TUSHARE_TOKEN)
    end = datetime.strptime(trade_date, "%Y%m%d")
    start_date = (end - timedelta(days=120)).strftime("%Y%m%d")

    to_fetch = missing[:max_fill]
    interval = float(REQUEST_INTERVAL or 0)
    for i, c in enumerate(to_fetch):
        mv = _fetch_ma5_one(pro, c, trade_date, start_date)
        cache[c] = mv
        out[c] = mv
        if interval > 0 and i + 1 < len(to_fetch):
            time.sleep(interval)

    _save_cache(trade_date, cache)

    return out
