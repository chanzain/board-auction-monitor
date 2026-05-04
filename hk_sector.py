"""
港股板块成交额数据 - 使用东方财富 API 获取港股实时数据，按行业板块聚合
缓存策略：首次拉取全量数据，之后直接读缓存（秒开）
说明：港股无严格集合竞价，用 开盘价×成交量 近似板块成交额
"""

import json
import time
import requests
from pathlib import Path
from datetime import datetime, timedelta

DATA_DIR = Path(__file__).parent / "data"
OUTPUT_FILE = DATA_DIR / "hk_sector_data.json"
CACHE_FILE = DATA_DIR / "hk_stock_cache.json"
CACHE_MAX_AGE_HOURS = 4  # 缓存有效期（小时）

# 东方财富 港股实时行情接口
EM_HK_PUSH_URL = "https://push2.eastmoney.com/api/qt/clist/get"

# 港股行业板块分类（手动维护主要成分股）
# 板块名称与 EastMoney 港股行业分类对齐
HK_SECTOR_STOCKS = {
    "科技": [
        "00700", "09988", "03690", "01024", "00285", "01810", "09618",
        "01347", "09888", "06618", "02331", "09896", "00388", "09999",
        "01833", "09626", "02269", "09868", "00992", "01797",
    ],
    "金融": [
        "01398", "02318", "00939", "01299", "02388", "03988", "03328",
        "01359", "01339", "02338", "06837", "09660", "08368", "08693",
        "00267", "01988", "01551", "02888", "02318", "00005",
    ],
    "地产": [
        "00016", "00012", "00017", "00083", "01038", "01109", "01213",
        "02007", "03333", "02202", "00874", "00960", "01468", "03900",
        "01564", "00813", "02382", "01668", "01918", "02013",
    ],
    "能源": [
        "00883", "00386", "01211", "02883", "00857", "00338", "01138",
        "02688", "00094", "01818", "06811", "09600", "01888", "09868",
    ],
    "医疗健康": [
        "01093", "02269", "06618", "09618", "01258", "01530", "01177",
        "01789", "09995", "02251", "01458", "03692", "06990", "01801",
        "01513", "02359", "01072", "00874", "09633", "09866",
    ],
    "消费": [
        "02319", "00241", "00874", "01299", "01579", "02238", "01787",
        "00992", "02545", "09987", "01458", "02382", "09668", "00151",
        "00868", "01880", "01336", "01988", "00522", "00493",
    ],
    "电信": [
        "00762", "00941", "00728", "06869", "00823", "00315", "01762",
        "01983", "01523", "01635", "02038", "00041", "00019", "00451",
    ],
    "公用事业": [
        "00002", "00003", "00006", "00080", "00257", "00836", "01071",
        "02688", "00051", "00012", "00016", "01972", "01686", "03868",
    ],
    "原材料": [
        "01313", "01211", "01787", "00669", "00874", "00386", "01088",
        "01258", "01818", "02338", "02899", "03328", "05138", "07608",
    ],
    "工业": [
        "00011", "00017", "00165", "00267", "00322", "00323", "00511",
        "00669", "00762", "00960", "01316", "01800", "02186", "02318",
    ],
}

# 港股代码前缀 -> 东方财富 market 参数
# 港股在东方财富的 fs 参数格式：m:116+t:2 (港股主板)


def fetch_hk_realtime_page(page=1, page_size=500):
    """获取一页东方财富港股实时行情数据"""
    params = {
        "pn": str(page),
        "pz": str(page_size),
        "po": "1",
        "np": "1",
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": "2",
        "invt": "2",
        "fid": "f3",
        "fs": "m:116+t:3",  # 港股（主板+创业板）
        "fields": "f12,f14,f2,f3,f4,f5,f6,f15,f16,f17,f18",
        "_": str(int(time.time() * 1000)),
    }
    resp = requests.get(EM_HK_PUSH_URL, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


def fetch_all_hk_realtime():
    """获取全量港股实时行情"""
    all_stocks = []
    page = 1
    while True:
        data = fetch_hk_realtime_page(page, 500)
        diff = data.get("data", {}).get("diff", [])
        if not diff:
            break
        all_stocks.extend(diff)
        total = data.get("data", {}).get("total", 0)
        if len(all_stocks) >= total:
            break
        page += 1
        time.sleep(0.3)

    return all_stocks


def get_hk_stock_map(force_refresh=False):
    """
    获取港股实时数据并缓存
    返回: {ts_code: {name, price, volume, amount, change_pct}, ...}
    """
    cache_file = CACHE_FILE
    output_file = OUTPUT_FILE

    # 检查缓存
    if not force_refresh and cache_file.exists():
        mtime = datetime.fromtimestamp(cache_file.stat().st_mtime)
        age = datetime.now() - mtime
        if age < timedelta(hours=CACHE_MAX_AGE_HOURS):
            print("[港股] 使用缓存数据（新鲜度 < 4小时）...")
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    cached = json.load(f)
                # 确保数值字段为 float 类型
                for code, info in cached.items():
                    for key in ("price", "pre_close", "volume", "amount", "change_pct"):
                        if key in info and not isinstance(info[key], (int, float)):
                            info[key] = float(info[key]) if info[key] else 0.0
                return cached
            except Exception:
                pass

    print("[港股] 正在从东方财富获取港股全量数据...")
    try:
        raw = fetch_all_hk_realtime()
    except Exception as e:
        print(f"[港股] 获取数据失败: {e}")
        # 尝试加载过期缓存
        if cache_file.exists():
            with open(cache_file, "r", encoding="utf-8") as f:
                return json.load(f)
        return None

    result = {}
    for stock in raw:
        code = str(stock.get("f12", "")).strip()
        if not code:
            continue

        def _to_float(val):
            """安全转float， '-' 或 None 等无效值返回 0.0"""
            if val is None or val == '-' or val == '':
                return 0.0
            try:
                return float(val)
            except (ValueError, TypeError):
                return 0.0

        price = _to_float(stock.get("f2"))       # 最新价
        pre_close = _to_float(stock.get("f18"))   # 昨收
        volume = _to_float(stock.get("f5"))       # 成交量（手）
        amount = _to_float(stock.get("f6"))       # 成交额（港元）
        change_pct = _to_float(stock.get("f3"))  # 涨跌幅(%)

        result[code] = {
            "code": code,
            "name": stock.get("f14", ""),
            "price": price,
            "pre_close": pre_close,
            "volume": volume,
            "amount": amount,
            "change_pct": change_pct,
        }

    print(f"[港股] 获取到 {len(result)} 只港股")

    # 保存缓存
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False)
    print(f"[港股] 缓存已保存: {cache_file}")

    return result


def get_hk_sector_data(force_refresh=False):
    """
    获取港股各板块成交额
    force_refresh: 是否强制重新拉取全量数据
    返回: {"date": "2026-05-04", "sectors": [...]}
    """
    stock_map = get_hk_stock_map(force_refresh=force_refresh)
    if stock_map is None:
        print("[港股] 数据获取失败")
        return None

    trade_date = datetime.now().strftime("%Y-%m-%d")

    # 按板块聚合
    results = []
    for sector_name, codes in HK_SECTOR_STOCKS.items():
        stocks_in_sector = []
        total_amount = 0.0
        changes = []

        for code in codes:
            if code in stock_map:
                info = stock_map[code]
                stocks_in_sector.append(info)
                total_amount += info["amount"]
                changes.append(info["change_pct"])

        if not stocks_in_sector:
            continue

        # 平均涨跌幅
        avg_change = round(sum(changes) / len(changes), 2) if changes else 0.0

        # 涨跌家数
        rise = sum(1 for c in changes if c > 0)
        fall = sum(1 for c in changes if c < 0)

        # 按成交额排序，取前10
        stocks_in_sector.sort(key=lambda x: x["amount"], reverse=True)

        results.append({
            "name": sector_name,
            "amount": total_amount,
            "stock_count": len(stocks_in_sector),
            "avg_change": avg_change,
            "rise": rise,
            "fall": fall,
            "top_stocks": stocks_in_sector[:10],
        })

    # 按成交额排序
    results.sort(key=lambda x: x["amount"], reverse=True)

    return {
        "date": trade_date,
        "sectors": results,
    }


def save_hk_sector_data(data):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"[港股] 板块数据已保存: {OUTPUT_FILE}")


def load_hk_sector_data():
    if not OUTPUT_FILE.exists():
        return None
    with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


if __name__ == "__main__":
    print("=" * 60)
    print("  港股板块成交额数据抓取")
    print("  说明：港股无集合竞价，用 最新价×成交量 近似")
    print("=" * 60)
    data = get_hk_sector_data(force_refresh=True)
    if data:
        save_hk_sector_data(data)
        print(f"\n数据日期: {data['date']}")
        print(f"板块数: {len(data['sectors'])}")
        print("\n各板块成交额:")
        for s in data["sectors"]:
            print(f"  {s['name']}: {s['amount']:,.0f} ({s['stock_count']}只) 涨跌: {s['avg_change']}%")
    else:
        print("数据获取失败")
