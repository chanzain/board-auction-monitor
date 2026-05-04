"""
重建 board_summary 汇总文件（使用概念板块映射）
从 data/concept_board_stock_map.json 读取概念板块映射
"""
import sys
import json
import pandas as pd
from pathlib import Path

sys.path.insert(0, ".")
from auction_monitor import aggregate_board_amount, save_summary

DATA_DIR = Path("data")
AUCTION_DIR = DATA_DIR / "auction"

# 加载概念板块映射
concept_map_path = DATA_DIR / "concept_board_stock_map.json"
with open(concept_map_path, "r", encoding="utf-8") as f:
    board_map = json.load(f)
print(f"概念板块映射已加载，共 {len(board_map)} 个概念板块")

# FOCUS_BOARDS 暂时不用于概念板块模式（全部平等展示）
focus_boards = list(board_map.keys())

rebuilt = 0
for auc_file in sorted(AUCTION_DIR.glob("auction_*.csv"), reverse=True):
    date_str = auc_file.stem.replace("auction_", "")
    print(f"处理 {date_str}...", end=" ")
    try:
        df = pd.read_csv(auc_file, encoding="utf-8-sig")
        summary_df = aggregate_board_amount(df, board_map, focus_boards)
        if not summary_df.empty:
            save_summary(summary_df, date_str)
            rebuilt += 1
            print("OK")
        else:
            print("无数据")
    except Exception as e:
        print(f"失败: {e}")
        import traceback
        traceback.print_exc()

print(f"\n完成！共重建 {rebuilt} 个汇总文件。")
