"""
重建 board_summary 汇总文件（使用新的 board_stock_map.json）
"""
import sys
import pandas as pd
from pathlib import Path

sys.path.insert(0, ".")
from auction_monitor import aggregate_board_amount, save_summary, FOCUS_BOARDS
from board_data import load_board_map

DATA_DIR = Path("data")
AUCTION_DIR = DATA_DIR / "auction"

board_map = load_board_map()
print(f"板块映射已加载，共 {len(board_map)} 个板块")

rebuilt = 0
for auc_file in sorted(AUCTION_DIR.glob("auction_*.csv"), reverse=True):
    date_str = auc_file.stem.replace("auction_", "")
    print(f"处理 {date_str}...", end=" ")
    try:
        df = pd.read_csv(auc_file, encoding="utf-8-sig")
        summary_df = aggregate_board_amount(df, board_map, FOCUS_BOARDS)
        if not summary_df.empty:
            save_summary(summary_df, date_str)
            rebuilt += 1
            print("✓")
        else:
            print("无数据")
    except Exception as e:
        print(f"失败: {e}")

print(f"\n完成！共重建 {rebuilt} 个汇总文件。")
