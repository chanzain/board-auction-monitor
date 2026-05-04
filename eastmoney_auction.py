"""
东方财富竞价数据源 - 作为 Tushare 的补充/替代
在竞价时段（9:15-9:25）可实时获取数据
"""
import requests
import time
from datetime import datetime
from pathlib import Path
import json

DATA_DIR = Path(__file__).parent / "data"
CACHE_FILE = DATA_DIR / "eastmoney_auction_cache.json"

# 东方财富实时行情接口
EM_PUSH_URL = "https://push2.eastmoney.com/api/qt/clist/get"

# 字段说明（东方财富返回字段）：
# f12=代码, f14=名称, f2=最新价(竞价期间=竞价成交价), f3=涨跌幅, f4=涨跌额
# f5=成交量(手), f6=成交额(元), f15=最高, f16=最低, f17=今开, f18=昨收
EM_FIELDS = "f12,f14,f2,f3,f4,f5,f6,f15,f16,f17,f18"

def fetch_em_realtime_page(page=1, page_size=5000):
    """获取一页东方财富实时行情数据"""
    params = {
        "pn": str(page),
        "pz": str(page_size),
        "po": "1",
        "np": "1",
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": "2",
        "invt": "2",
        "fid": "f3",
        "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",  # A股全部
        "fields": EM_FIELDS,
        "_": str(int(time.time() * 1000)),
    }
    resp = requests.get(EM_PUSH_URL, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


def fetch_all_em_realtime():
    """获取全量A股实时行情（用于竞价数据采集）"""
    all_stocks = []
    page = 1
    while True:
        data = fetch_em_realtime_page(page, 5000)
        diff = data.get("data", {}).get("diff", [])
        if not diff:
            break
        all_stocks.extend(diff)
        total = data.get("data", {}).get("total", 0)
        if len(all_stocks) >= total:
            break
        page += 1
        time.sleep(0.2)

    return all_stocks


def get_auction_data_from_em(trade_date=None):
    """
    从东方财富获取竞价数据
    在 9:15-9:25 期间调用，f2(最新价)即为竞价成交价
    返回格式与 tushare stk_auction_moni 对齐，方便后续复用
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

        latest_price = stock.get("f2") or 0       # 最新价（竞价期间=竞价价）
        pre_close = stock.get("f18") or 0          # 昨收
        volume_hand = stock.get("f5") or 0         # 成交量（手）
        amount = stock.get("f6") or 0              # 成交额（元）
        change_pct = stock.get("f3") or 0         # 涨跌幅(%)

        # 竞价量为0的跳过（未参与竞价）
        if volume_hand == 0 and amount == 0:
            continue

        results.append({
            "ts_code": ts_code,
            "name": stock.get("f14", ""),
            "trade_date": trade_date,
            "price": latest_price,          # 竞价成交价
            "pre_close": pre_close,
            "volume": volume_hand * 100,    # 手→股
            "amount": amount,               # 成交额（元）
            "change_pct": change_pct,
            "open": stock.get("f17") or 0,
            "high": stock.get("f15") or 0,
            "low": stock.get("f16") or 0,
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
