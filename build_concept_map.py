"""
根据概念板块清单，自动构建 概念→成分股代码 映射
策略：
  1. 行业字段匹配（Tushare industry字段直接对应）
  2. 股票名称关键词匹配（名称包含概念相关关键词）
  3. 手动补充龙头股简称
  4. 去重：每只股票可能同时属于多个概念
"""

import tushare as ts
import json
import csv
from pathlib import Path
from config import TUSHARE_TOKEN

DATA_DIR = Path(__file__).parent / "data"
pro = ts.pro_api(TUSHARE_TOKEN)

# ========== 1. 加载全量股票列表 ==========
print("正在获取A股全量股票列表...")
stock_df = pro.stock_basic(exchange="", list_status="L", fields="ts_code,name,industry")
print(f"  共 {len(stock_df)} 只股票，{stock_df['industry'].nunique()} 个行业")

# 构建 股票代码→名称 映射
code_to_name = dict(zip(stock_df["ts_code"], stock_df["name"]))
name_to_codes = {}
for _, row in stock_df.iterrows():
    name = row["name"]
    name_to_codes.setdefault(name, []).append(row["ts_code"])

# 构建 行业→股票代码 映射
industry_to_codes = {}
for _, row in stock_df.iterrows():
    industry = row["industry"]
    if industry:
        industry_to_codes.setdefault(industry, []).append(row["ts_code"])


def match_by_name(keywords, exclude_keywords=None, extra_names=None):
    """
    按股票名称关键词匹配
    keywords: 名称中需包含的关键词列表（OR关系）
    exclude_keywords: 排除名称包含这些关键词的股票
    extra_names: 额外补充的股票简称列表
    """
    import re
    patterns = "|".join(re.escape(k) for k in keywords)
    matched = stock_df[stock_df["name"].str.contains(patterns, na=False, regex=True)]
    codes = set(matched["ts_code"].tolist())

    if exclude_keywords:
        for kw in exclude_keywords:
            exclude_df = stock_df[stock_df["name"].str.contains(re.escape(kw), na=False)]
            codes -= set(exclude_df["ts_code"].tolist())

    if extra_names:
        # 模糊匹配：extra_names 中的名称作为关键词在股票名称中搜索（包含关系）
        for name in extra_names:
            fuzzy_matches = stock_df[stock_df["name"].str.contains(re.escape(name), na=False, regex=True)]
            if not fuzzy_matches.empty:
                codes.update(fuzzy_matches["ts_code"].tolist())

    return sorted(codes)


def match_by_industry(industry_names):
    """按Tushare行业字段精确匹配"""
    codes = set()
    for ind in industry_names:
        codes.update(industry_to_codes.get(ind, []))
    return sorted(codes)


# ========== 2. 概念板块关键词规则定义 ==========
# 结构: 概念名称 → { "type": "name"|"industry"|"hybrid", ... }

CONCEPT_RULES = {}

# ----- 行业字段直接对应的板块 -----
INDUSTRY_BOARDS = {
    "半导体": ["半导体"],
    "IT设备": ["IT设备"],
    "互联网": ["互联网"],
    "元器件": ["元器件"],
    "软件服务": ["软件服务"],
    "通信设备": ["通信设备"],
    "有色金属": ["小金属", "铜", "铝", "铅锌", "黄金"],
    "银行": ["银行"],
    "证券": ["证券"],
    "保险": ["保险"],
    "房地产": ["全国地产", "区域地产", "园区开发", "房产服务"],
    "传媒": ["影视音像", "出版业", "广告包装"],
    "煤炭": ["煤炭开采"],
    "钢铁": ["普钢", "特种钢", "钢加工"],
    "水泥": ["水泥"],
    "玻璃": ["玻璃"],
    "玻纤": ["矿物制品"],
    "造纸": ["造纸"],
    "医药商业": ["医药商业"],
    "化学制药": ["化学制药"],
    "生物制药": ["生物制药"],
    "中药": ["中成药"],
    "医疗保健": ["医疗保健"],
    "家用电器": ["家用电器"],
    "乳业": ["乳制品"],
    "白酒": ["白酒"],
    "啤酒": ["啤酒"],
    "食品加工": ["食品"],
    "汽车整车": ["汽车整车"],
    "汽车零部件": ["汽车配件"],
    "汽车服务": ["汽车服务"],
    "纺织服饰": ["纺织", "服饰"],
    "环保": ["环境保护"],
    "水务": ["水务"],
    "燃气": ["供气供热"],
    "公路": ["公路"],
    "铁路": ["铁路"],
    "航空": ["航空"],
    "港口": ["港口"],
    "水运": ["水运"],
    "机场": ["机场"],
    "电力设备": ["电气设备"],
    "工程机械": ["工程机械"],
    "轨道交通": ["运输设备"],
    "农药化肥": ["农药化肥"],
    "塑料": ["塑料"],
    "橡胶": ["橡胶"],
    "化纤": ["化纤"],
    "建筑施工": ["建筑工程"],
    "装修装饰": ["装修装饰"],
    "广告营销": ["广告包装"],
    "游戏": ["影视音像"],  # Tushare没有单独游戏行业
    "百货": ["百货"],
    "商超": ["超市连锁"],
    "酒店餐饮": ["酒店餐饮"],
    "旅游酒店": ["旅游景点", "旅游服务", "酒店餐饮"],
    "影视": ["影视音像"],
    "出版业": ["出版业"],
    "文教休闲": ["文教休闲"],
    "家居用品": ["家居用品"],
    "服饰": ["服饰"],
    "化妆品": ["日用化工"],
    "电信运营": ["电信运营"],
    "多元金融": ["多元金融"],
    "多元化工": ["化工原料"],
    "饲料": ["饲料"],
    "种植业": ["种植业"],
    "林业": ["林业"],
    "水产养殖": ["渔业"],
    "农机": ["农用机械"],
    "仓储物流": ["仓储物流"],
    "基建": ["建筑工程", "路桥"],
    "家具": ["家居用品"],
    "家用电器": ["家用电器"],
}

for concept, industries in INDUSTRY_BOARDS.items():
    CONCEPT_RULES[concept] = {
        "type": "industry",
        "industries": industries,
    }


# ----- 股票名称关键词匹配的概念板块 -----
NAME_BOARDS = {
    # === AI / 算力 ===
    "人工智能": {
        "keywords": ["人工智能", "AI", "智能科技", "智能技术"],
        "exclude": ["人工智能ETF"],
        "extra_names": [],
    },
    "AI算力": {
        "keywords": ["算力", "数据中心", "数据港", "IDC", "润泽科技"],
        "exclude": [],
        "extra_names": [],
    },
    "AI服务器": {
        "keywords": ["服务器"],
        "exclude": [],
        "extra_names": ["浪潮信息", "中科曙光", "紫光股份", "工业富联"],
    },
    "AI芯片": {
        "keywords": ["AI芯片", "人工智能芯片", "神经网络"],
        "exclude": [],
        "extra_names": ["寒武纪", "海光信息"],
    },
    "AIGC": {
        "keywords": ["AIGC"],
        "exclude": [],
        "extra_names": ["万兴科技", "昆仑万维", "中文在线", "蓝色光标", "因赛集团"],
    },
    "AI应用": {
        "keywords": ["AI应用", "智能应用"],
        "exclude": [],
        "extra_names": ["科大讯飞", "拓尔思", "汉王科技", "金山办公"],
    },
    "AI教育": {
        "keywords": ["AI教育", "智慧教育", "教育科技", "在线教育"],
        "exclude": [],
        "extra_names": ["科大讯飞", "视源股份", "鸿合科技"],
    },
    "AI医疗": {
        "keywords": ["AI医疗", "智慧医疗", "医疗AI"],
        "exclude": [],
        "extra_names": ["科大讯飞", "卫宁健康", "创业慧康", "万达信息"],
    },
    "AI机器人": {
        "keywords": ["人形机器人", "仿人机器人"],
        "exclude": [],
        "extra_names": ["优必选", "特斯拉"],
    },
    "机器学习": {
        "keywords": ["机器学习"],
        "exclude": [],
        "extra_names": [],
    },
    "深度学习": {
        "keywords": ["深度学习"],
        "exclude": [],
        "extra_names": [],
    },
    "大模型": {
        "keywords": ["大模型", "大语言模型", "GPT"],
        "exclude": [],
        "extra_names": ["科大讯飞", "三六零", "昆仑万维", "百度"],
    },
    "数据要素": {
        "keywords": ["数据要素", "数据交易", "数据确权", "数据资产"],
        "exclude": [],
        "extra_names": ["易华录", "深桑达A", "太极股份", "数据港"],
    },
    "数据安全": {
        "keywords": ["数据安全", "信息安全", "网络安全", "信息安全"],
        "exclude": [],
        "extra_names": ["奇安信", "启明星辰", "深信服", "天融信", "绿盟科技", "安恒信息"],
    },
    "大数据": {
        "keywords": ["大数据"],
        "exclude": [],
        "extra_names": ["东方国信", "美林数据", "拓尔思", "海量数据"],
    },
    "云计算": {
        "keywords": ["云计算", "云服务", "云平台", "公有云", "私有云"],
        "exclude": [],
        "extra_names": ["用友网络", "金山办公", "广联达"],
    },
    "边缘计算": {
        "keywords": ["边缘计算", "边缘智能"],
        "exclude": [],
        "extra_names": ["网宿科技", "中兴通讯"],
    },
    "工业互联网": {
        "keywords": ["工业互联网", "工业4.0", "工业互联"],
        "exclude": [],
        "extra_names": ["用友网络", "东方国信", "宝信软件"],
    },
    "物联网": {
        "keywords": ["物联网", "IoT"],
        "exclude": [],
        "extra_names": ["广和通", "移远通信", "美格智能"],
    },
    "区块链": {
        "keywords": ["区块链"],
        "exclude": [],
        "extra_names": [],
    },
    "数字货币": {
        "keywords": ["数字货币", "数字人民币", "DCEP"],
        "exclude": [],
        "extra_names": ["楚天龙", "飞天诚信", "数字认证", "拉卡拉"],
    },
    "Web3.0": {
        "keywords": ["Web3", "Web3.0"],
        "exclude": [],
        "extra_names": [],
    },
    "元宇宙": {
        "keywords": ["元宇宙", "Metaverse"],
        "exclude": [],
        "extra_names": [],
    },
    "虚拟现实VR": {
        "keywords": ["虚拟现实", "VR"],
        "exclude": [],
        "extra_names": [],
    },
    "增强现实AR": {
        "keywords": ["增强现实", "AR"],
        "exclude": [],
        "extra_names": [],
    },
    "混合现实MR": {
        "keywords": ["混合现实", "MR"],
        "exclude": [],
        "extra_names": [],
    },
    "数字孪生": {
        "keywords": ["数字孪生"],
        "exclude": [],
        "extra_names": [],
    },
    "数字经济": {
        "keywords": ["数字经济"],
        "exclude": [],
        "extra_names": ["太极股份", "中国长城", "中国软件"],
    },
    "东数西算": {
        "keywords": ["东数西算"],
        "exclude": [],
        "extra_names": ["润建股份", "美利云", "数据港", "光环新网"],
    },

    # === 光通信 / 光模块 ===
    "光模块": {
        "keywords": ["光模块", "光器件"],
        "exclude": [],
        "extra_names": ["中际旭创", "新易盛", "天孚通信", "光迅科技", "华工科技", "亨通光电"],
    },
    "光芯片": {
        "keywords": ["光芯片", "激光芯片"],
        "exclude": [],
        "extra_names": ["源杰科技", "长光华芯", "仕佳光子"],
    },
    "高速光模块": {
        "keywords": ["光模块"],
        "exclude": [],
        "extra_names": ["中际旭创", "新易盛", "天孚通信"],
    },
    "CPO": {
        "keywords": ["CPO", "共封装光学"],
        "exclude": [],
        "extra_names": ["中际旭创", "新易盛", "天孚通信", "光迅科技", "华工科技"],
    },
    "硅光": {
        "keywords": ["硅光", "硅光子"],
        "exclude": [],
        "extra_names": ["博创科技", "亨通光电", "中际旭创"],
    },
    "太赫兹": {
        "keywords": ["太赫兹"],
        "exclude": [],
        "extra_names": [],
    },
    "量子计算": {
        "keywords": ["量子计算", "量子"],
        "exclude": ["量子通信"],
        "extra_names": ["国盾量子", "科大国创"],
    },
    "量子通信": {
        "keywords": ["量子通信", "量子保密"],
        "exclude": [],
        "extra_names": ["神州信息", "亨通光电", "科大国创"],
    },

    # === 通信 ===
    "6G": {
        "keywords": ["6G"],
        "exclude": [],
        "extra_names": [],
    },
    "5G": {
        "keywords": ["5G"],
        "exclude": ["5G-A"],
        "extra_names": [],
    },
    "5G-A": {
        "keywords": ["5G-A", "5.5G"],
        "exclude": [],
        "extra_names": [],
    },
    "卫星互联网": {
        "keywords": ["卫星互联网", "卫星通信", "卫星网络"],
        "exclude": [],
        "extra_names": ["中国卫通", "铖昌科技"],
    },
    "商业航天": {
        "keywords": ["航天", "卫星", "火箭", "星际"],
        "exclude": [],
        "extra_names": [],
    },
    "卫星导航": {
        "keywords": ["卫星导航", "北斗导航", "北斗", "导航定位", "GNSS"],
        "exclude": ["中国卫星"],
        "extra_names": ["华测导航", "北斗星通", "海格通信", "中海达"],
    },
    "北斗": {
        "keywords": ["北斗"],
        "exclude": [],
        "extra_names": ["北斗星通", "海格通信", "华测导航", "中海达"],
    },
    "星链": {
        "keywords": ["星链", "Starlink"],
        "exclude": [],
        "extra_names": [],
    },

    # === 低空经济 / 飞行器 ===
    "低空经济": {
        "keywords": ["低空经济", "低空", "eVTOL", "飞行汽车", "通用航空"],
        "exclude": [],
        "extra_names": ["万丰奥威", "中信海直", "莱斯信息", "宗申动力"],
    },
    "无人机": {
        "keywords": ["无人机", "无人驾驶飞机"],
        "exclude": [],
        "extra_names": ["大疆", "亿航智能"],
    },

    # === 机器人 ===
    "机器人": {
        "keywords": ["机器人"],
        "exclude": ["人形机器人"],
        "extra_names": [],
    },
    "工业机器人": {
        "keywords": ["工业机器人", "自动化"],
        "exclude": ["机器人教育"],
        "extra_names": ["埃斯顿", "汇川技术", "新松机器人", "拓斯达"],
    },
    "服务机器人": {
        "keywords": ["服务机器人", "扫地机器人", "清洁机器人"],
        "exclude": [],
        "extra_names": ["科沃斯", "石头科技", "亿嘉和"],
    },
    "人形机器人": {
        "keywords": ["人形机器人", "仿人", "双足机器人"],
        "exclude": [],
        "extra_names": ["优必选", "小米集团"],
    },
    "减速器": {
        "keywords": ["减速器", "精密减速", "谐波减速", "RV减速"],
        "exclude": [],
        "extra_names": ["绿的谐波", "双环传动", "中大力德", "秦川机床"],
    },
    "伺服电机": {
        "keywords": ["伺服电机", "伺服系统", "伺服"],
        "exclude": [],
        "extra_names": ["汇川技术", "禾川科技", "雷赛智能"],
    },
    "机器视觉": {
        "keywords": ["机器视觉", "视觉检测", "图像识别"],
        "exclude": [],
        "extra_names": ["奥普特", "海康机器人", "凌云光"],
    },
    "传感器": {
        "keywords": ["传感器"],
        "exclude": [],
        "extra_names": ["韦尔股份", "歌尔股份", "森霸传感", "汉威科技"],
    },
    "MEMS": {
        "keywords": ["MEMS", "微机电"],
        "exclude": [],
        "extra_names": ["敏芯股份", "歌尔股份", "瑞声科技"],
    },

    # === 半导体细分 ===
    "芯片": {
        "keywords": ["芯片", "集成电路", "微电子"],
        "exclude": [],
        "extra_names": [],
    },
    "光刻机": {
        "keywords": ["光刻", "光刻机", "曝光机"],
        "exclude": [],
        "extra_names": ["茂莱光学", "福晶科技", "苏大维格"],
    },
    "刻蚀机": {
        "keywords": ["刻蚀", "刻蚀机"],
        "exclude": [],
        "extra_names": ["中微公司", "北方华创"],
    },
    "半导体设备": {
        "keywords": ["半导体设备", "晶圆设备", "芯片设备"],
        "exclude": [],
        "extra_names": ["北方华创", "中微公司", "盛美上海", "华海清科", "拓荆科技", "芯源微"],
    },
    "半导体材料": {
        "keywords": ["半导体材料", "电子气体", "光刻胶", "抛光液", "靶材", "电子化学品"],
        "exclude": [],
        "extra_names": ["沪硅产业", "雅克科技", "南大光电", "江丰电子", "安集科技", "鼎龙股份"],
    },
    "先进封装": {
        "keywords": ["先进封装", "封装测试", "封测", "封装"],
        "exclude": ["包装"],
        "extra_names": ["长电科技", "通富微电", "华天科技", "甬矽电子", "伟测科技"],
    },
    "Chiplet": {
        "keywords": ["Chiplet", "芯粒"],
        "exclude": [],
        "extra_names": ["通富微电", "长电科技", "芯原股份"],
    },
    "晶圆": {
        "keywords": ["晶圆"],
        "exclude": [],
        "extra_names": ["中芯国际", "华虹半导体", "华润微"],
    },
    "硅片": {
        "keywords": ["硅片", "大硅片"],
        "exclude": [],
        "extra_names": ["沪硅产业", "TCL中环", "隆基绿能"],
    },
    "IGBT": {
        "keywords": ["IGBT"],
        "exclude": [],
        "extra_names": ["斯达半导", "时代电气", "宏微科技", "新洁能", "士兰微"],
    },
    "MOSFET": {
        "keywords": ["MOSFET", "MOS管"],
        "exclude": [],
        "extra_names": ["新洁能", "士兰微", "扬杰科技", "闻泰科技"],
    },
    "功率半导体": {
        "keywords": ["功率半导体", "功率器件", "功率IC"],
        "exclude": [],
        "extra_names": ["斯达半导", "时代电气", "扬杰科技", "士兰微", "新洁能", "闻泰科技"],
    },
    "第三代半导体": {
        "keywords": ["第三代半导体"],
        "exclude": [],
        "extra_names": ["三安光电", "天岳先进", "露笑科技", "东尼电子", "英诺特"],
    },
    "氮化镓": {
        "keywords": ["氮化镓", "GaN"],
        "exclude": [],
        "extra_names": ["三安光电", "海特高新", "赛微电子", "闻泰科技"],
    },
    "碳化硅": {
        "keywords": ["碳化硅", "SiC"],
        "exclude": [],
        "extra_names": ["三安光电", "天岳先进", "露笑科技", "东尼电子", "时代电气"],
    },
    "化合物半导体": {
        "keywords": ["化合物半导体"],
        "exclude": [],
        "extra_names": ["三安光电", "云南锗业", "有研新材"],
    },

    # === PCB / 面板 / 光学 ===
    "PCB": {
        "keywords": ["PCB", "印制电路", "电路板"],
        "exclude": ["FPC"],
        "extra_names": [],
    },
    "覆铜板": {
        "keywords": ["覆铜板", "CCL"],
        "exclude": [],
        "extra_names": ["生益科技", "华正新材", "南亚新材", "建滔积层板"],
    },
    "FPC": {
        "keywords": ["FPC", "柔性电路", "柔性线路"],
        "exclude": [],
        "extra_names": ["鹏鼎控股", "东山精密", "景旺电子", "传音控股"],
    },
    "显示面板": {
        "keywords": ["显示面板", "面板", "显示器"],
        "exclude": [],
        "extra_names": ["京东方A", "TCL科技", "维信诺", "深天马A"],
    },
    "LCD": {
        "keywords": ["LCD", "液晶面板", "液晶显示", "TFT-LCD"],
        "exclude": [],
        "extra_names": ["京东方A", "TCL科技"],
    },
    "OLED": {
        "keywords": ["OLED", "有机发光"],
        "exclude": [],
        "extra_names": ["京东方A", "维信诺", "和辉光电", "彩虹股份", "奥来德"],
    },
    "MiniLED": {
        "keywords": ["MiniLED", "Mini LED"],
        "exclude": [],
        "extra_names": ["三安光电", "TCL科技", "京东方A", "兆元光电"],
    },
    "MicroLED": {
        "keywords": ["MicroLED", "Micro LED"],
        "exclude": [],
        "extra_names": ["三安光电", "京东方A"],
    },
    "柔性屏": {
        "keywords": ["柔性屏", "柔性显示", "折叠屏", "折叠"],
        "exclude": ["柔性电路", "FPC"],
        "extra_names": ["京东方A", "维信诺", "TCL科技"],
    },
    "屏下指纹": {
        "keywords": ["屏下指纹", "指纹识别", "指纹芯片"],
        "exclude": [],
        "extra_names": ["汇顶科技", "兆易创新", "思立微"],
    },
    "摄像头": {
        "keywords": ["摄像头", "摄像", "影像", "相机模组"],
        "exclude": [],
        "extra_names": ["欧菲光", "舜宇光学", "联创电子", "水晶光电"],
    },
    "CIS": {
        "keywords": ["CIS", "CMOS图像", "图像传感器"],
        "exclude": [],
        "extra_names": ["韦尔股份", "格科微", "思特威"],
    },
    "镜头": {
        "keywords": ["镜头", "光学镜头"],
        "exclude": [],
        "extra_names": ["舜宇光学", "欧菲光", "联创电子", "水晶光电", "永新光学"],
    },
    "光学元件": {
        "keywords": ["光学元件", "光学器件", "光学", "滤光片"],
        "exclude": ["光学镜头"],
        "extra_names": ["水晶光电", "五粮液", "福晶科技", "腾景科技"],
    },

    # === 消费电子 / 苹果 / 华为 ===
    "消费电子": {
        "keywords": ["消费电子", "电子制造"],
        "exclude": [],
        "extra_names": ["立讯精密", "歌尔股份", "蓝思科技", "领益智造", "传音控股", "欧菲光", "信维通信", "瑞芯微", "恒玄科技"],
    },
    "苹果概念": {
        "keywords": ["苹果"],
        "exclude": [],
        "extra_names": ["立讯精密", "歌尔股份", "蓝思科技", "领益智造", "欧菲光", "信维通信", "舜宇光学"],
    },
    "华为概念": {
        "keywords": ["华为"],
        "exclude": [],
        "extra_names": ["中芯国际", "京东方A"],
    },
    "鸿蒙": {
        "keywords": ["鸿蒙", "HarmonyOS", "鸿蒙OS"],
        "exclude": [],
        "extra_names": ["润和软件", "诚迈科技", "拓维信息", "常山北明"],
    },
    "欧拉": {
        "keywords": ["欧拉", "openEuler"],
        "exclude": [],
        "extra_names": ["润和软件", "拓维信息"],
    },
    "小米概念": {
        "keywords": ["小米"],
        "exclude": [],
        "extra_names": [],
    },
    "特斯拉概念": {
        "keywords": ["特斯拉"],
        "exclude": [],
        "extra_names": [],
    },
    "英伟达概念": {
        "keywords": ["英伟达", "NVIDIA", "Nvidia"],
        "exclude": [],
        "extra_names": ["中际旭创", "工业富联"],
    },
    "AMD概念": {
        "keywords": ["AMD"],
        "exclude": [],
        "extra_names": [],
    },

    # === 新能源 ===
    "新能源": {
        "keywords": ["新能源"],
        "exclude": [],
        "extra_names": ["宁德时代", "比亚迪", "隆基绿能", "阳光电源"],
    },
    "碳中和": {
        "keywords": ["碳中和", "碳达峰", "碳排放", "碳交易", "碳汇"],
        "exclude": [],
        "extra_names": ["龙源电力", "三峡能源", "长江电力", "华能国际"],
    },
    "碳达峰": {
        "keywords": ["碳达峰", "碳排放"],
        "exclude": [],
        "extra_names": [],
    },
    "碳交易": {
        "keywords": ["碳交易", "碳排放权"],
        "exclude": [],
        "extra_names": [],
    },
    "碳捕获": {
        "keywords": ["碳捕获", "碳捕集", "CCUS", "CCS"],
        "exclude": [],
        "extra_names": [],
    },
    "绿色电力": {
        "keywords": ["绿色电力", "绿电", "清洁能源"],
        "exclude": [],
        "extra_names": ["龙源电力", "三峡能源", "长江电力", "华能国际", "国电电力"],
    },
    "绿电": {
        "keywords": ["绿电"],
        "exclude": [],
        "extra_names": [],
    },
    "光伏": {
        "keywords": ["光伏", "太阳能", "硅料", "硅片", "光伏组件", "电池组件"],
        "exclude": ["太阳能电池"],
        "extra_names": ["隆基绿能", "通威股份", "阳光电源", "晶澳科技", "天合光能", "晶科能源", "TCL中环", "锦浪科技", "固德威", "德业股份", "福斯特", "福莱特", "爱旭股份", "东方日升", "大全能源"],
    },
    "晶硅": {
        "keywords": ["晶硅", "多晶硅", "单晶硅"],
        "exclude": [],
        "extra_names": ["通威股份", "大全能源", "协鑫科技", "新特能源", "保利协鑫"],
    },
    "TOPCon": {
        "keywords": ["TOPCon"],
        "exclude": [],
        "extra_names": ["晶科能源", "天合光能", "晶澳科技", "钧达股份", "一道新能"],
    },
    "HJT": {
        "keywords": ["HJT", "异质结", "异质结电池"],
        "exclude": [],
        "extra_names": ["东方日升", "爱康科技", "金刚玻璃"],
    },
    "BC电池": {
        "keywords": ["BC电池", "BC组件", "背接触"],
        "exclude": [],
        "extra_names": ["隆基绿能", "爱旭股份", "通威股份"],
    },
    "钙钛矿": {
        "keywords": ["钙钛矿", "钙钛矿电池"],
        "exclude": [],
        "extra_names": ["协鑫光电", "极电光能", "纤纳光电", "纤纳", "微导纳米"],
    },
    "BIPV": {
        "keywords": ["BIPV", "光伏建筑", "光伏幕墙"],
        "exclude": [],
        "extra_names": ["隆基绿能", "森特股份", "亚玛顿"],
    },
    "光伏胶膜": {
        "keywords": ["光伏胶膜", "胶膜", "EVA胶膜"],
        "exclude": ["面膜"],
        "extra_names": ["福斯特", "海优新材", "赛伍技术"],
    },
    "光伏玻璃": {
        "keywords": ["光伏玻璃"],
        "exclude": [],
        "extra_names": ["福莱特", "信义光能", "亚玛顿", "旗滨集团"],
    },
    "逆变器": {
        "keywords": ["逆变器"],
        "exclude": [],
        "extra_names": ["阳光电源", "锦浪科技", "固德威", "德业股份", "禾迈股份", "上能电气"],
    },
    "风电": {
        "keywords": ["风电", "风力发电", "风电机组", "风机"],
        "exclude": [],
        "extra_names": ["金风科技", "明阳智能", "东方电气", "运达股份", "电气风电", "日月股份", "天顺风能", "金雷股份", "中材科技"],
    },
    "陆上风电": {
        "keywords": ["风电", "风力发电"],
        "exclude": ["海上风电", "海风"],
        "extra_names": ["金风科技", "明阳智能", "运达股份", "电气风电"],
    },
    "海上风电": {
        "keywords": ["海上风电", "海风", "海上风能"],
        "exclude": [],
        "extra_names": ["明阳智能", "东方电气", "电气风电", "大金重工", "天顺风能"],
    },
    "塔筒": {
        "keywords": ["塔筒", "风电塔筒"],
        "exclude": [],
        "extra_names": ["天顺风能", "大金重工", "泰胜风能", "天能重工"],
    },
    "叶片": {
        "keywords": ["风电叶片", "风叶", "叶片"],
        "exclude": [],
        "extra_names": ["中材科技", "时代新材"],
    },

    # === 氢能 ===
    "氢能": {
        "keywords": ["氢能", "氢能源", "氢气"],
        "exclude": ["氢氧化"],
        "extra_names": ["亿华通", "美锦能源", "雄韬股份"],
    },
    "燃料电池": {
        "keywords": ["燃料电池", "氢燃料", "质子交换膜"],
        "exclude": [],
        "extra_names": ["亿华通", "雄韬股份", "潍柴动力", "雪人股份"],
    },
    "氢能源": {
        "keywords": ["氢能源", "氢能"],
        "exclude": [],
        "extra_names": [],
    },
    "电解水制氢": {
        "keywords": ["电解水", "制氢", "电解槽"],
        "exclude": [],
        "extra_names": ["隆基绿能", "阳光电源", "华电重工", "亿利洁能"],
    },
    "储氢": {
        "keywords": ["储氢", "氢储运", "氢气储存"],
        "exclude": [],
        "extra_names": [],
    },
    "加氢站": {
        "keywords": ["加氢站", "加氢"],
        "exclude": [],
        "extra_names": [],
    },

    # === 储能 / 电池 ===
    "储能": {
        "keywords": ["储能"],
        "exclude": [],
        "extra_names": ["宁德时代", "比亚迪", "阳光电源", "派能科技", "固德威", "锦浪科技"],
    },
    "锂电储能": {
        "keywords": ["锂电储能", "锂电池储能"],
        "exclude": [],
        "extra_names": ["宁德时代", "比亚迪", "亿纬锂能", "派能科技"],
    },
    "钠离子电池": {
        "keywords": ["钠离子电池", "钠电池", "钠电"],
        "exclude": [],
        "extra_names": ["宁德时代", "华阳股份", "传艺科技", "维科技术"],
    },
    "固态电池": {
        "keywords": ["固态电池", "固态电解质", "全固态"],
        "exclude": [],
        "extra_names": ["宁德时代", "赣锋锂业", "比亚迪", "亿纬锂能", "孚能科技", "国轩高科"],
    },
    "钒电池": {
        "keywords": ["钒电池", "全钒液流", "钒液流"],
        "exclude": [],
        "extra_names": ["钒钛股份", "河钢股份", "上海电气"],
    },
    "液流电池": {
        "keywords": ["液流电池"],
        "exclude": [],
        "extra_names": ["钒钛股份", "上海电气"],
    },
    "抽水蓄能": {
        "keywords": ["抽水蓄能", "抽蓄"],
        "exclude": [],
        "extra_names": ["中国电建", "中国能建", "文山电力"],
    },
    "虚拟电厂": {
        "keywords": ["虚拟电厂"],
        "exclude": [],
        "extra_names": ["国电南瑞", "国网信通", "朗新科技", "远光软件", "恒实科技"],
    },
    "特高压": {
        "keywords": ["特高压"],
        "exclude": [],
        "extra_names": ["国电南瑞", "许继电气", "中国西电", "平高电气", "特变电工", "思源电气"],
    },
    "智能电网": {
        "keywords": ["智能电网", "电网"],
        "exclude": ["特高压"],
        "extra_names": ["国电南瑞", "许继电气", "国网信通", "积成电子"],
    },
    "电网自动化": {
        "keywords": ["电网自动化", "配网自动化", "调度自动化"],
        "exclude": [],
        "extra_names": ["国电南瑞", "许继电气", "积成电子", "东方电子"],
    },
    "高压开关": {
        "keywords": ["高压开关", "断路器", "隔离开关"],
        "exclude": [],
        "extra_names": ["中国西电", "平高电气", "思源电气"],
    },
    "变压器": {
        "keywords": ["变压器"],
        "exclude": [],
        "extra_names": ["特变电工", "许继电气", "保变电气", "中国西电"],
    },
    "充电桩": {
        "keywords": ["充电桩", "充电设备", "充电站", "充电设施"],
        "exclude": [],
        "extra_names": ["特锐德", "星星充电", "科士达", "科华数据"],
    },
    "换电概念": {
        "keywords": ["换电", "电池更换"],
        "exclude": [],
        "extra_names": ["瀚川智能", "博众精工", "协鑫能科"],
    },
    "动力电池": {
        "keywords": ["动力电池", "锂电池"],
        "exclude": ["融捷健康", "成都先导"],
        "extra_names": ["宁德时代", "比亚迪", "中创新航", "国轩高科", "亿纬锂能", "欣旺达", "孚能科技"],
    },
    "锂电材料": {
        "keywords": ["锂电材料", "正极", "负极", "电解液", "隔膜", "锂电化学品"],
        "exclude": ["融捷健康", "成都先导"],
        "extra_names": [],
    },
    "锂矿": {
        "keywords": ["锂矿", "锂资源", "锂盐", "碳酸锂", "氢氧化锂"],
        "exclude": ["融捷健康", "成都先导"],
        "extra_names": ["天齐锂业", "赣锋锂业", "融捷股份", "盐湖股份", "西藏矿业", "中矿资源", "江特电机", "永兴材料", "川能动力"],
    },
    "盐湖提锂": {
        "keywords": ["盐湖提锂", "盐湖"],
        "exclude": ["融捷健康", "成都先导"],
        "extra_names": ["盐湖股份", "藏格矿业", "西藏矿业", "西藏城投"],
    },
    "钴": {
        "keywords": ["钴", "钴业"],
        "exclude": [],
        "extra_names": ["华友钴业", "寒锐钴业", "洛阳钼业", "格林美"],
    },
    "镍": {
        "keywords": ["镍", "镍业"],
        "exclude": [],
        "extra_names": ["华友钴业", "格林美", "中伟股份"],
    },
    "锰": {
        "keywords": ["锰", "锰业", "锰铁"],
        "exclude": [],
        "extra_names": ["湘潭电化", "红星发展", "中钢天源"],
    },
    "磷": {
        "keywords": ["磷", "磷酸", "磷化工"],
        "exclude": ["磷肥"],
        "extra_names": ["云天化", "兴发集团", "川恒股份", "云图控股"],
    },
    "氟": {
        "keywords": ["氟", "氟化工", "氟化"],
        "exclude": [],
        "extra_names": ["多氟多", "巨化股份", "天赐材料", "永和股份", "东岳集团"],
    },
    "PVDF": {
        "keywords": ["PVDF"],
        "exclude": [],
        "extra_names": ["联创股份", "东岳集团", "巨化股份", "乳源东阳光"],
    },
    "电解液": {
        "keywords": ["电解液"],
        "exclude": [],
        "extra_names": ["天赐材料", "新宙邦", "国泰华荣", "瑞泰新能源", "石大胜华"],
    },
    "隔膜": {
        "keywords": ["隔膜", "锂电池隔膜", "湿法隔膜", "干法隔膜"],
        "exclude": ["面膜"],
        "extra_names": ["恩捷股份", "星源材质", "中材科技"],
    },
    "正极材料": {
        "keywords": ["正极材料", "三元材料", "三元正极", "磷酸铁锂", "锰酸锂", "高镍"],
        "exclude": [],
        "extra_names": ["德方纳米", "容百科技", "当升科技", "长远锂科", "厦门钨业"],
    },
    "负极材料": {
        "keywords": ["负极材料", "石墨负极", "硅基负极", "人造石墨", "天然石墨"],
        "exclude": ["碳石墨"],
        "extra_names": ["贝特瑞", "璞泰来", "杉杉股份", "翔丰华", "中科电气"],
    },
    "电池回收": {
        "keywords": ["电池回收", "动力电池回收", "锂电回收", "再生铅"],
        "exclude": [],
        "extra_names": ["格林美", "邦普循环", "华友钴业", "光华科技", "赣锋锂业"],
    },

    # === 新能源车 ===
    "新能源车": {
        "keywords": ["新能源车", "新能源汽车", "电动汽车", "电动车"],
        "exclude": ["电动工具", "电动自行车", "电力设备"],
        "extra_names": ["比亚迪", "理想汽车", "蔚来", "小鹏汽车", "赛力斯", "北汽蓝谷", "长安汽车", "广汽集团", "上汽集团", "江淮汽车", "海马汽车", "众泰汽车"],
    },
    "新能源整车": {
        "keywords": ["新能源整车", "电动汽车"],
        "exclude": [],
        "extra_names": ["比亚迪", "理想汽车", "蔚来", "小鹏汽车", "赛力斯"],
    },
    "自动驾驶": {
        "keywords": ["自动驾驶", "无人驾驶", "autonomous"],
        "exclude": [],
        "extra_names": ["小鹏汽车", "百度", "德赛西威", "禾赛科技"],
    },
    "无人驾驶": {
        "keywords": ["无人驾驶"],
        "exclude": [],
        "extra_names": [],
    },
    "车联网": {
        "keywords": ["车联网", "V2X", "车载互联"],
        "exclude": [],
        "extra_names": ["德赛西威", "中科创达", "鸿泉物联"],
    },
    "智能座舱": {
        "keywords": ["智能座舱", "座舱", "车载娱乐"],
        "exclude": [],
        "extra_names": ["德赛西威", "华阳集团", "均胜电子"],
    },
    "汽车电子": {
        "keywords": ["汽车电子", "车载电子"],
        "exclude": [],
        "extra_names": ["德赛西威", "均胜电子", "华阳集团", "保隆科技"],
    },
    "车载芯片": {
        "keywords": ["车载芯片", "车规芯片", "车规级芯片"],
        "exclude": [],
        "extra_names": ["韦尔股份", "兆易创新", "纳芯微", "杰发科技"],
    },
    "高压快充": {
        "keywords": ["高压快充", "快充", "超充", "800V"],
        "exclude": [],
        "extra_names": ["宁德时代", "比亚迪", "欣旺达", "亿纬锂能", "特锐德"],
    },
    "汽车轻量化": {
        "keywords": ["汽车轻量化", "轻量化"],
        "exclude": [],
        "extra_names": ["文灿股份", "旭升集团", "拓普集团", "爱柯迪"],
    },

    # === 医药 ===
    "创新药": {
        "keywords": ["创新药", "创新生物"],
        "exclude": [],
        "extra_names": ["恒瑞医药", "百济神州", "信达生物", "君实生物", "荣昌生物"],
    },
    "仿制药": {
        "keywords": ["仿制药", "仿制"],
        "exclude": [],
        "extra_names": [],
    },
    "中医药": {
        "keywords": ["中医药", "中药"],
        "exclude": [],
        "extra_names": [],
    },
    "老字号": {
        "keywords": ["老字号", "中华老字号", "百年"],
        "exclude": [],
        "extra_names": ["片仔癀", "云南白药", "同仁堂", "贵州茅台", "五粮液", "泸州老窖", "东阿阿胶", "广誉远", "马应龙", "白云山", "九芝堂", "江中药业", "太极集团"],
    },
    "生物疫苗": {
        "keywords": ["疫苗", "生物疫苗"],
        "exclude": [],
        "extra_names": ["智飞生物", "万泰生物", "康泰生物", "沃森生物", "康希诺"],
    },
    "新冠药": {
        "keywords": ["新冠药", "新冠治疗", "抗新冠"],
        "exclude": [],
        "extra_names": ["君实生物", "真实生物", "先声药业"],
    },
    "新冠检测": {
        "keywords": ["新冠检测", "核酸检测"],
        "exclude": [],
        "extra_names": [],
    },
    "医疗器械": {
        "keywords": ["医疗器械", "医疗设备"],
        "exclude": [],
        "extra_names": ["迈瑞医疗", "联影医疗", "微创医疗", "新华医疗"],
    },
    "医疗设备": {
        "keywords": ["医疗设备", "医疗影像", "医疗诊断设备"],
        "exclude": [],
        "extra_names": ["迈瑞医疗", "联影医疗", "开立医疗", "新华医疗"],
    },
    "高值耗材": {
        "keywords": ["高值耗材", "医疗耗材", "医用耗材"],
        "exclude": [],
        "extra_names": ["威高股份", "大博医疗", "三鑫医疗", "健帆生物"],
    },
    "体外诊断IVD": {
        "keywords": ["体外诊断", "IVD", "生化诊断", "免疫诊断", "分子诊断"],
        "exclude": [],
        "extra_names": ["迈瑞医疗", "安图生物", "新产业", "万孚生物", "圣湘生物", "达安基因"],
    },
    "基因测序": {
        "keywords": ["基因测序", "基因检测", "基因芯片"],
        "exclude": [],
        "extra_names": ["华大基因", "贝瑞基因", "诺禾致源", "燃石医学"],
    },
    "细胞治疗": {
        "keywords": ["细胞治疗", "细胞免疫", "CAR-T"],
        "exclude": [],
        "extra_names": ["复星凯特", "药明巨诺", "金斯瑞"],
    },
    "干细胞": {
        "keywords": ["干细胞"],
        "exclude": [],
        "extra_names": ["中源协和", "冠昊生物"],
    },
    "医美": {
        "keywords": ["医美", "医疗美容", "整形"],
        "exclude": [],
        "extra_names": ["爱美客", "华熙生物", "昊海生科", "华东医药"],
    },
    "胶原蛋白": {
        "keywords": ["胶原蛋白"],
        "exclude": [],
        "extra_names": ["巨子生物", "锦波生物", "华熙生物", "丸美股份"],
    },
    "玻尿酸": {
        "keywords": ["玻尿酸", "透明质酸"],
        "exclude": [],
        "extra_names": ["华熙生物", "爱美客", "昊海生科", "鲁商发展"],
    },
    "养老概念": {
        "keywords": ["养老", "养老产业", "养老地产", "养老服务"],
        "exclude": [],
        "extra_names": [],
    },
    "辅助生殖": {
        "keywords": ["辅助生殖", "生殖医学", "试管婴儿"],
        "exclude": [],
        "extra_names": ["锦欣生殖", "达嘉维康"],
    },
    "民营医院": {
        "keywords": ["民营医院", "私立医院", "医院"],
        "exclude": [],
        "extra_names": ["爱尔眼科", "通策医疗", "美年健康"],
    },
    "CXO": {
        "keywords": ["CXO", "医药外包"],
        "exclude": [],
        "extra_names": ["药明康德", "药明生物", "凯莱英", "康龙化成", "泰格医药", "九洲药业"],
    },
    "CRO": {
        "keywords": ["CRO"],
        "exclude": [],
        "extra_names": ["药明康德", "药明生物", "凯莱英", "康龙化成", "泰格医药", "睿智医药"],
    },
    "CMO": {
        "keywords": ["CMO"],
        "exclude": ["CXO"],
        "extra_names": ["药明康德", "凯莱英", "九洲药业"],
    },
    "CDMO": {
        "keywords": ["CDMO"],
        "exclude": [],
        "extra_names": ["药明康德", "药明生物", "凯莱英", "博腾股份"],
    },
    "血液制品": {
        "keywords": ["血液制品", "血浆", "白蛋白", "免疫球蛋白"],
        "exclude": [],
        "extra_names": ["天坛生物", "华兰生物", "上海莱士", "博雅生物"],
    },
    "生物安全": {
        "keywords": ["生物安全", "生物安全柜"],
        "exclude": [],
        "extra_names": [],
    },
    "幽门螺杆菌": {
        "keywords": ["幽门螺杆菌", "幽门螺旋杆菌"],
        "exclude": [],
        "extra_names": [],
    },
    "阿尔茨海默": {
        "keywords": ["阿尔茨海默", "老年痴呆"],
        "exclude": [],
        "extra_names": ["绿谷制药"],
    },
    "糖尿病": {
        "keywords": ["糖尿病"],
        "exclude": [],
        "extra_names": ["甘李药业", "通化东宝", "联邦制药"],
    },
    "抗肿瘤药": {
        "keywords": ["抗肿瘤", "肿瘤药", "抗癌"],
        "exclude": [],
        "extra_names": ["恒瑞医药", "百济神州", "信达生物", "君实生物"],
    },
    "儿童药": {
        "keywords": ["儿童药", "儿童用药", "儿科"],
        "exclude": [],
        "extra_names": ["济川药业", "葫芦娃", "康芝药业"],
    },
    "罕见病": {
        "keywords": ["罕见病"],
        "exclude": [],
        "extra_names": [],
    },

    # === 消费 ===
    "白酒": {
        "keywords": ["白酒", "酒", "酒业"],
        "exclude": ["啤酒", "红酒", "葡萄酒", "黄酒", "保健酒", "酒业"],
        "extra_names": ["贵州茅台", "五粮液", "泸州老窖", "洋河股份", "山西汾酒", "古井贡酒", "今世缘", "迎驾贡酒"],
    },
    "啤酒": {
        "keywords": ["啤酒"],
        "exclude": [],
        "extra_names": [],
    },
    "葡萄酒": {
        "keywords": ["葡萄酒", "红酒"],
        "exclude": [],
        "extra_names": [],
    },
    "食品加工": {
        "keywords": ["食品", "食品加工", "速冻"],
        "exclude": ["食品安全", "食品饮料"],
        "extra_names": [],
    },
    "休闲食品": {
        "keywords": ["休闲食品", "零食", "坚果", "卤味", "辣条"],
        "exclude": [],
        "extra_names": ["三只松鼠", "良品铺子", "洽洽食品", "盐津铺子", "卫龙美味", "绝味食品", "周黑鸭"],
    },
    "调味品": {
        "keywords": ["调味品", "调味", "酱油", "醋", "味精", "鸡精"],
        "exclude": [],
        "extra_names": ["海天味业", "中炬高新", "千禾味业", "恒顺醋业", "安琪酵母", "涪陵榨菜"],
    },
    "预制菜": {
        "keywords": ["预制菜", "半成品菜"],
        "exclude": [],
        "extra_names": ["味知香", "千味央厨", "安井食品", "国联水产", "龙大美食", "得利斯"],
    },
    "速冻食品": {
        "keywords": ["速冻", "冷冻食品"],
        "exclude": [],
        "extra_names": ["安井食品", "三全食品", "思念食品", "千味央厨"],
    },
    "烘焙": {
        "keywords": ["烘焙", "面包", "蛋糕", "烘焙食品"],
        "exclude": [],
        "extra_names": ["桃李面包", "立高食品", "南侨食品"],
    },
    "保健品": {
        "keywords": ["保健品", "保健食品", "膳食补充"],
        "exclude": ["保健酒"],
        "extra_names": ["汤臣倍健", "仙乐健康"],
    },
    "免税概念": {
        "keywords": ["免税", "免税店"],
        "exclude": [],
        "extra_names": ["中国中免", "王府井", "海南发展"],
    },
    "新零售": {
        "keywords": ["新零售"],
        "exclude": [],
        "extra_names": [],
    },
    "电商": {
        "keywords": ["电商", "电子商务", "跨境电商"],
        "exclude": ["京东方"],
        "extra_names": ["苏宁易购", "跨境通", "南极电商", "壹网壹创", "丽人丽妆", "若羽臣"],
    },
    "直播电商": {
        "keywords": ["直播电商", "直播带货"],
        "exclude": [],
        "extra_names": ["快手", "遥望科技", "星期六"],
    },
    "网红经济": {
        "keywords": ["网红经济", "网红"],
        "exclude": ["网红打卡"],
        "extra_names": [],
    },
    "家电": {
        "keywords": ["家电", "电器"],
        "exclude": ["新电器"],
        "extra_names": [],
    },
    "白电": {
        "keywords": ["白电", "冰箱", "洗衣机", "空调", "冰柜"],
        "exclude": [],
        "extra_names": ["美的集团", "格力电器", "海尔智家"],
    },
    "黑电": {
        "keywords": ["黑电", "电视机", "电视", "彩电"],
        "exclude": [],
        "extra_names": ["海信视像", "TCL科技", "创维集团", "四川长虹"],
    },
    "厨电": {
        "keywords": ["厨电", "厨房电器", "油烟机", "燃气灶"],
        "exclude": [],
        "extra_names": ["老板电器", "华帝股份", "浙江美大", "火星人"],
    },
    "智能家居": {
        "keywords": ["智能家居", "智能家电", "智能门锁"],
        "exclude": [],
        "extra_names": ["美的集团", "海尔智家", "小米集团"],
    },
    "小家电": {
        "keywords": ["小家电", "厨房小家电"],
        "exclude": ["家电"],
        "extra_names": ["小熊电器", "苏泊尔", "九阳股份", "新宝股份", "莱克电气"],
    },
    "美妆": {
        "keywords": ["美妆", "彩妆", "化妆品"],
        "exclude": [],
        "extra_names": ["珀莱雅", "贝泰妮", "华熙生物", "上海家化", "逸仙电商", "水羊股份"],
    },
    "美容护理": {
        "keywords": ["美容护理", "个人护理"],
        "exclude": [],
        "extra_names": ["珀莱雅", "上海家化", "水羊股份", "稳健医疗"],
    },
    "纺织服饰": {
        "keywords": ["纺织", "服饰", "服装"],
        "exclude": [],
        "extra_names": [],
    },
    "家纺": {
        "keywords": ["家纺", "家纺用品", "床上用品"],
        "exclude": [],
        "extra_names": ["罗莱生活", "富安娜", "水星家纺", "梦洁股份"],
    },
    "鞋包": {
        "keywords": ["鞋", "箱包", "皮具", "运动鞋"],
        "exclude": [],
        "extra_names": ["安踏体育", "李宁", "特步国际", "申洲国际"],
    },
    "黄金珠宝": {
        "keywords": ["黄金珠宝", "珠宝", "首饰", "钻石", "珠宝首饰"],
        "exclude": [],
        "extra_names": ["周大福", "老凤祥", "豫园股份", "周大生", "潮宏基"],
    },
    "贵金属": {
        "keywords": ["贵金属", "黄金", "白银", "铂金"],
        "exclude": ["黄金珠宝"],
        "extra_names": ["紫金矿业", "山东黄金", "中金黄金", "银泰黄金"],
    },

    # === 能源 / 资源 ===
    "煤炭": {
        "keywords": ["煤", "煤炭"],
        "exclude": [],
        "extra_names": [],
    },
    "动力煤": {
        "keywords": ["动力煤"],
        "exclude": [],
        "extra_names": ["中国神华", "中煤能源", "陕西煤业", "兖矿能源"],
    },
    "焦煤": {
        "keywords": ["焦煤"],
        "exclude": [],
        "extra_names": ["山西焦煤", "平煤股份", "淮北矿业", "潞安环能"],
    },
    "焦炭": {
        "keywords": ["焦炭"],
        "exclude": [],
        "extra_names": ["山西焦化", "美锦能源", "陕西黑猫"],
    },
    "石油石化": {
        "keywords": ["石油", "石化", "油气"],
        "exclude": ["煤焦油"],
        "extra_names": ["中国石油", "中国石化", "中国海油"],
    },
    "天然气": {
        "keywords": ["天然气", "LNG"],
        "exclude": [],
        "extra_names": ["中国石油", "中国石化", "广汇能源", "新奥股份"],
    },
    "油气开采": {
        "keywords": ["油气开采", "石油开采", "采油"],
        "exclude": [],
        "extra_names": ["中国石油", "中国海油", "中曼石油"],
    },
    "油服": {
        "keywords": ["油服", "油田服务", "钻井"],
        "exclude": [],
        "extra_names": ["中海油服", "海油工程", "杰瑞股份", "石化油服"],
    },
    "页岩气": {
        "keywords": ["页岩气", "页岩油"],
        "exclude": [],
        "extra_names": [],
    },
    "有色金属": {
        "keywords": ["有色", "金属"],
        "exclude": ["黑色金属"],
        "extra_names": [],
    },
    "铜": {
        "keywords": ["铜", "铜业", "铜矿"],
        "exclude": ["铜陵"],
        "extra_names": ["紫金矿业", "江西铜业", "云南铜业", "铜陵有色", "西部矿业"],
    },
    "铝": {
        "keywords": ["铝", "铝业"],
        "exclude": [],
        "extra_names": ["中国铝业", "云铝股份", "南山铝业", "神火股份", "天山铝业"],
    },
    "锌": {
        "keywords": ["锌"],
        "exclude": [],
        "extra_names": ["驰宏锌锗", "中金岭南", "锌业股份"],
    },
    "锡": {
        "keywords": ["锡", "锡业"],
        "exclude": [],
        "extra_names": ["锡业股份"],
    },
    "铅": {
        "keywords": ["铅"],
        "exclude": [],
        "extra_names": ["豫光金铅", "驰宏锌锗"],
    },
    "稀土": {
        "keywords": ["稀土"],
        "exclude": [],
        "extra_names": ["北方稀土", "中国稀土", "广晟有色", "盛和资源", "厦门钨业"],
    },
    "永磁材料": {
        "keywords": ["永磁", "钕铁硼"],
        "exclude": [],
        "extra_names": ["北方稀土", "金力永磁", "宁波韵升", "中科三环", "英洛华"],
    },
    "黄金": {
        "keywords": ["黄金", "金矿", "金业"],
        "exclude": [],
        "extra_names": ["紫金矿业", "山东黄金", "中金黄金", "银泰黄金", "赤峰黄金", "湖南黄金"],
    },
    "白银": {
        "keywords": ["白银", "银矿"],
        "exclude": ["银行"],
        "extra_names": ["盛达资源", "银泰黄金", "紫金矿业", "盛和资源"],
    },

    # === 钢铁 / 建材 ===
    "普钢": {
        "keywords": ["钢铁", "钢材", "普钢"],
        "exclude": [],
        "extra_names": ["宝钢股份", "华菱钢铁", "鞍钢股份", "首钢股份"],
    },
    "特钢": {
        "keywords": ["特钢", "特殊钢", "合金钢"],
        "exclude": [],
        "extra_names": ["中信特钢", "方大特钢", "南钢股份", "抚顺特钢"],
    },
    "不锈钢": {
        "keywords": ["不锈钢"],
        "exclude": [],
        "extra_names": [],
    },
    "建筑钢材": {
        "keywords": ["建筑钢材", "螺纹钢", "线材"],
        "exclude": [],
        "extra_names": [],
    },
    "防水": {
        "keywords": ["防水", "防水材料"],
        "exclude": [],
        "extra_names": ["东方雨虹", "科顺股份", "凯伦股份"],
    },
    "涂料": {
        "keywords": ["涂料", "油漆", "涂装"],
        "exclude": ["染料"],
        "extra_names": ["三棵树", "亚士创能", "立邦"],
    },
    "管材": {
        "keywords": ["管材", "管道", "管业"],
        "exclude": [],
        "extra_names": ["伟星新材", "永高股份", "雄塑科技"],
    },
    "装配式建筑": {
        "keywords": ["装配式建筑", "装配式"],
        "exclude": [],
        "extra_names": ["远大住工", "鸿路钢构"],
    },
    "钢结构": {
        "keywords": ["钢结构"],
        "exclude": [],
        "extra_names": ["鸿路钢构", "精工钢构", "东南网架"],
    },

    # === 化工 ===
    "基础化工": {
        "keywords": ["基础化工", "化工"],
        "exclude": ["氟化工", "磷化工", "氯碱", "煤化工", "精细化工"],
        "extra_names": [],
    },
    "精细化工": {
        "keywords": ["精细化工", "精细化学"],
        "exclude": [],
        "extra_names": [],
    },
    "煤化工": {
        "keywords": ["煤化工", "煤制", "煤基"],
        "exclude": ["煤焦"],
        "extra_names": ["宝丰能源", "华鲁恒升", "鲁西化工", "丹化科技"],
    },
    "盐化工": {
        "keywords": ["盐化工"],
        "exclude": [],
        "extra_names": [],
    },
    "化纤": {
        "keywords": ["化纤", "涤纶", "锦纶", "氨纶", "腈纶", "维纶"],
        "exclude": [],
        "extra_names": ["桐昆股份", "恒逸石化", "荣盛石化", "东方盛虹", "新凤鸣"],
    },
    "塑料": {
        "keywords": ["塑料", "塑料制品"],
        "exclude": [],
        "extra_names": [],
    },
    "橡胶": {
        "keywords": ["橡胶", "轮胎"],
        "exclude": [],
        "extra_names": [],
    },
    "农药": {
        "keywords": ["农药"],
        "exclude": [],
        "extra_names": ["扬农化工", "利尔化学", "先达股份"],
    },
    "化肥": {
        "keywords": ["化肥", "复合肥", "氮肥", "磷肥", "钾肥", "尿素"],
        "exclude": [],
        "extra_names": ["史丹利", "新洋丰", "云天化", "盐湖股份"],
    },
    "磷化工": {
        "keywords": ["磷化工", "磷酸"],
        "exclude": [],
        "extra_names": ["云天化", "兴发集团", "川恒股份", "云图控股", "川金诺"],
    },
    "氟化工": {
        "keywords": ["氟化工", "含氟"],
        "exclude": [],
        "extra_names": ["巨化股份", "多氟多", "永和股份", "东岳集团"],
    },
    "氯碱化工": {
        "keywords": ["氯碱", "氯碱化工", "烧碱", "聚氯乙烯", "PVC"],
        "exclude": [],
        "extra_names": ["中泰化学", "新疆天业", "君正集团", "鄂尔多斯"],
    },
    "钛白粉": {
        "keywords": ["钛白粉"],
        "exclude": [],
        "extra_names": ["龙佰集团", "中核钛白", "惠云钛业"],
    },
    "甲醇": {
        "keywords": ["甲醇"],
        "exclude": [],
        "extra_names": [],
    },
    "乙二醇": {
        "keywords": ["乙二醇"],
        "exclude": [],
        "extra_names": [],
    },
    "纯碱": {
        "keywords": ["纯碱"],
        "exclude": [],
        "extra_names": ["远兴能源", "山东海化", "云图控股", "中盐化工"],
    },
    "烧碱": {
        "keywords": ["烧碱"],
        "exclude": [],
        "extra_names": [],
    },
    "维生素": {
        "keywords": ["维生素"],
        "exclude": [],
        "extra_names": ["新和成", "浙江医药", "兄弟科技", "亿帆医药"],
    },
    "染料": {
        "keywords": ["染料"],
        "exclude": [],
        "extra_names": ["浙江龙盛", "闰土股份", "吉华集团"],
    },
    "树脂": {
        "keywords": ["树脂"],
        "exclude": [],
        "extra_names": [],
    },

    # === 金融 ===
    "银行": {
        "keywords": ["银行"],
        "exclude": [],
        "extra_names": [],
    },
    "证券": {
        "keywords": ["证券", "券商"],
        "exclude": [],
        "extra_names": [],
    },
    "保险": {
        "keywords": ["保险"],
        "exclude": [],
        "extra_names": ["中国平安", "中国人寿", "中国人保", "中国太保", "新华保险", "天茂集团"],
    },
    "信托": {
        "keywords": ["信托"],
        "exclude": [],
        "extra_names": ["陕国投A", "安信信托", "中航产融"],
    },
    "期货": {
        "keywords": ["期货"],
        "exclude": [],
        "extra_names": ["永安期货", "南华期货", "瑞达期货"],
    },
    "互联网金融": {
        "keywords": ["互联网金融", "网络金融"],
        "exclude": [],
        "extra_names": ["东方财富", "同花顺", "恒生电子", "金证股份"],
    },
    "金融科技": {
        "keywords": ["金融科技", "金融IT", "FinTech"],
        "exclude": [],
        "extra_names": ["恒生电子", "东方财富", "同花顺", "金证股份", "宇信科技"],
    },
    "创投": {
        "keywords": ["创投", "创业投资", "风险投资", "PE", "VC"],
        "exclude": ["创投ETF"],
        "extra_names": ["鲁信创投", "创业黑马", "九鼎投资"],
    },
    "参股金融": {
        "keywords": ["参股金融"],
        "exclude": [],
        "extra_names": [],
    },
    "融资租赁": {
        "keywords": ["融资租赁", "租赁"],
        "exclude": [],
        "extra_names": [],
    },
    "消费金融": {
        "keywords": ["消费金融"],
        "exclude": [],
        "extra_names": [],
    },

    # === 地产 / 基建 ===
    "房地产": {
        "keywords": ["房地产", "地产", "置业"],
        "exclude": ["物业管理", "房产服务"],
        "extra_names": ["万科A", "保利发展", "招商蛇口", "碧桂园", "龙湖集团"],
    },
    "地产开发": {
        "keywords": ["地产开发", "房地产开发"],
        "exclude": [],
        "extra_names": [],
    },
    "园区开发": {
        "keywords": ["园区开发", "产业园", "工业园"],
        "exclude": [],
        "extra_names": [],
    },
    "物业服务": {
        "keywords": ["物业", "物业管理", "物业服务"],
        "exclude": [],
        "extra_names": ["碧桂园服务", "保利物业", "万物云", "招商积余"],
    },
    "基建": {
        "keywords": ["基建", "基础建设", "基础设施建设"],
        "exclude": [],
        "extra_names": ["中国中铁", "中国铁建", "中国交建", "中国电建", "中国建筑", "中国能建"],
    },
    "工程机械": {
        "keywords": ["工程机械", "挖掘机", "起重机", "混凝土机械"],
        "exclude": [],
        "extra_names": ["三一重工", "徐工机械", "中联重科", "柳工"],
    },
    "高铁": {
        "keywords": ["高铁", "高速铁路"],
        "exclude": [],
        "extra_names": ["中国中车", "中国中铁", "中国铁建"],
    },
    "轨道交通": {
        "keywords": ["轨道交通", "地铁", "城轨"],
        "exclude": [],
        "extra_names": ["中国中车", "中国中铁", "中国交建"],
    },
    "铁路": {
        "keywords": ["铁路"],
        "exclude": [],
        "extra_names": [],
    },
    "公路": {
        "keywords": ["公路", "高速"],
        "exclude": ["高速公路"],
        "extra_names": [],
    },
    "桥梁": {
        "keywords": ["桥梁", "桥梁工程"],
        "exclude": [],
        "extra_names": [],
    },
    "建筑设计": {
        "keywords": ["建筑设计", "建筑设计院", "设计院", "建筑设计咨询"],
        "exclude": ["室内设计", "平面设计"],
        "extra_names": ["华东建筑", "华建集团", "启迪设计"],
    },
    "装饰装修": {
        "keywords": ["装饰装修", "装修", "装饰"],
        "exclude": [],
        "extra_names": ["金螳螂", "亚厦股份", "东易日盛", "全筑股份"],
    },
    "水利工程": {
        "keywords": ["水利工程", "水利", "水务工程"],
        "exclude": [],
        "extra_names": ["中国电建", "中国能建", "粤水电"],
    },
    "环保工程": {
        "keywords": ["环保工程", "环境工程", "环境治理"],
        "exclude": [],
        "extra_names": [],
    },
    "园林工程": {
        "keywords": ["园林", "园林绿化", "景观"],
        "exclude": [],
        "extra_names": ["东方园林", "岭南股份", "棕榈股份"],
    },
    "PPP": {
        "keywords": ["PPP", "政府和社会资本合作"],
        "exclude": [],
        "extra_names": [],
    },
    "REITs": {
        "keywords": ["REITs", "REIT", "基础设施基金"],
        "exclude": [],
        "extra_names": [],
    },

    # === 区域 / 政策 ===
    "一带一路": {
        "keywords": ["一带一路"],
        "exclude": [],
        "extra_names": ["中国交建", "中国铁建", "中国中铁", "中国电建", "北方国际"],
    },
    "雄安新区": {
        "keywords": ["雄安"],
        "exclude": [],
        "extra_names": [],
    },
    "粤港澳大湾区": {
        "keywords": ["粤港澳大湾区", "大湾区"],
        "exclude": [],
        "extra_names": [],
    },
    "长三角": {
        "keywords": ["长三角"],
        "exclude": [],
        "extra_names": [],
    },
    "京津冀": {
        "keywords": ["京津冀"],
        "exclude": [],
        "extra_names": [],
    },
    "海南自贸": {
        "keywords": ["海南自贸", "海南自由贸"],
        "exclude": [],
        "extra_names": [],
    },
    "海南免税": {
        "keywords": ["海南免税"],
        "exclude": [],
        "extra_names": ["中国中免", "王府井", "海南发展"],
    },
    "新疆板块": {
        "keywords": ["新疆"],
        "exclude": [],
        "extra_names": [],
    },
    "西藏板块": {
        "keywords": ["西藏"],
        "exclude": [],
        "extra_names": [],
    },
    "西部大开发": {
        "keywords": ["西部大开发"],
        "exclude": [],
        "extra_names": [],
    },
    "东北振兴": {
        "keywords": ["东北振兴"],
        "exclude": [],
        "extra_names": [],
    },
    "乡村振兴": {
        "keywords": ["乡村振兴"],
        "exclude": [],
        "extra_names": [],
    },
    "共同富裕": {
        "keywords": ["共同富裕"],
        "exclude": [],
        "extra_names": [],
    },
    "国企改革": {
        "keywords": ["国企改革"],
        "exclude": [],
        "extra_names": [],
    },
    "央企改革": {
        "keywords": ["央企改革"],
        "exclude": [],
        "extra_names": [],
    },
    "中字头": {
        "keywords": ["中国"],
        "exclude": ["中国中免", "中国人寿"],
        "extra_names": [],
    },
    "国资重组": {
        "keywords": ["国资重组"],
        "exclude": [],
        "extra_names": [],
    },
    "混改": {
        "keywords": ["混改", "混合所有制"],
        "exclude": [],
        "extra_names": [],
    },
    "股权转让": {
        "keywords": ["股权转让"],
        "exclude": [],
        "extra_names": [],
    },
    "借壳上市": {
        "keywords": ["借壳"],
        "exclude": [],
        "extra_names": [],
    },
    "壳资源": {
        "keywords": ["壳资源", "ST"],
        "exclude": [],
        "extra_names": [],
    },

    # === 指数 / 资金 ===
    "ST板块": {
        "keywords": ["ST"],
        "exclude": [],
        "extra_names": [],
    },
    "次新股": {
        "keywords": ["次新"],
        "exclude": [],
        "extra_names": [],
    },
    "沪深300": {
        "keywords": [],
        "exclude": [],
        "extra_names": [],  # 需要指数成分列表
    },
    "破净股": {
        "keywords": [],  # 动态计算，无法静态匹配
        "exclude": [],
        "extra_names": [],
    },
    "股份回购": {
        "keywords": ["回购"],
        "exclude": [],
        "extra_names": [],
    },
    "高管增持": {
        "keywords": ["增持"],
        "exclude": [],
        "extra_names": [],
    },
    "股权激励": {
        "keywords": ["股权激励", "员工激励"],
        "exclude": [],
        "extra_names": [],
    },
    "员工持股": {
        "keywords": ["员工持股"],
        "exclude": [],
        "extra_names": [],
    },
    "并购重组": {
        "keywords": ["并购重组", "并购", "重组"],
        "exclude": [],
        "extra_names": [],
    },
    "资产注入": {
        "keywords": ["资产注入"],
        "exclude": [],
        "extra_names": [],
    },

    # === 环保细分 ===
    "固废处理": {
        "keywords": ["固废", "固体废物", "固废处理"],
        "exclude": [],
        "extra_names": ["瀚蓝环境", "绿色动力", "旺能环境"],
    },
    "污水处理": {
        "keywords": ["污水处理", "污水", "水务"],
        "exclude": [],
        "extra_names": ["碧水源", "创业环保", "兴蓉环境", "重庆水务"],
    },
    "大气治理": {
        "keywords": ["大气治理", "脱硫脱硝", "除尘"],
        "exclude": [],
        "extra_names": ["龙净环保", "菲达环保", "清新环境"],
    },
    "土壤修复": {
        "keywords": ["土壤修复", "土壤治理"],
        "exclude": [],
        "extra_names": ["高能环境", "永清环保", "博世科"],
    },
    "危废处理": {
        "keywords": ["危废", "危险废物", "危险废弃物"],
        "exclude": [],
        "extra_names": ["高能环境", "瀚蓝环境", "东江环保"],
    },
    "垃圾焚烧": {
        "keywords": ["垃圾焚烧", "垃圾发电", "垃圾处理"],
        "exclude": [],
        "extra_names": ["绿色动力", "瀚蓝环境", "旺能环境", "伟明环保"],
    },
    "环卫": {
        "keywords": ["环卫", "环卫服务"],
        "exclude": [],
        "extra_names": ["侨银股份", "盈峰环境", "玉禾田"],
    },
    "节能环保": {
        "keywords": ["节能环保", "节能减排"],
        "exclude": [],
        "extra_names": [],
    },
    "绿色建筑": {
        "keywords": ["绿色建筑", "绿色建材"],
        "exclude": [],
        "extra_names": [],
    },
    "节能照明": {
        "keywords": ["节能照明", "LED照明", "LED灯"],
        "exclude": [],
        "extra_names": ["欧普照明", "三安光电", "佛山照明", "木林森"],
    },
    "LED": {
        "keywords": ["LED"],
        "exclude": [],
        "extra_names": ["三安光电", "木林森", "国星光电", "鸿利智汇", "聚飞光电"],
    },
    "燃气": {
        "keywords": ["燃气", "天然气"],
        "exclude": [],
        "extra_names": [],
    },
    "火电": {
        "keywords": ["火电", "火力发电"],
        "exclude": [],
        "extra_names": ["华能国际", "国电电力", "华电国际", "大唐发电"],
    },
    "水电": {
        "keywords": ["水电", "水力发电"],
        "exclude": [],
        "extra_names": ["长江电力", "华能水电", "川投能源", "国投电力"],
    },
    "核电": {
        "keywords": ["核电", "核能"],
        "exclude": [],
        "extra_names": ["中国核电", "中国广核", "大唐发电"],
    },
    "生物质能": {
        "keywords": ["生物质", "生物质能", "生物质发电"],
        "exclude": [],
        "extra_names": [],
    },
    "地热能": {
        "keywords": ["地热", "地热能", "地热发电"],
        "exclude": [],
        "extra_names": [],
    },
    "光热发电": {
        "keywords": ["光热发电", "光热", "太阳能热发电"],
        "exclude": ["光热"],
        "extra_names": [],
    },
    "公共交通": {
        "keywords": ["公共交通", "公交", "客运"],
        "exclude": [],
        "extra_names": [],
    },
    "垃圾分类": {
        "keywords": ["垃圾分类"],
        "exclude": [],
        "extra_names": [],
    },
    "再生资源": {
        "keywords": ["再生资源", "资源回收", "废钢", "废铝"],
        "exclude": [],
        "extra_names": ["格林美", "中再资环", "华宏科技", "怡球资源"],
    },
    "循环经济": {
        "keywords": ["循环经济"],
        "exclude": [],
        "extra_names": [],
    },

    # === 农业 ===
    "农林牧渔": {
        "keywords": ["农业", "农", "林", "牧", "渔"],
        "exclude": ["农药", "农用机械"],
        "extra_names": [],
    },
    "粮食安全": {
        "keywords": ["粮食安全", "粮食"],
        "exclude": [],
        "extra_names": [],
    },
    "种业": {
        "keywords": ["种业", "种子", "育种", "制种"],
        "exclude": [],
        "extra_names": ["隆平高科", "大北农", "登海种业", "荃银高科", "万向德农", "神农科技"],
    },
    "转基因": {
        "keywords": ["转基因"],
        "exclude": [],
        "extra_names": ["大北农", "隆平高科", "登海种业"],
    },
    "粮食种植": {
        "keywords": ["粮食种植", "种植"],
        "exclude": [],
        "extra_names": [],
    },
    "蔬菜种植": {
        "keywords": ["蔬菜", "蔬果"],
        "exclude": [],
        "extra_names": [],
    },
    "水果种植": {
        "keywords": ["水果", "果业"],
        "exclude": [],
        "extra_names": [],
    },
    "木材": {
        "keywords": ["木材", "木业", "板材", "人造板"],
        "exclude": ["本钢", "钢铁"],
        "extra_names": ["丰林集团", "兔宝宝", "大亚圣象", "皮阿诺"],
    },
    "生猪养殖": {
        "keywords": ["生猪养殖", "生猪", "养猪", "猪业"],
        "exclude": [],
        "extra_names": ["牧原股份", "温氏股份", "新希望", "正邦科技", "天邦食品", "大北农", "天康生物"],
    },
    "猪肉概念": {
        "keywords": ["猪肉", "猪"],
        "exclude": [],
        "extra_names": ["牧原股份", "温氏股份", "新希望", "正邦科技", "天邦食品"],
    },
    "肉鸡": {
        "keywords": ["肉鸡", "白羽鸡", "禽业", "禽肉"],
        "exclude": [],
        "extra_names": ["温氏股份", "圣农发展", "益生股份", "民和股份", "仙坛股份"],
    },
    "蛋鸡": {
        "keywords": ["蛋鸡", "鸡蛋"],
        "exclude": [],
        "extra_names": ["晓鸣股份", "益生股份"],
    },
    "水产养殖": {
        "keywords": ["水产养殖", "水产", "养殖"],
        "exclude": [],
        "extra_names": ["国联水产", "大湖股份", "百洋股份", "獐子岛"],
    },
    "兽药": {
        "keywords": ["兽药", "动物保健", "动保"],
        "exclude": [],
        "extra_names": ["瑞普生物", "生物股份", "中牧股份", "海利生物"],
    },
    "动物疫苗": {
        "keywords": ["动物疫苗", "兽用疫苗", "禽流感疫苗"],
        "exclude": [],
        "extra_names": ["生物股份", "中牧股份", "瑞普生物", "海利生物", "普莱柯"],
    },
}

for concept, rule in NAME_BOARDS.items():
    CONCEPT_RULES[concept] = {
        "type": "name",
        **rule,
    }


# ========== 3. 生成映射 ==========
print(f"\n概念匹配规则已定义: {len(CONCEPT_RULES)} 个")

# 加载CSV概念清单
csv_path = r"C:\Users\13512\Downloads\A 股全部概念板块清单.csv"
csv_concepts = []
with open(csv_path, encoding="utf-8-sig") as f:
    reader = csv.reader(f)
    header = next(reader)
    for row in reader:
        if row:
            csv_concepts.append(row[0])

print(f"CSV概念清单: {len(csv_concepts)} 个")

# 检查未覆盖的概念
covered = set(CONCEPT_RULES.keys())
csv_set = set(csv_concepts)
uncovered = csv_set - covered
extra = covered - csv_set

if uncovered:
    print(f"\n⚠️ 未定义规则的概念 ({len(uncovered)} 个):")
    for c in sorted(uncovered):
        print(f"  - {c}")

if extra:
    print(f"\n规则中多余的（CSV没有）({len(extra)} 个):")
    for c in sorted(extra):
        print(f"  - {c}")

# ========== 4. 执行匹配 ==========
board_stock_map = {}
skipped = []

for concept in csv_concepts:
    if concept not in CONCEPT_RULES:
        # 跳过无法匹配的概念
        skipped.append(concept)
        continue

    rule = CONCEPT_RULES[concept]
    if rule["type"] == "industry":
        codes = match_by_industry(rule["industries"])
    elif rule["type"] == "name":
        keywords = rule.get("keywords", [])
        exclude_kw = rule.get("exclude", [])
        extra_names = rule.get("extra_names", [])
        if keywords or extra_names:
            codes = match_by_name(keywords, exclude_kw, extra_names)
        else:
            # 无法静态匹配的概念（如指数成分、破净股）
            skipped.append(f"{concept}(无静态匹配)")
            continue
    else:
        skipped.append(f"{concept}(未知类型)")
        continue

    if codes:
        board_stock_map[concept] = codes

print(f"\n成功匹配: {len(board_stock_map)} 个概念板块")
print(f"跳过: {len(skipped)} 个概念")

if skipped:
    print("\n跳过的概念:")
    for s in skipped:
        print(f"  - {s}")

# 统计
total_stocks = set()
for codes in board_stock_map.values():
    total_stocks.update(codes)
print(f"\n覆盖股票总数（去重）: {len(total_stocks)} 只")

# 各板块成分股数量统计
print("\n=== 各概念板块成分股数量 ===")
count_list = [(name, len(codes)) for name, codes in board_stock_map.items()]
count_list.sort(key=lambda x: x[1], reverse=True)

# 分段统计
for name, count in count_list[:30]:
    print(f"  {name}: {count} 只")
if len(count_list) > 30:
    print(f"  ... (还有 {len(count_list)-30} 个)")
    for name, count in count_list[-10:]:
        print(f"  {name}: {count} 只")

# 零匹配的概念
zero_match = [(name, len(codes)) for name, codes in board_stock_map.items() if len(codes) == 0]
if zero_match:
    print(f"\n⚠️ 零匹配的概念 ({len(zero_match)} 个):")
    for name, _ in zero_match:
        print(f"  - {name}")

# ========== 5. 保存 ==========
output_path = DATA_DIR / "concept_board_stock_map.json"
with open(output_path, "w", encoding="utf-8") as f:
    json.dump(board_stock_map, f, ensure_ascii=False, indent=2)
print(f"\n概念板块映射已保存: {output_path}")
