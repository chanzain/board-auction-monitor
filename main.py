"""
命令行入口
"""

import argparse
import sys


def main():
    parser = argparse.ArgumentParser(
        description="A股板块集合竞价成交额监控工具（Tushare方案）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python main.py --init-boards          首次使用：初始化板块成分股数据
  python main.py --date today           采集今日竞价数据
  python main.py --date 20260503        采集指定日期竞价数据
  python main.py --days 5               采集最近5个交易日
  python main.py --date today --top 20  显示Top 20板块
        """,
    )

    parser.add_argument(
        "--init-boards",
        action="store_true",
        help="初始化/更新板块成分股映射数据（首次使用需运行）",
    )
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="交易日期（YYYYMMDD格式或'today'）",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="采集最近N个交易日的数据",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=10,
        help="显示Top N板块（默认10）",
    )
    parser.add_argument(
        "--source",
        type=str,
        default="auto",
        choices=["tushare", "eastmoney", "auto"],
        help="竞价数据源（tushare=仅Tushare，eastmoney=仅东方财富，auto=自动选择，默认auto）",
    )

    args = parser.parse_args()

    if args.init_boards:
        from board_data import refresh_board_data
        refresh_board_data()
        return

    if args.days:
        from auction_monitor import fetch_history_days
        fetch_history_days(args.days, source=args.source)
        return

    if args.date:
        from auction_monitor import run_analysis
        run_analysis(args.date, args.top, source=args.source)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
