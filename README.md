# 板块竞价监控 - Tushare方案

## 项目说明

基于 **Tushare + AKShare** 的A股板块集合竞价成交额监控系统。

Tushare 提供 9:25~9:29 的个股集合竞价数据（`stk_auction`），结合 AKShare 的板块成分股数据，实现板块级别的竞价成交额汇总和排行。

## 安装依赖

```bash
pip install -r requirements.txt
```

## 配置 Tushare Token

1. 注册 [Tushare](https://tushare.pro) 账号
2. 在个人中心获取 API Token
3. 编辑 `config.py` 填入你的 Token

## 启动

```bash
# 命令行模式 - 采集今日竞价数据
python main.py --date today

# 命令行模式 - 采集指定日期
python main.py --date 20260503

# 命令行模式 - 采集最近5天
python main.py --days 5

# Web 查看模式
python web_app.py
```

访问 http://localhost:5000 查看竞价数据面板。

## 文件结构

```
board-auction-monitor/
├── config.py            # 配置文件（Tushare Token 等）
├── main.py              # 命令行入口
├── web_app.py           # Flask Web 服务
├── auction_monitor.py   # 核心逻辑：竞价数据采集+板块汇总
├── board_data.py        # 板块成分股数据管理
├── requirements.txt     # Python 依赖
├── data/                # 数据存储目录
│   ├── auction/         # 每日竞价数据（CSV）
│   └── board_summary/   # 板块汇总数据（CSV + Excel）
├── templates/           # Flask 模板
│   └── index.html       # Web 面板页面
└── static/              # 静态资源
```

## 功能特性

- 实时采集 9:25 集合竞价数据（需在 9:25~15:00 之间运行）
- 支持历史数据回填
- 板块成交额 Top N 排行
- 同比/环比分析
- Web 可视化面板
- 定时自动采集（通过 APScheduler）
