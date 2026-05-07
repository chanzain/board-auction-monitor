"""
东方财富竞价数据源 - 作为 Tushare 的补充/替代
集合竞价观察窗口（9:15-9:30，沪/深）内可轮询获取实时累积成交额；9:25 产生开盘价后至 9:30 仍为竞价展示时段。
"""
import json
import math
import time
from datetime import datetime
from pathlib import Path

import requests

DATA_DIR = Path(__file__).parent / "data"
CACHE_FILE = DATA_DIR / "eastmoney_auction_cache.json"

# 多节点轮询（与 AkShare stock_zh_a_spot_em 使用的 82.push2 等一致，单点 push2 常超时/空数据）
EM_PUSH_BASES = [
    "https://82.push2.eastmoney.com/api/qt/clist/get",
    "https://72.push2.eastmoney.com/api/qt/clist/get",
    "https://81.push2.eastmoney.com/api/qt/clist/get",
    "https://33.push2.eastmoney.com/api/qt/clist/get",
    "https://push2.eastmoney.com/api/qt/clist/get",
]

# 与 AkShare 一致的 fs（空格分隔 + 含京 A）；旧版 + 号作备用
EM_FS_PRIMARY = "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:81 s:2048"
EM_FS_FALLBACK = [
    "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
]

EM_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://quote.eastmoney.com/center/gridlist.html",
}

# 字段说明（东方财富返回字段）：
# f12=代码, f14=名称, f2=最新价(竞价期间=竞价成交价), f3=涨跌幅, f4=涨跌额
# f5=成交量(手), f6=成交额(元), f15=最高, f16=最低, f17=今开, f18=昨收
EM_FIELDS = "f12,f14,f2,f3,f4,f5,f6,f15,f16,f17,f18"


def _parse_em_numeric(val) -> float:
    """东财字段可能是数字、字符串 '-'、空"""
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return 0.0
        return f
    s = str(val).strip()
    if s in ("", "-", "--", "—"):
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def fetch_em_realtime_page(base_url: str, fs: str, page: int = 1, page_size: int = 5000) -> dict:
    """获取一页东方财富实时行情数据"""
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
        "fields": EM_FIELDS,
        "_": str(int(time.time() * 1000)),
    }
    resp = requests.get(base_url, params=params, headers=EM_HEADERS, timeout=28)
    resp.raise_for_status()
    return resp.json()


def _fetch_all_pages_for_base_fs(base_url: str, fs: str) -> list:
    all_stocks = []
    page = 1
    while True:
        data = fetch_em_realtime_page(base_url, fs, page, 5000)
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


def fetch_all_em_realtime():
    """获取全量 A 股实时行情（多节点 + 多 fs 回退）"""
    errors = []
    for base in EM_PUSH_BASES:
        for fs in [EM_FS_PRIMARY] + EM_FS_FALLBACK:
            try:
                raw = _fetch_all_pages_for_base_fs(base, fs)
                if raw:
                    print(f"[东方财富] 使用节点 {base.split('/')[2]} fs={fs[:24]}... 共 {len(raw)} 条原始行情")
                    return raw
            except Exception as e:
                errors.append(f"{base.split('/')[2]}:{type(e).__name__}")
    if errors:
        print(f"[东方财富] 全部节点失败摘要: {'; '.join(errors[:8])}")
    return []


def get_auction_data_from_em(trade_date=None):
    """
    从东方财富获取全 A 实时行情，用于集合竞价阶段成交额/价量
    9:15-9:30 内多次调用可得到随时间更新的累积成交额（f6）；f2 在竞价阶段为竞价相关成交价展示
    """
    if trade_date is None:
        trade_date = datetime.now().strftime("%Y%m%d")

    print(f"[东方财富] 开始获取 {trade_date} 竞价数据...")

    try:
        raw = fetch_all_em_realtime()
    except Exception as e:
        print(f"[东方财富] 获取数据失败: {e}")
        return None

    results = []
    for stock in raw:
        ts_code = _em_code_to_tushare(stock.get("f12", ""))
        if not ts_code:
            continue

        latest_price = _parse_em_numeric(stock.get("f2"))
        pre_close = _parse_em_numeric(stock.get("f18"))
        volume_hand = _parse_em_numeric(stock.get("f5"))
        amount = _parse_em_numeric(stock.get("f6"))
        change_pct = _parse_em_numeric(stock.get("f3"))

        # 无成交且无额的跳过（集合竞价未申报；盘后全市场快照仍会保留有成交标的）
        if volume_hand == 0 and amount == 0:
            continue

        results.append({
            "ts_code": ts_code,
            "name": stock.get("f14", ""),
            "trade_date": trade_date,
            "price": latest_price,
            "pre_close": pre_close,
            "volume": volume_hand * 100.0,
            "amount": amount,
            "change_pct": change_pct,
            "open": _parse_em_numeric(stock.get("f17")),
            "high": _parse_em_numeric(stock.get("f15")),
            "low": _parse_em_numeric(stock.get("f16")),
        })

    print(f"[东方财富] 获取到 {len(results)} 只股票的竞价数据")
    return {
        "date": trade_date,
        "stocks": results,
        "source": "eastmoney",
        "fetch_time": datetime.now().isoformat(),
    }


def _em_code_to_tushare(code):
    """将东方财富代码转为 Tushare 格式（带交易所后缀）"""
    if not code:
        return ""
    code = str(code).upper()
    # 东方财富返回的代码不带后缀，需判断市场
    # 简单规则：6开头→上海，0/3开头→深圳
    if code.startswith("6"):
        return code + ".SH"
    elif code.startswith(("0", "3", "301")):
        return code + ".SZ"
    return code


def save_em_cache(data):
    """保存东方财富竞价数据到缓存"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"[东方财富] 数据已缓存: {CACHE_FILE}")


def load_em_cache(trade_date=None):
    """加载缓存的东方财富竞价数据"""
    if not CACHE_FILE.exists():
        return None
    with open(CACHE_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    if trade_date and data.get("date") != trade_date:
        return None
    return data


if __name__ == "__main__":
    data = get_auction_data_from_em()
    if data:
        save_em_cache(data)
        print(f"完成！共 {len(data['stocks'])} 只股票")
