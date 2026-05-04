"""
板块成分股数据管理（Tushare方案）
使用 Tushare stock_basic 接口获取股票行业分类
避免依赖 AKShare 的网络请求问题
"""

import tushare as ts
import pandas as pd
import json
import os
from pathlib import Path
from datetime import datetime

DATA_DIR = Path(__file__).parent / "data"

# 初始化 Tushare
from config import TUSHARE_TOKEN
pro = ts.pro_api(TUSHARE_TOKEN)


# 预定义的主流板块与行业关键词映射
BOARD_KEYWORDS = {
    "锂电池": ["电池", "锂电", "储能电池", "电解液", "正极材料", "负极材料"],
    "新能源车": ["新能源车", "汽车整车", "汽车零部件", "汽车服务"],
    "光伏": ["光伏", "太阳能", "硅片", "电池组件"],
    "储能": ["储能", "电池", "逆变器"],
    "风电": ["风电", "风力发电", "风电机组"],
    "充电桩": ["充电桩", "充电设备"],
    "半导体": ["半导体", "集成电路", "芯片"],
    "芯片": ["芯片", "集成电路", "半导体"],
    "人工智能": ["人工智能", "软件", "计算机应用"],
    "算力": ["算力", "数据中心", "服务器"],
    "ChatGPT": ["人工智能", "软件", "互联网服务"],
    "机器人": ["机器人", "自动化设备", "工业机械"],
    "计算机应用": ["计算机应用", "软件开发", "IT服务"],
    "通信设备": ["通信设备", "5G", "通信运营"],
    "消费电子": ["消费电子", "电子制造", "元器件"],
    "光模块": ["通信设备", "光电子"],
    "白酒": ["白酒", "酿酒"],
    "食品饮料": ["食品", "饮料", "酿酒"],
    "家电": ["家电", "家用电器", "智能家居"],
    "纺织服装": ["纺织", "服装", "服饰"],
    "旅游": ["旅游", "景区", "酒店"],
    "医美": ["医美", "医疗美容", "化妆品"],
    "证券": ["证券", "券商"],
    "银行": ["银行"],
    "保险": ["保险"],
    "煤炭": ["煤炭", "矿业"],
    "有色金属": ["有色金属", "稀土", "黄金"],
    "钢铁": ["钢铁", "冶金"],
    "化工": ["化工", "化学制品"],
    "石油": ["石油", "石化"],
    "汽车整车": ["汽车整车", "新能源车"],
    "汽车零部件": ["汽车零部件", "汽车服务"],
    "军工": ["军工", "国防", "航空航天"],
    "航空": ["航空", "机场", "航运"],
    "医药": ["医药", "生物制药", "化学制药"],
    "中药": ["中药", "医药"],
    "医疗器械": ["医疗器械", "医疗"],
    "创新药": ["医药", "生物制药"],
    "房地产": ["房地产", "地产开发"],
    "电力": ["电力", "发电", "热电"],
    "传媒": ["传媒", "影视", "出版"],
    "游戏": ["游戏", "互联网"],
    "教育": ["教育", "培训"],
    "物流": ["物流", "快递", "运输"],
}


def get_stock_list_with_industry() -> pd.DataFrame:
    """
    获取A股列表及行业分类（Tushare）
    返回: DataFrame[ts_code, name, industry, area]
    """
    print("正在从 Tushare 获取股票列表...")
    try:
        df = pro.stock_basic(
            exchange="",
            list_status="L",  # 上市状态：L=上市 D=退市 P=暂停上市
            fields="ts_code,name,industry,area",
        )
        print(f"  获取 {len(df)} 只股票")
        return df
    except Exception as e:
        print(f"  获取失败: {e}")
        return pd.DataFrame()


def build_board_map_from_industry(stock_df: pd.DataFrame) -> dict:
    """
    根据股票行业字段，构建板块 -> 成分股代码列表 的映射
    使用所有行业分类，自动生成板块映射
    """
    print("正在根据行业字段构建板块映射（使用所有行业）...")
    board_map = {}

    # 使用BOARD_KEYWORDS中定义的板块（如果存在）
    for board_name, keywords in BOARD_KEYWORDS.items():
        matched = stock_df[
            stock_df["industry"].astype(str).str.contains(
                "|".join(keywords), na=False, case=False
            )
        ]
        if len(matched) > 0:
            codes = matched["ts_code"].tolist()
            board_map[board_name] = codes
            print(f"  {board_name}: {len(codes)} 只股票")

    # 添加所有其他行业作为独立板块
    all_industries = stock_df["industry"].dropna().unique()
    for industry in all_industries:
        # 跳过已经处理的行业
        if industry in board_map:
            continue
        matched = stock_df[stock_df["industry"] == industry]
        if len(matched) > 0:
            codes = matched["ts_code"].tolist()
            board_map[industry] = codes
            print(f"  {industry}: {len(codes)} 只股票")

    # 加入"其他"板块（未匹配的）
    matched_codes = set()
    for codes in board_map.values():
        matched_codes.update(codes)

    all_codes = set(stock_df["ts_code"].tolist())
    other_codes = list(all_codes - matched_codes)
    if other_codes:
        board_map["其他"] = other_codes
        print(f"  其他: {len(other_codes)} 只股票")

    print(f"\n共构建 {len(board_map)} 个板块")
    return board_map


def save_board_map(board_map: dict, filepath: str = None):
    """保存板块映射到 JSON 文件"""
    if filepath is None:
        filepath = str(DATA_DIR / "board_stock_map.json")
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    # 将列表转为字符串方便存储
    save_data = {k: v for k, v in board_map.items()}
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(save_data, f, ensure_ascii=False, indent=2)
    print(f"板块映射已保存: {filepath}")


def load_board_map(filepath: str = None) -> dict:
    """加载板块映射（优先加载概念板块映射）"""
    if filepath is None:
        # 优先尝试概念板块映射
        concept_path = str(DATA_DIR / "concept_board_stock_map.json")
        default_path = str(DATA_DIR / "board_stock_map.json")
        if os.path.exists(concept_path):
            filepath = concept_path
        elif os.path.exists(default_path):
            filepath = default_path
        else:
            return {}
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def refresh_board_data():
    """
    刷新板块和成分股数据
    """
    print("=" * 50)
    print("板块成分股数据初始化（Tushare方案）")
    print("=" * 50)

    # 获取股票列表
    stock_df = get_stock_list_with_industry()
    if stock_df.empty:
        print("获取股票列表失败，请检查 Tushare Token")
        return {}

    # 构建板块映射
    board_map = build_board_map_from_industry(stock_df)

    # 保存
    save_board_map(board_map)

    print(f"\n完成！共获取 {len(board_map)} 个板块的成分股数据")
    return board_map


if __name__ == "__main__":
    refresh_board_data()
