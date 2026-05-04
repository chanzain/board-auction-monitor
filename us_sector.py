"""
美股板块成交额数据 - 使用 AKShare 获取美股全量数据，按11个GICS板块聚合
缓存策略：首次拉取全量数据（慢），之后直接读缓存（秒开）
说明：美股无集合竞价，用 最新价×成交量 近似板块成交额
"""
import json
import time
import re
from pathlib import Path
from datetime import datetime, timedelta

DATA_DIR = Path(__file__).parent / "data"
OUTPUT_FILE = DATA_DIR / "us_sector_data.json"
CACHE_FILE = DATA_DIR / "us_stock_full_cache.json"
CACHE_MAX_AGE_HOURS = 4  # 缓存有效期（小时）

# 11个GICS板块 + 中概股，手动维护知名成分股
SECTOR_STOCKS = {
    "信息技术": [
        "AAPL", "MSFT", "NVDA", "AMD", "INTC", "QCOM", "AVGO", "TSLA",
        "META", "GOOGL", "NFLX", "DIS", "CMCSA", "T", "VZ", "TMUS",
        "ORCL", "CRM", "ADBE", "SNOW", "PANW", "CRWD", "ZS", "NOW",
        "ACN", "IBM", "HPQ", "DELL", "CRM", "DDOG", "GTLB", "S",
    ],
    "金融": [
        "JPM", "BAC", "WFC", "C", "GS", "MS", "AXP", "BLK",
        "SCHW", "TFC", "PNC", "COF", "USB", "STT", "BK", "RF",
        "HBAN", "CMA", "MTB", "KEY", "CFG", "FITB", "USB", "AJG",
    ],
    "能源": [
        "XOM", "CVX", "COP", "SLB", "HAL", "EOG", "PXD", "KMI",
        "ET", "WMB", "OXY", "DVN", "MPC", "VLO", "PSX", "HES",
        "OKE", "WMB", "MPLX", "TRGP",
    ],
    "医疗保健": [
        "JNJ", "UNH", "PFE", "ABBV", "MRK", "LLY", "BMY", "AMGN",
        "GILD", "ISRG", "MDT", "ABT", "TMO", "DHR", "VRTX", "REGN",
        "VTRS", "BIIB", "ALNY", "SGEN",
    ],
    "工业": [
        "BA", "CAT", "GE", "HON", "UPS", "FDX", "LMT", "RTX",
        "NOC", "GD", "EMR", "ITW", "DE", "CSX", "NSC", "UNP",
        "LUV", "DAL", "UAL", "AAL",
    ],
    "必需消费品": [
        "PG", "KO", "PEP", "WMT", "COST", "PM", "MO", "EL",
        "CL", "KMB", "GIS", "KHC", "HSY", "STZ", "TAP", "CHD",
        "MKC", "CPB", "CAG", "SJM",
    ],
    "可选消费品": [
        "AMZN", "TSLA", "HD", "LOW", "MCD", "SBUX", "NKE", "TGT",
        "BKNG", "ABNB", "GM", "F", "TM", "HMC", "RIVN", "LCID",
        "ETSY", "EBAY", "CHWY", "DPZ",
    ],
    "通信服务": [
        "GOOGL", "META", "NFLX", "DIS", "CMCSA", "T", "VZ", "TMUS",
        "CHTR", "DISH", "FOX", "NWSA", "WBD", "PARAMOUNT", "LYV", "SPOT",
    ],
    "公用事业": [
        "NEE", "DUK", "SO", "D", "AEP", "EXC", "SRE", "XEL",
        "WEC", "ES", "EIX", "AWK", "CNP", "PNW", "NRG", "ETR",
        "FE", "AEE", "DTE", "CMS",
    ],
    "原材料": [
        "LIN", "APD", "SHW", "ECL", "DD", "PPG", "IFF", "LYB",
        "EMN", "CE", "OLN", "CF", "MOS", "FMC", "APD", "SHW",
        "NEM", "GOLD", "AEM", "FNV",
    ],
    "房地产": [
        "PLD", "AMT", "EQIX", "CCI", "SPG", "O", "WELL", "VTR",
        "AVB", "EQR", "ESS", "UDR", "MAA", "HST", "RCL", "PK",
        "BXP", "CWK", "SLG", "HIW",
    ],
    "中国概念股": [
        "BABA", "JD", "BIDU", "PDD", "NTES", "BILI", "NIO", "XPEV",
        "LI", "TIGR", "FUTU", "BEKE", "YMM", "IQ", "DOYU", "VIPS",
        "MOMO", "YY", "HUYA", "ATHM",
    ],
}


def _extract_ticker(code_str):
    """从 AKShare 代码格式（如 105.INTC）中提取纯 ticker"""
    if not code_str:
        return ""
    # 去掉 .US 或数字前缀，取最后一段
    parts = str(code_str).split(".")
    ticker = parts[-1].strip().upper()
    # 如果第一段是纯数字（如 105），ticker 是第二段
    # 如果只有一段，直接返回
    return ticker


def _is_cache_valid():
    """检查缓存是否有效（在有效期内）"""
    if not CACHE_FILE.exists():
        return False
    mtime = datetime.fromtimestamp(CACHE_FILE.stat().st_mtime)
    age = datetime.now() - mtime
    return age < timedelta(hours=CACHE_MAX_AGE_HOURS)


def _load_cache():
    """加载全量股票缓存"""
    if not CACHE_FILE.exists():
        return None
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _save_cache(data):
    """保存全量股票缓存"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    print(f"  全量缓存已保存: {CACHE_FILE}")


def fetch_all_us_stocks():
    """
    获取美股全量数据并缓存
    返回: {ticker: {name, price, volume, change_pct}, ...}
    """
    import pandas as pd

    print("正在获取美股全量数据（stock_us_spot_em），预计需要3-5分钟...")
    print("  首次运行请耐心等待，后续将直接读取缓存...")
    try:
        import akshare as ak
        df = ak.stock_us_spot_em()
    except Exception as e:
        print(f"  获取失败: {e}")
        return None

    if df is None or df.empty:
        print("  未获取到数据")
        return None

    print(f"  获取到 {len(df)} 只美股，正在处理...")

    # 映射列名
    code_col = None
    name_col = None
    price_col = None
    open_col = None
    volume_col = None
    change_col = None

    for col in df.columns:
        cl = col.lower()
        if "代码" in col or cl == "code":
            code_col = col
        elif "名称" in col or "name" == cl:
            name_col = col
        elif "最新价" in col or "price" == cl:
            price_col = col
        elif "开盘价" in col or "open" == cl:
            open_col = col
        elif "成交量" in col or "volume" == cl:
            volume_col = col
        elif "涨跌幅" in col or "change" in cl:
            change_col = col

    result = {}
    for _, row in df.iterrows():
        code = str(row[code_col]) if code_col and pd.notna(row[code_col]) else ""
        ticker = _extract_ticker(code)

        if not ticker:
            continue

        # 价格：优先最新价，其次开盘价
        price = 0.0
        for col in [price_col, open_col]:
            if col and pd.notna(row[col]):
                try:
                    price = float(row[col])
                    if price > 0:
                        break
                except Exception:
                    pass

        # 成交量
        volume = 0.0
        if volume_col and pd.notna(row[volume_col]):
            try:
                volume = float(row[volume_col])
            except Exception:
                pass

        # 涨跌幅
        change_pct = 0.0
        if change_col and pd.notna(row[change_col]):
            try:
                change_pct = float(row[change_col])
            except Exception:
                pass

        name = str(row[name_col]) if name_col and pd.notna(row[name_col]) else ""

        result[ticker] = {
            "code": ticker,
            "name": name,
            "price": price,
            "volume": volume,
            "amount": price * volume,
            "change_pct": change_pct,
        }

    print(f"  处理完成，共 {len(result)} 只有效股票")
    _save_cache(result)
    return result


def get_us_sector_data(force_refresh=False):
    """
    获取美股各板块成交额
    force_refresh: 是否强制重新拉取全量数据
    返回: {"date": "2026-05-01", "sectors": [...]}
    """
    # 1. 获取全量股票数据（优先缓存）
    stock_map = None
    if not force_refresh and _is_cache_valid():
        print("使用缓存数据（新鲜度 < 4小时）...")
        stock_map = _load_cache()

    if stock_map is None:
        stock_map = fetch_all_us_stocks()
        if stock_map is None:
            # 尝试加载过期缓存
            stock_map = _load_cache()
            if stock_map:
                print("  全量拉取失败，使用过期缓存")
            else:
                print("  数据获取失败，且无缓存")
                return None

    trade_date = datetime.now().strftime("%Y-%m-%d")

    # 2. 按板块聚合
    results = []
    for sector_name, tickers in SECTOR_STOCKS.items():
        stocks_in_sector = []
        total_amount = 0.0
        changes = []

        for t in tickers:
            if t in stock_map:
                info = stock_map[t]
                stocks_in_sector.append(info)
                total_amount += info["amount"]
                changes.append(info["change_pct"])

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


def save_us_sector_data(data):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"板块数据已保存: {OUTPUT_FILE}")


def load_us_sector_data():
    if not OUTPUT_FILE.exists():
        return None
    with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


if __name__ == "__main__":
    print("=" * 60)
    print("  美股板块成交额数据抓取")
    print("  说明：美股无集合竞价，用 最新价×成交量 近似")
    print("=" * 60)
    data = get_us_sector_data(force_refresh=True)
    if data:
        save_us_sector_data(data)
        print(f"\n数据日期: {data['date']}")
        print(f"板块数: {len(data['sectors'])}")
        print("\n各板块成交额:")
        for s in data["sectors"]:
            print(f"  {s['name']}: {s['amount']:,.0f} ({s['stock_count']}只) 涨跌: {s['avg_change']}%")
    else:
        print("数据获取失败")
