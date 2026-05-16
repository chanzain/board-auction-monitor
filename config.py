"""
配置文件
"""

# ==================== Tushare 配置 ====================
# 注册地址: https://tushare.pro/register
# 获取Token: https://tushare.pro/user/token
TUSHARE_TOKEN = "90212db45133063c9cc4e7fa5dc9b12c4c0ca01a6a89aa09fb6b710b"

# ==================== 关注板块 ====================
# 主流板块名称（与 data/concept_board_stock_map.json 的 key 保持一致）
# 当使用概念板块模式时，从 concept_board_stock_map.json 自动读取所有概念
FOCUS_BOARDS = []  # 留空表示自动加载全部概念板块

# ==================== 采集配置 ====================
# 每批次请求间隔（秒），避免触发 Tushare 限流
REQUEST_INTERVAL = 0.5

# 单次 Web/API 请求最多新拉取多少只股票的日线 MA5（其余写入缓存后下次继续补全）
MA5_MAX_FILL_PER_REQUEST = 150

# 板块成分股弹窗：默认仅读本地 MA5 缓存（0=不发起网络请求，秒开列表）。
# 前端可在后台再请求 ma5_fill=N 渐进补全；单次网络补全上限见 MA5_BOARD_STOCKS_MAX_FILL。
MA5_BOARD_STOCKS_DEFAULT_FILL = 0

# 成分股弹窗后台补全五日线时，单次最多新拉取多少只（其余下次继续）
MA5_BOARD_STOCKS_MAX_FILL = 80

# 并发请求数
MAX_CONCURRENT = 3

# 排行榜显示数量
TOP_N = 30

# ==================== Web 服务配置 ====================
WEB_HOST = "0.0.0.0"
WEB_PORT = 5000

# ==================== 定时任务配置 ====================
# 是否开启自动采集
AUTO_FETCH_ENABLED = False

# 每日采集时间（HH:MM）
DAILY_FETCH_TIME = "09:30"
