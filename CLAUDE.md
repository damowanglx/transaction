# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Architecture

Pipeline order: **Data → Factors → Selector → Backtest → Risk → Execute → Monitor**

```
AkShare → ClickHouse(行情) + PostgreSQL(业务)
  → strategy/factors/ (momentum, volatility, turnover, technical)
  → strategy/selector/ (IC-weighted multi-factor ranking)
  → strategy/timing/ (trend_follow, mean_revert)
  → backtest/ (event-driven engine + A-share broker sim)
  → risk/ (position_mgr → circuit_breaker → risk_engine)
  → live/ (QMT adapter — Phase 5, pending broker account)
  → monitor/ (Streamlit dashboard)
  → notify/ (DingTalk, WeChat webhooks)
```

## Design invariants

1. **All data models use `@dataclass(frozen=True)`** — never mutate, always return new instances
2. **PositionManager caps, never rejects** — when budget exceeds position limit, reduce to max allowed, don't error
3. **Equal-weight fallback** — `StockSelector._score_stocks()` defaults to 1/N weights when IC unavailable
4. **A-share rules in broker sim**: T+1 settlement (shares locked 1 day after buy), ±10% price limits (20% for 688/300), 100-share lots, 千一 stamp tax on sell only, 万三 commission min ¥5
5. **Event-driven backtest loop**: settle T+1 → update prices → run strategy → risk check → execute → record P&L
6. **Factor IC ≥ 0.02 minimum** — below this, factor weight set to 0 in selector

## Commands

```bash
# Infrastructure
docker compose up -d                          # Start ClickHouse + PostgreSQL

# Tests
python -m pytest tests/ -v                    # All 96 tests
python -m pytest tests/test_factors.py -v     # Single file
python -m pytest tests/ -v -k "test_buy"      # By keyword
python -m pytest tests/ -v -m "not integration"  # Skip integration (no DB needed)

# Data
python scripts/download_history.py            # Download 3yr history (2-3 hours, ~5500 stocks)

# Run
python scripts/run_backtest.py trend_follow   # Backtest one strategy
python scripts/run_backtest.py all            # Both strategies
python scripts/daily_signal.py                # Generate today's signals
streamlit run monitor/app.py                  # Dashboard at localhost:8501

# PostgreSQL table init (one-time, after first docker compose up)
python -c "
from data.storage.postgres_client import get_postgres_client
from sqlalchemy import text
pg = get_postgres_client()
with pg.engine.connect() as conn:
    conn.execute(text('CREATE TABLE IF NOT EXISTS trade_records (...);'))
    conn.execute(text('CREATE TABLE IF NOT EXISTS daily_pnl (...);'))
    conn.execute(text('CREATE TABLE IF NOT EXISTS risk_events (...);'))
    conn.execute(text('CREATE TABLE IF NOT EXISTS strategy_signals (...);'))
    conn.commit()
"
```

## Key patterns

- **Singleton clients**: `get_clickhouse_client()`, `get_postgres_client()` — lazy-init, reuse across modules
- **Strategy contract**: inherit `BaseStrategy`, implement `init(**params)` + `on_data(data, current_date) → list[Signal]`
- **Risk pipeline**: `RiskEngine.check_order()` runs breaker → stop-loss → position check in order, fast-fails
- **Test marks**: `@pytest.mark.integration` for tests needing DB, `@pytest.mark.unit` for pure logic
- **Synthetic data helpers**: `_make_price_series()`, `_make_ohlcv_df()`, `_make_multi_stock_df()` in test files

## Recent fixes (2026-07-04)

- **record_day fix**: Removed duplicate `record_day()` call in backtest/engine.py (was called twice per day, doubling consecutive loss count)
- **PositionMgr weight fix**: Fixed denominator in target_weight calc (was `total_value + budget`, now `total_value` since budget comes from cash already in total_value)
- **CircuitBreaker refactor**: Separated state mutation into `_trip()` helper, clarified mutation boundaries
- **trader.py hardening**: `total_value` now estimated from positions MV + cash; only executed orders saved to positions.json
- **OHLCV fallback removed**: Daily signal script no longer synthesizes fake OHLCV data on query failure
- **Atomic writes**: `atomic_write_json()` in config/settings.py; all position saves use tmp→rename pattern
- **Optimizer diversity**: Time-based seed instead of fixed `random.seed(42)`
- **DingTalk batching**: `send_batch_signals()` sends all signals in one API call
- **WeChat integration**: WeChat notifier wired into daily_signal.py
- **Pre-market gap check**: `scripts/pre_market_check.py` checks held positions for overnight gaps
- **Dashboard fixes**: No more fake equity data, save button actually persists config, status auto-detects DB
- **Weekly resample**: `"W"` → `"W-FRI"` for China market week endings
- **Precomputed flag leak**: Per-stock `use_precomputed` instead of looping `_precomputed` mutation
- **Param snapshots**: `scripts/snapshot_params.py` save/list/diff for tracking strategy parameter changes
- **Risk/strategy alignment**: `max_holdings_count` now 10 (matches strategy `top_n=10`)

## Key scripts

| Script | Purpose |
|--------|---------|
| `scripts/daily_signal.py` | Generate daily trading signals (use ClickHouse) |
| `scripts/daily_signal_qmt.py` | Generate signals from QMT data |
| `scripts/pre_market_check.py` | Overnight gap risk check before market open |
| `scripts/snapshot_params.py` | Save/list/diff parameter snapshots |
| `scripts/optimizer.py` | Grid search + genetic algorithm parameter optimization |
| `scripts/run_backtest.py` | Run backtest for one or all strategies |
| `scripts/validate.py` | Multi-sample validation (5 runs × 1000 stocks) |
| `scripts/segment_test.py` | Market regime segment backtests |
| `scripts/download_history.py` | Download 3yr history from AkShare |
| `scripts/download_incremental.py` | Incremental data update |

## Test file mapping

| Test file | Covers | DB needed |
|-----------|--------|-----------|
| `test_data_collector.py` | DailyBar model, AkShare, Cleaner, CH/PG clients, RiskConfig | CH+PG |
| `test_factors.py` | 4 factor groups, StockSelector, Signal, IC analysis | No |
| `test_backtest.py` | BrokerSim, BacktestEngine, TrendFollow, MeanRevert, Reporter | No |
| `test_risk.py` | PositionManager, CircuitBreaker, RiskEngine, Notifications | No |

## Phase status (2026-06-29)

- ✅ Phase 1–4 complete, 96/96 tests passing
- ✅ Historical data: 5528 A-shares, 4.5M bars (2023-06 to 2026-06)
- ✅ Broker: GuoJin Securities account opened, 20-trading-day verification in progress (~4 weeks)
- ✅ Strategy: Mean reversion baseline confirmed (+41%, Sharpe 0.90, 2000-stock validated)
- 🔄 Final backtest running (all improvements: weekly filter, optimized risk params)
- ⏳ Phase 5 (QMT adapter) starts Week 3 of go-live plan

### 4-Week Go-Live Plan

| Week | Focus | Key Deliverable |
|------|-------|----------------|
| 1 (6/29-7/4) | Backtest validation | Param optimization + full-universe backtest |
| 2 (7/5-7/11) | Stress testing | Segment backtests + sample-out verification |
| 3 (7/12-7/18) | QMT integration | Paper trading 5 days zero errors |
| 4 (7/19-7/25) | Live prep | Small capital (1-2万) live test 2 weeks |

## Memory files

Cross-session context: `C:\Users\13981\.claude\projects\d--workspace-transaction\memory\`
- `project-overview.md` — architecture, tech stack, design rationale
- `project-status.md` — current phase, key commands
- `project-plan.md` — full plan reference, risk config
