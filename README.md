# A股量化交易系统

个人A股程序化交易系统。20万本金，Python技术栈，国金MiniQMT。

## 架构

```
数据层: AkShare → ClickHouse(行情) + PostgreSQL(业务)
策略层: 因子库 → 选股器 → 择时策略
回测层: 事件驱动引擎 + A股模拟券商(T+1/涨跌停/手续费)
风控层: 仓位管理 + 熔断器 + 止损
执行层: QMT API + 钉钉通知
监控层: Streamlit面板
```

## 项目结构（68个文件）

```
config/             settings.py, risk_params.py       全局配置
data/
  collector/        akshare_fetcher.py, scheduler.py  行情采集
  models/           bar.py                            数据模型（不可变）
  storage/          clickhouse_client.py              存储层
strategy/
  base/             strategy_template.py              策略基类
  factors/          momentum/volatility/turnover/     4类因子
  selector/         stock_selector.py                 IC加权多因子选股
  timing/           trend_follow.py, mean_revert.py   择时策略
backtest/
  broker_sim.py    A股模拟券商(T+1/涨跌停/千一印花税)
  engine.py        事件驱动回测引擎
  reporter.py      回测报告(夏普/最大回撤/胜率/盈亏比)
risk/
  position_mgr.py  仓位管理器
  circuit_breaker.py 熔断器(日亏损/连续亏损)
  risk_engine.py   风控引擎(统一检查入口)
live/               QMT实盘适配(Phase 5)
monitor/            Streamlit可视化面板
notify/             钉钉/企业微信推送
scripts/
  init_db.sql      数据库DDL
  download_history.py  历史数据批量下载
  daily_signal.py  每日信号生成
  run_backtest.py  回测入口
tests/              96个测试，4个测试文件
```

## 设计决策

1. **不可变数据** — 所有核心模型使用`@dataclass(frozen=True)`，永不原地修改
2. **限仓不拒单** — PositionManager超标时自动削减预算，不直接拒绝
3. **事件驱动回测** — 按交易日逐日推进：T+1交收→更新市价→策略信号→风控→执行
4. **等权回退** — IC权重不可用时自动回退到等权打分
5. **位置管理器设计** — MiniQMT优先，XTP(300万门槛)放弃，PTrade(云端代码)放弃
6. **风控硬约束**: 单票≤20% 日亏损2%熔断 连续3天亏损暂停 止损-5%

## 快速开始

```bash
# 1. 启动数据库
docker compose up -d

# 2. 安装依赖
pip install -r requirements.txt

# 3. 初始化PG表
python -c "from data.storage.postgres_client import get_postgres_client; ..."

# 4. 下载历史数据（2-3小时）
python scripts/download_history.py

# 5. 运行测试
python -m pytest tests/ -v

# 6. 启动面板
streamlit run monitor/app.py

# 7. 跑回测
python scripts/run_backtest.py all
```

## 测试覆盖

| 文件 | 测试数 | 覆盖范围 |
|------|--------|---------|
| test_data_collector.py | 23 | 数据模型、AkShare、Cleaner、ClickHouse、PG、风控配置 |
| test_factors.py | 26 | 动量/波动率/换手率/技术因子 + 选股器 + IC分析 |
| test_backtest.py | 23 | 模拟券商(T+1/手续费/涨跌停) + 引擎 + 趋势/均值回归策略 + 报告 |
| test_risk.py | 24 | 仓位管理 + 熔断器 + 风控引擎 + 通知推送 |

## 券商状态

- [ ] 联系国金证券客户经理开通MiniQMT（10万门槛，20万达标）
- [ ] 备选银河证券
- [ ] QMT仿真环境注册
