"""
重新采集最近10个交易日的竞价数据 + 板块汇总
使用新的 concept_board_stock_map.json（含行业板块）
"""
import sys
sys.path.insert(0, __file__.rsplit("\\", 1)[0] if "\\" in __file__ else __file__.rsplit("/", 1)[0])

import time
import json
from pathlib import Path
from datetime import datetime

# 导入原有模块
from auction_monitor import (
    fetch_auction_data,
    aggregate_board_amount,
    save_summary,
    FOCUS_BOARDS,
)
from board_data import load_board_map

DATA_DIR = Path(__file__).parent / "data"
AUCTION_DIR = DATA_DIR / "auction"
SUMMARY_DIR = DATA_DIR / "board_summary"

# 最近10个交易日（2026年4月，除去周末）
# 注：如遇节假日需手动调整
TRADE_DATES = [
    "20260417", "20260420", "20260421", "20260422",
    "20260423", "20260424", "20260427", "20260428",
    "20260429", "20260430",
]

def main():
    AUCTION_DIR.mkdir(parents=True, exist_ok=True)
    SUMMARY_DIR.mkdir(parents=True, exist_ok=True)

    # 加载板块映射
    print("加载板块映射...")
    board_map = load_board_map()
    if not board_map:
        print("错误：板块映射为空，请先运行 build_concept_map_v2.py")
        return
    print(f"  映射加载成功，共 {len(board_map)} 个板块\n")

    for i, date_str in enumerate(TRADE_DATES, 1):
        print(f"\n{'#'*60}")
        print(f"  [{i}/{len(TRADE_DATES)}] 采集 {date_str}")
        print(f"{'#'*60}")

        # 检查是否已存在，跳过
        auction_file = AUCTION_DIR / f"auction_{date_str}.csv"
        summary_csv = SUMMARY_DIR / f"board_auction_{date_str}.csv"

        if summary_csv.exists() and auction_file.exists():
            print(f"  已存在，跳过")
            continue

        # 1. 获取竞价数据
        auction_df = fetch_auction_data(date_str)
        if auction_df.empty:
            print(f"  无竞价数据，跳过")
            time.sleep(1)
            continue

        actual_date = auction_df["trade_date"].iloc[0]

        # 2. 按板块汇总
        summary_df = aggregate_board_amount(auction_df, board_map, FOCUS_BOARDS)
        if summary_df.empty:
            print(f"  汇总失败，跳过")
            time.sleep(1)
            continue

        # 3. 保存
        save_summary(summary_df, actual_date)
        print(f"  ✓ 完成 {actual_date}")

        # 限流：Tushare 每分钟200次
        if i < len(TRADE_DATES):
            print(f"  等待3秒...")
            time.sleep(3)

    print(f"\n{'='*60}")
    print(f"  全部采集完成！")
    print(f"  竞价数据: {AUCTION_DIR}")
    print(f"  汇总数据: {SUMMARY_DIR}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
