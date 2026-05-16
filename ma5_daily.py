"""
日线收盘价 MA5（最近 5 个交易日收盘价的算术平均）。
按交易日缓存到 data/ma5_cache/{trade_date}.json；单次请求仅补全部分缺漏，避免全市场首次加载过久。
"""

from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import requests

from config import MA5_MAX_FILL_PER_REQUEST, REQUEST_INTERVAL, TUSHARE_TOKEN
from eastmoney_auction import EM_HEADERS

# 东财日 K 接口多节点（单点 push2his 易断连；AkShare 内部只打主域名）
EM_KLINE_BASES = [
    "https://push2his.eastmoney.com/api/qt/stock/kline/get",
    "https://33.push2his.eastmoney.com/api/qt/stock/kline/get",
    "https://63.push2his.eastmoney.com/api/qt/stock/kline/get",
    "https://91.push2his.eastmoney.com/api/qt/stock/kline/get",
    "https://7.push2his.eastmoney.com/api/qt/stock/kline/get",
    "http://7.push2his.eastmoney.com/api/qt/stock/kline/get",
]

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


def _fetch_ma5_one_tushare(pro, ts_code: str, end_date: str, start_date: str) -> Optional[float]:
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


def _ts_code_to_6digit(ts_code: str) -> str:
    s = str(ts_code).strip()
    if "." in s:
        s = s.split(".", 1)[0]
    return s


def _parse_em_kline_date(s: str) -> Optional[date]:
    s = str(s).strip().replace("/", "-")
    if not s:
        return None
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        try:
            return datetime.strptime(s[:10], "%Y-%m-%d").date()
        except ValueError:
            return None
    if len(s) >= 8 and s[:8].isdigit():
        try:
            return datetime.strptime(s[:8], "%Y%m%d").date()
        except ValueError:
            return None
    return None


def _ts_code_to_em_secid(ts_code: str) -> Optional[str]:
    """Tushare 代码 → 东财 secid（与 AkShare stock_zh_a_hist 一致：沪 1.xxx，深/北证常用 0.xxx）。"""
    s = str(ts_code).strip().upper()
    if "." not in s:
        return None
    code, suf = s.split(".", 1)
    if not code.isdigit():
        return None
    if suf == "SH":
        if code.startswith("6"):
            return f"1.{code}"
        return None
    if suf == "SZ":
        return f"0.{code}"
    if suf == "BJ":
        return f"0.{code}"
    return None


def _fetch_ma5_one_eastmoney(ts_code: str, end_date: str, start_date: str) -> Optional[float]:
    """直连东财日 K 多节点，取截止 end_date 的最近 5 个交易日收盘均价。"""
    secid = _ts_code_to_em_secid(ts_code)
    if not secid:
        return None
    try:
        end_dt = datetime.strptime(end_date, "%Y%m%d").date()
    except ValueError:
        return None

    params = {
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f116",
        "ut": "7eea3edcaed734bea9cbfc24409ed989",
        "klt": "101",
        "fqt": "0",
        "secid": secid,
        "beg": start_date,
        "end": end_date,
    }

    klines: List[str] = []
    for base in EM_KLINE_BASES:
        for _ in range(2):
            try:
                r = requests.get(base, params=params, headers=EM_HEADERS, timeout=12)
                r.raise_for_status()
                js = r.json()
                klines = (js.get("data") or {}).get("klines") or []
                if klines:
                    break
            except Exception:
                time.sleep(0.15)
        if klines:
            break

    if not klines:
        return None

    rows: List[Tuple[date, float]] = []
    for line in klines:
        parts = str(line).split(",")
        if len(parts) < 3:
            continue
        d = _parse_em_kline_date(parts[0])
        if d is None:
            continue
        if d > end_dt:
            continue
        try:
            close = float(parts[2])
        except (TypeError, ValueError):
            continue
        rows.append((d, close))

    if len(rows) < 5:
        return None
    rows.sort(key=lambda x: x[0])
    last5 = [c for _, c in rows[-5:]]
    return float(sum(last5) / 5.0)


def _fetch_ma5_one_akshare(ts_code: str, end_date: str, start_date: str) -> Optional[float]:
    """东财日 K（AkShare），无 Tushare daily 权限时使用。"""
    try:
        import akshare as ak
    except ImportError:
        return None
    sym = _ts_code_to_6digit(ts_code)
    if not sym:
        return None
    try:
        df = ak.stock_zh_a_hist(
            symbol=sym,
            period="daily",
            start_date=start_date,
            end_date=end_date,
            adjust="",
        )
    except Exception:
        return None
    if df is None or df.empty or len(df) < 5:
        return None
    if "收盘" not in df.columns or "日期" not in df.columns:
        return None
    df = df.sort_values("日期")
    closes = pd.to_numeric(df["收盘"], errors="coerce").tail(5)
    if closes.isna().any() or len(closes) < 5:
        return None
    return float(closes.mean())


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
            # 曾失败或未拉取：允许在后续请求中重试（避免整文件写满 null 后永不更新）
            missing.append(c)
            out[c] = None
        else:
            try:
                out[c] = float(raw)
            except (TypeError, ValueError):
                missing.append(c)
                out[c] = None

    if not missing or max_fill <= 0:
        return out

    end = datetime.strptime(trade_date, "%Y%m%d")
    start_date = (end - timedelta(days=120)).strftime("%Y%m%d")

    pro = None
    if TUSHARE_TOKEN:
        try:
            import tushare as ts

            pro_try = ts.pro_api(TUSHARE_TOKEN)
            pro_try.daily(ts_code="000001.SZ", start_date=start_date, end_date=trade_date)
            pro = pro_try
        except Exception:
            pro = None

    to_fetch = missing[:max_fill]
    interval = float(REQUEST_INTERVAL or 0)
    for i, c in enumerate(to_fetch):
        mv = None
        if pro is not None:
            mv = _fetch_ma5_one_tushare(pro, c, trade_date, start_date)
        if mv is None:
            mv = _fetch_ma5_one_eastmoney(c, trade_date, start_date)
        if mv is None:
            mv = _fetch_ma5_one_akshare(c, trade_date, start_date)
        cache[c] = mv
        out[c] = mv
        if interval > 0 and i + 1 < len(to_fetch):
            time.sleep(interval)

    _save_cache(trade_date, cache)

    return out
