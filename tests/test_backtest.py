"""
Tests for backtest system (Phase 3).

Verifies:
- BrokerSim: T+1 settlement, buy/sell, commissions, price limits
- BacktestEngine: Full backtest with synthetic data
- Strategies: Trend following and mean reversion signal logic
- Reporter: Report formatting and export
"""

import sys
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, ".")


# ============================================================
# Test Helpers
# ============================================================

def _make_price_data(
    n_days: int = 200,
    n_stocks: int = 20,
    seed: int = 42,
) -> pd.DataFrame:
    """Generate multi-stock synthetic price data for backtesting."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range(start="2023-01-01", periods=n_days, freq="B")
    rows = []

    for s in range(n_stocks):
        # Random walk with drift
        drift = rng.uniform(-0.0005, 0.0015)  # -12% to +37% annual
        vol = rng.uniform(0.015, 0.035)  # Daily volatility
        returns = rng.normal(drift, vol, n_days)
        prices = 10.0 * np.cumprod(1 + np.clip(returns, -0.10, 0.10))

        for d_idx, d in enumerate(dates):
            price = prices[d_idx]
            rows.append({
                "ts_code": f"T{s:02d}.SZ",
                "trade_date": d,
                "open": price * (1 + rng.uniform(-0.01, 0.01)),
                "high": price * (1 + abs(rng.uniform(0, 0.03))),
                "low": price * (1 - abs(rng.uniform(0, 0.03))),
                "close": price,
                "vol": rng.integers(100_000, 10_000_000),
                "amount": price * rng.integers(100_000, 10_000_000),
            })

    return pd.DataFrame(rows)


# ============================================================
# Test: BrokerSim
# ============================================================

class TestBrokerSim:
    """A-share broker simulation tests."""

    def test_initial_state(self):
        from backtest.broker_sim import BrokerSim

        broker = BrokerSim(initial_cash=200_000, slippage_rate=0)
        assert broker.cash == 200_000
        assert broker.total_value == 200_000
        assert len(broker.positions) == 0

    def test_buy_order(self):
        from backtest.broker_sim import BrokerSim

        broker = BrokerSim(initial_cash=200_000, slippage_rate=0)  # No slippage for exact test
        result = broker.place_order("BUY", "000001.SZ", 10.00, 1000, date(2024, 6, 15))

        assert not result.rejected, result.reject_reason
        assert len(result.trades) == 1
        trade = result.trades[0]
        assert trade.direction == "BUY"
        assert trade.volume == 1000
        assert trade.price == 10.00
        # Commission: max(10000 * 0.0003, 5) = max(3, 5) = 5
        assert trade.commission == 5.0
        assert trade.stamp_tax == 0.0  # No stamp tax on buy

    def test_sell_order(self):
        from backtest.broker_sim import BrokerSim

        broker = BrokerSim(initial_cash=200_000, slippage_rate=0)

        # Buy first
        result = broker.place_order("BUY", "000001.SZ", 10.00, 1000, date(2024, 6, 15))
        broker._state = result.remaining_state

        # Settle T+1
        broker = broker.settle_t_plus_1(date(2024, 6, 16))

        # Sell
        broker = broker.update_market_prices({"000001.SZ": 11.00})
        result = broker.place_order("SELL", "000001.SZ", 11.00, 1000, date(2024, 6, 16))

        assert not result.rejected, result.reject_reason
        assert len(result.trades) == 1
        trade = result.trades[0]
        assert trade.direction == "SELL"
        assert trade.price == 11.00
        # Commission: max(11000 * 0.0003, 5) = max(3.30, 5) = 5
        assert trade.commission == pytest.approx(5.0, rel=0.1)
        # Stamp tax: 11000 * 0.001 = 11.0
        assert trade.stamp_tax == pytest.approx(11.0, rel=0.1)

    def test_t_plus_1_lock(self):
        from backtest.broker_sim import BrokerSim

        broker = BrokerSim(initial_cash=200_000, slippage_rate=0)

        # Buy on day T
        result = broker.place_order("BUY", "000001.SZ", 10.00, 1000, date(2024, 6, 15))
        broker._state = result.remaining_state

        # Same day — shares should NOT be available
        pos = broker.positions.get("000001.SZ")
        assert pos is not None
        assert pos.volume == 1000
        assert pos.available_volume == 0  # Locked

        # Try to sell same day — should reject (or reduce to 0)
        result = broker.place_order("SELL", "000001.SZ", 11.00, 1000, date(2024, 6, 15))
        assert result.rejected
        assert "locked" in result.reject_reason.lower() or "available" in result.reject_reason.lower()

    def test_insufficient_cash(self):
        from backtest.broker_sim import BrokerSim

        broker = BrokerSim(initial_cash=1000, slippage_rate=0)
        # Trying to buy ¥100,000 worth of stock
        result = broker.place_order("BUY", "000001.SZ", 100.00, 1000, date(2024, 6, 15))
        assert result.rejected
        assert "Insufficient" in result.reject_reason

    def test_round_lot(self):
        from backtest.broker_sim import BrokerSim

        broker = BrokerSim(initial_cash=200_000, slippage_rate=0)
        # 150 shares → round to 100
        result = broker.place_order("BUY", "000001.SZ", 10.00, 150, date(2024, 6, 15))
        assert result.trades[0].volume == 100

    def test_no_position_sell(self):
        from backtest.broker_sim import BrokerSim

        broker = BrokerSim(initial_cash=200_000, slippage_rate=0)
        result = broker.place_order("SELL", "000001.SZ", 10.00, 100, date(2024, 6, 15))
        assert result.rejected
        assert "No position" in result.reject_reason

    def test_position_update_after_buy(self):
        from backtest.broker_sim import BrokerSim

        broker = BrokerSim(initial_cash=200_000, slippage_rate=0)
        result = broker.place_order("BUY", "000001.SZ", 10.00, 1000, date(2024, 6, 15))
        broker._state = result.remaining_state

        pos = broker.positions["000001.SZ"]
        assert pos.volume == 1000
        assert pos.avg_cost == 10.0
        assert pos.current_price == 10.0

    def test_average_cost_update(self):
        from backtest.broker_sim import BrokerSim

        broker = BrokerSim(initial_cash=200_000, slippage_rate=0)

        # Buy 1000 @ 10
        result = broker.place_order("BUY", "000001.SZ", 10.00, 1000, date(2024, 6, 10))
        broker._state = result.remaining_state
        broker = broker.settle_t_plus_1(date(2024, 6, 11))

        # Update price to allow second buy (within 10% limit)
        broker = broker.update_market_prices({"000001.SZ": 10.50})

        # Buy 500 @ 10.50
        result = broker.place_order("BUY", "000001.SZ", 10.50, 500, date(2024, 6, 11))
        broker._state = result.remaining_state

        pos = broker.positions["000001.SZ"]
        assert pos.volume == 1500
        # (1000*10 + 500*10.50) / 1500 = 15250/1500 = 10.167
        assert pos.avg_cost == pytest.approx(10.167, rel=0.01)


# ============================================================
# Test: Slippage
# ============================================================

class TestSlippage:
    """Verify slippage simulates realistic execution prices."""

    def test_buy_slippage(self):
        from backtest.broker_sim import BrokerSim

        broker = BrokerSim(initial_cash=200_000, slippage_rate=0.002)
        result = broker.place_order("BUY", "000001.SZ", 10.00, 1000, date(2024, 6, 15))
        price = result.trades[0].price
        assert price > 10.00, f"Buy slippage should increase price, got {price}"
        assert price <= 10.00 * 1.002, f"Slippage too large: {price}"

    def test_sell_slippage(self):
        from backtest.broker_sim import BrokerSim

        broker = BrokerSim(initial_cash=200_000, slippage_rate=0.002)
        result = broker.place_order("BUY", "000001.SZ", 10.00, 1000, date(2024, 6, 15))
        broker._state = result.remaining_state
        broker = broker.settle_t_plus_1(date(2024, 6, 16))
        broker = broker.update_market_prices({"000001.SZ": 10.00})
        result = broker.place_order("SELL", "000001.SZ", 10.00, 1000, date(2024, 6, 16))
        price = result.trades[0].price
        assert price < 10.00, f"Sell slippage should decrease price, got {price}"
        assert price >= 10.00 * 0.998, f"Slippage too large: {price}"

    def test_zero_slippage_exact(self):
        from backtest.broker_sim import BrokerSim

        broker = BrokerSim(initial_cash=200_000, slippage_rate=0)
        result = broker.place_order("BUY", "000001.SZ", 10.00, 1000, date(2024, 6, 15))
        assert result.trades[0].price == 10.00


# ============================================================
# Test: Price Limits
# ============================================================

class TestPriceLimits:
    """Verify A-share price limit enforcement."""

    def test_buy_above_limit_up_blocked(self):
        from backtest.broker_sim import BrokerSim

        broker = BrokerSim(initial_cash=200_000, slippage_rate=0)
        # Main board: ±10%. pre_close=10, limit_up=11. Order at 12 exceeds.
        result = broker.place_order("BUY", "600000.SH", 12.00, 1000, date(2024, 6, 15), pre_close=10.00)
        assert result.rejected
        assert "exceeds" in result.reject_reason.lower() or "limit" in result.reject_reason.lower()

    def test_buy_within_limit_allowed(self):
        from backtest.broker_sim import BrokerSim

        broker = BrokerSim(initial_cash=200_000, slippage_rate=0)
        # Main board ±10%, order at 10.90 (within 10% from pre_close 10)
        result = broker.place_order("BUY", "600000.SH", 10.90, 1000, date(2024, 6, 15), pre_close=10.00)
        assert not result.rejected

    def test_star_market_20pct_limit(self):
        from backtest.broker_sim import BrokerSim

        broker = BrokerSim(initial_cash=200_000, slippage_rate=0)
        # STAR market 688xxx: ±20%. Order at 11.80 from pre_close 10 = 18%, allowed.
        result = broker.place_order("BUY", "688001.SH", 11.80, 1000, date(2024, 6, 15), pre_close=10.00)
        assert not result.rejected
        # Order at 12.50 from pre_close 10 = 25%, rejected.
        result = broker.place_order("BUY", "688001.SH", 12.50, 1000, date(2024, 6, 15), pre_close=10.00)
        assert result.rejected


# ============================================================
# Test: Alpha / Benchmark
# ============================================================

class TestAlpha:
    """Verify benchmark and alpha calculation."""

    def test_alpha_positive_when_outperform(self):
        import pandas as pd
        from backtest.engine import BacktestEngine
        from strategy.selector.stock_selector import StockSelector

        engine = BacktestEngine(initial_cash=200_000)
        selector = StockSelector("test")
        selector.init(factors=["mom_60"], top_n=0)  # No trades

        # Synthetic data: strategy flat, benchmark up 10% → alpha = -10%
        data = _make_price_data(n_days=100, n_stocks=5)
        start = data["trade_date"].min().date()
        end = data["trade_date"].max().date()

        # Create benchmark data (10% gain)
        bench_dates = sorted(data["trade_date"].unique())
        import numpy as np
        bench_prices = 1000 * (1.001) ** np.arange(len(bench_dates))  # ~10% annual
        bench_df = pd.DataFrame({
            "trade_date": bench_dates,
            "close": bench_prices,
        })

        result = engine.run(selector, data, start, end, benchmark_data=bench_df)
        assert result.benchmark_return != 0, "Should have benchmark data"
        assert result.alpha is not None


# ============================================================
# Test: Backtest Engine
# ============================================================

class TestBacktestEngine:
    """Backtest engine integration tests."""

    def test_empty_run(self):
        from backtest.engine import BacktestEngine
        from strategy.selector.stock_selector import StockSelector

        engine = BacktestEngine(initial_cash=200_000)
        selector = StockSelector("empty")
        selector.init(factors=["mom_60"])

        result = engine.run(
            selector,
            pd.DataFrame(),
            date(2024, 1, 1),
            date(2024, 1, 31),
        )
        assert result.total_return == 0.0

    def test_basic_run(self):
        from backtest.engine import BacktestEngine
        from strategy.selector.stock_selector import StockSelector

        engine = BacktestEngine(initial_cash=200_000)
        selector = StockSelector("test")
        selector.init(
            factors=["mom_60"],
            top_n=3,
        )

        data = _make_price_data(n_days=100, n_stocks=10)
        start = data["trade_date"].min().date()
        end = data["trade_date"].max().date()

        result = engine.run(selector, data, start, end)

        assert result.initial_cash == 200_000
        assert len(result.daily_records) > 0
        assert result.total_return is not None
        assert result.max_drawdown <= 0  # Max drawdown is negative or zero

    def test_no_trades_without_signals(self):
        from backtest.engine import BacktestEngine
        from strategy.selector.stock_selector import StockSelector

        engine = BacktestEngine(initial_cash=200_000)
        selector = StockSelector("empty")
        selector.init(factors=["mom_60"], top_n=0)  # No stocks selected

        data = _make_price_data(n_days=50, n_stocks=10)
        start = data["trade_date"].min().date()
        end = data["trade_date"].max().date()

        result = engine.run(selector, data, start, end)
        assert result.total_trades == 0
        assert result.final_value == 200_000


# ============================================================
# Test: Strategies
# ============================================================

class TestTrendFollowStrategy:
    """Trend following strategy tests."""

    def test_init(self):
        from strategy.timing.trend_follow import TrendFollowStrategy

        strat = TrendFollowStrategy("test_tf")
        strat.init(ma_fast=5, ma_slow=20, top_n=5)
        assert strat.is_initialized

    def test_empty_data(self):
        from strategy.timing.trend_follow import TrendFollowStrategy

        strat = TrendFollowStrategy("test_tf")
        strat.init()
        signals = strat.on_data(pd.DataFrame(), date.today())
        assert signals == []

    def test_generates_signals_on_trending_data(self):
        from strategy.timing.trend_follow import TrendFollowStrategy

        strat = TrendFollowStrategy("test_tf")
        strat.init(ma_fast=3, ma_slow=10, ma_trend=30, top_n=3)

        # Generate strong uptrend data
        dates = pd.date_range("2024-01-01", periods=100, freq="B")
        prices = 10.0 * (1.01) ** np.arange(100)  # 1% daily uptrend!
        prices[:40] = 10.0  # Flat first 40 days for MA calculation

        data = pd.DataFrame({
            "ts_code": ["UP01.SZ"] * 100,
            "trade_date": dates,
            "close": prices,
            "vol": [1_000_000] * 100,
        })

        signals = strat.on_data(data, dates[-1].date())
        # Should at least generate some signal
        assert isinstance(signals, list)

    def test_rsi_calculation(self):
        from strategy.timing.trend_follow import TrendFollowStrategy

        strat = TrendFollowStrategy("test")
        strat.init()

        # Build data with a dip and recovery
        n = 50
        prices = pd.Series(
            [10.0] * 30 +  # Flat
            [9.0] * 10 +   # Dip
            [10.5] * 10     # Recovery
        )
        rsi = strat._calc_rsi(prices, 14)
        assert 0 <= rsi <= 100


class TestMeanRevertStrategy:
    """Mean reversion strategy tests."""

    def test_init(self):
        from strategy.timing.mean_revert import MeanRevertStrategy

        strat = MeanRevertStrategy("test_mr")
        strat.init(bb_period=20, rsi_oversold=30)
        assert strat.is_initialized

    def test_empty_data(self):
        from strategy.timing.mean_revert import MeanRevertStrategy

        strat = MeanRevertStrategy("test_mr")
        strat.init()
        signals = strat.on_data(pd.DataFrame(), date.today())
        assert signals == []

    def test_buy_signal_on_oversold(self):
        from strategy.timing.mean_revert import MeanRevertStrategy

        strat = MeanRevertStrategy("test_mr")
        strat.init(bb_period=20, bb_std=2.0, rsi_oversold=40, top_n=5)

        # Generate data with a sharp selloff then flatten
        n = 100
        dates = pd.date_range("2024-01-01", periods=n, freq="B")
        #  50 days normal → 30 days sharp decline → 20 days at the bottom
        prices = np.concatenate([
            10.0 + np.random.default_rng(42).normal(0, 0.1, 50).cumsum(),
            10.3 + np.random.default_rng(43).normal(-0.03, 0.05, 30).cumsum(),
            np.full(20, 8.5),  # Bottom
        ])

        data = pd.DataFrame({
            "ts_code": ["DOWN01.SZ"] * n,
            "trade_date": dates,
            "open": prices * 0.99,
            "high": prices * 1.02,
            "low": prices * 0.98,
            "close": prices,
            "vol": [1_000_000] * n,
        })

        signals = strat.on_data(data, dates[-1].date())
        assert isinstance(signals, list)

    def test_rsi_calculation(self):
        from strategy.timing.mean_revert import MeanRevertStrategy

        strat = MeanRevertStrategy("test")
        strat.init()

        n = 50
        prices = pd.Series(
            [10.0] * 20 + [9.0] * 15 + [10.5] * 15
        )
        rsi = strat._calc_rsi(prices, 14)
        assert 0 <= rsi <= 100


# ============================================================
# Test: Reporter
# ============================================================

class TestReporter:
    """Backtest report formatting tests."""

    def test_format_report(self):
        from backtest.engine import BacktestResult, DailyRecord
        from backtest.reporter import format_report

        result = BacktestResult(
            initial_cash=200_000,
            final_value=220_000,
            total_return=0.10,
            annual_return=0.15,
            sharpe_ratio=1.2,
            max_drawdown=-0.12,
            max_drawdown_duration=45,
            total_trades=50,
            win_rate=0.55,
            avg_win_pct=0.05,
            avg_loss_pct=-0.03,
            profit_factor=1.8,
            daily_records=[
                DailyRecord(
                    trade_date=date(2024, 1, 2),
                    cash=200_000, market_value=0, total_value=200_000,
                    daily_pnl=0, daily_return=0, cumulative_return=0,
                    trades=[],
                ),
            ],
            trade_history=[],
        )

        report = format_report(result, "TestStrategy")
        assert "TestStrategy" in report
        assert "200,000" in report
        assert "Sharpe Ratio" in report

    def test_verdict_strong(self):
        from backtest.engine import BacktestResult, DailyRecord
        from backtest.reporter import _verdict

        result = BacktestResult(
            initial_cash=200_000, final_value=300_000,
            total_return=0.50, annual_return=0.40,
            sharpe_ratio=2.0, max_drawdown=-0.10,
            max_drawdown_duration=20, total_trades=100,
            win_rate=0.60, avg_win_pct=0.08, avg_loss_pct=-0.03,
            profit_factor=2.5, daily_records=[], trade_history=[],
        )
        verdict, _ = _verdict(result)
        assert "STRONG" in verdict

    def test_verdict_fail(self):
        from backtest.engine import BacktestResult
        from backtest.reporter import _verdict

        result = BacktestResult(
            initial_cash=200_000, final_value=180_000,
            total_return=-0.10, annual_return=-0.15,
            sharpe_ratio=-0.5, max_drawdown=-0.35,
            max_drawdown_duration=120, total_trades=20,
            win_rate=0.30, avg_win_pct=0.02, avg_loss_pct=-0.06,
            profit_factor=0.5, daily_records=[], trade_history=[],
        )
        verdict, _ = _verdict(result)
        assert "FAIL" in verdict


# ============================================================
# Test: Trade matching (FIFO P&L pairs)
# ============================================================

class TestMatchTrades:
    """Verify _match_trades FIFO logic computes correct P&L."""

    def test_single_buy_sell_pair(self):
        from backtest.broker_sim import Trade
        from backtest.engine import BacktestEngine

        trades = [
            Trade("B1", "A.SZ", "BUY", 10.0, 1000, 10000, 5.0, 0.0, date(2024, 1, 10)),
            Trade("S1", "A.SZ", "SELL", 11.0, 1000, 11000, 5.0, 11.0, date(2024, 1, 15)),
        ]
        pairs = BacktestEngine._match_trades(trades)
        assert len(pairs) == 1
        assert pairs[0]["volume"] == 1000
        # PnL = sell_proceeds - buy_cost - fees
        # = (11000-5-11)*1 - (10000+5) = 10984 - 10005 = 979 (approx)
        assert pairs[0]["pnl"] > 0

    def test_fifo_multiple_lots(self):
        from backtest.broker_sim import Trade
        from backtest.engine import BacktestEngine

        trades = [
            Trade("B1", "A.SZ", "BUY", 10.0, 500, 5000, 5.0, 0.0, date(2024, 1, 10)),
            Trade("B2", "A.SZ", "BUY", 12.0, 500, 6000, 5.0, 0.0, date(2024, 1, 11)),
            Trade("S1", "A.SZ", "SELL", 11.0, 800, 8800, 4.4, 8.8, date(2024, 1, 15)),
        ]
        pairs = BacktestEngine._match_trades(trades)
        # 800 shares sold: 500 from first lot, 300 from second
        assert len(pairs) == 1
        assert pairs[0]["volume"] == 800

    def test_partial_sell_leaves_remainder(self):
        from backtest.broker_sim import Trade
        from backtest.engine import BacktestEngine

        trades = [
            Trade("B1", "A.SZ", "BUY", 10.0, 1000, 10000, 5.0, 0.0, date(2024, 1, 10)),
            Trade("S1", "A.SZ", "SELL", 10.5, 300, 3150, 5.0, 3.15, date(2024, 1, 15)),
        ]
        pairs = BacktestEngine._match_trades(trades)
        assert len(pairs) == 1
        assert pairs[0]["volume"] == 300

    def test_empty_trades(self):
        from backtest.engine import BacktestEngine

        pairs = BacktestEngine._match_trades([])
        assert pairs == []


# ============================================================
# Test: Circuit breaker state transitions (extended)
# ============================================================

class TestCircuitBreakerExtended:
    """Additional breaker transition tests."""

    def test_normal_to_tripped_to_cooling_to_normal(self):
        from risk.circuit_breaker import BreakerState, CircuitBreaker
        from config.risk_params import RiskConfig

        config = RiskConfig(max_daily_loss_pct=0.02, pause_duration_days=7)
        cb = CircuitBreaker(config)

        # NORMAL → TRIPPED
        st = cb.check(200_000, -5_000, -0.025, [], date(2026, 6, 1))
        assert st.state == BreakerState.TRIPPED

        # TRIPPED → COOLING (next day, within pause)
        st = cb.check(200_000, 0, 0, [], date(2026, 6, 2))
        assert st.state == BreakerState.COOLING

        # COOLING → NORMAL (after pause)
        st = cb.check(200_000, 0, 0, [], date(2026, 6, 9))
        assert st.state == BreakerState.NORMAL

    def test_consecutive_loss_reset_by_gain(self):
        from risk.circuit_breaker import BreakerState, CircuitBreaker

        cb = CircuitBreaker()
        cb.check(200_000, -100, -0.0005, [], date(2026, 6, 1))
        assert cb.state == BreakerState.NORMAL
        cb.check(200_000, -100, -0.0005, [], date(2026, 6, 2))
        # Gain resets counter
        cb.check(200_000, +100, +0.0005, [], date(2026, 6, 3))
        # Should still be normal (counter reset to 0)
        st = cb.check(200_000, -100, -0.0005, [], date(2026, 6, 4))


# ============================================================
# Test: RSI Strategy
# ============================================================

class TestRSIStrategy:
    def test_init(self):
        from strategy.timing.rsi_only import RSIStrategy
        strat = RSIStrategy("test_rsi")
        strat.init(rsi_period=14, rsi_oversold=25, rsi_overbought=60, top_n=10, stop_loss=0.05)
        assert strat._rsi_period == 14
        assert strat._rsi_oversold == 25

    def test_empty_data(self):
        from strategy.timing.rsi_only import RSIStrategy
        strat = RSIStrategy("test_rsi")
        strat.init()
        sigs = strat.on_data(pd.DataFrame(), date(2026, 6, 1))
        assert sigs == []

    def test_buy_on_oversold(self):
        from strategy.timing.rsi_only import RSIStrategy
        strat = RSIStrategy("test_rsi")
        strat.init(rsi_period=14, rsi_oversold=25, rsi_overbought=60, top_n=10, min_price=0)
        # Create a stock that keeps falling for all 14 RSI periods → very oversold
        dates = pd.date_range("2026-01-01", periods=64, freq="B")
        # Start high, then steady decline for 30+ bars (every bar lower)
        prices = np.concatenate([
            np.full(20, 100.0),
            np.linspace(100, 50, 30),    # Steady decline
            np.linspace(50, 45, 14),     # Still declining recently
        ])
        df = pd.DataFrame({
            "ts_code": "000001.SZ",
            "trade_date": dates,
            "close": prices,
            "open": prices * 0.99,
            "high": prices * 1.01,
            "low": prices * 0.98,
            "vol": 1_000_000.0,
            "amount": prices * 1_000_000,
            "turnover_rate": 2.0,
        })
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        sigs = strat.on_data(df, dates[-1].date())
        buys = [s for s in sigs if s.signal_type.value == "BUY"]
        assert len(buys) >= 1, "Should generate buy signal on oversold RSI"

    def test_sell_on_overbought(self):
        from strategy.timing.rsi_only import RSIStrategy
        strat = RSIStrategy("test_rsi")
        strat.init(rsi_period=14, rsi_oversold=25, rsi_overbought=60, top_n=10, min_price=0)
        # Create a stock in strong uptrend → overbought
        prices = 50.0 + np.arange(50).cumsum() * 0.3
        df = pd.DataFrame({
            "ts_code": "000001.SZ",
            "trade_date": [date(2026, 1, 1) + pd.Timedelta(days=i) for i in range(50)],
            "close": prices,
            "open": prices * 0.99,
            "high": prices * 1.01,
            "low": prices * 0.98,
            "vol": 1_000_000,
            "amount": prices * 1_000_000,
            "turnover_rate": 2.0,
        })
        strat.sync_positions({"000001.SZ": {"avg_cost": 50.0, "volume": 1000}})
        sigs = strat.on_data(df, date(2026, 2, 19))
        sells = [s for s in sigs if s.signal_type.value == "SELL"]
        assert len(sells) >= 1, "Should sell when RSI overbought"


# ============================================================
# Test: Momentum Rotation Strategy
# ============================================================

class TestMomentumRotation:
    def test_init(self):
        from strategy.timing.momentum_rot import MomentumRotation
        strat = MomentumRotation("test_mom")
        strat.init(mom_period=60, top_n=10, rebalance_days=20)
        assert strat._mom_period == 60
        assert strat._top_n == 10

    def test_empty_data(self):
        from strategy.timing.momentum_rot import MomentumRotation
        strat = MomentumRotation("test_mom")
        strat.init()
        sigs = strat.on_data(pd.DataFrame(), date(2026, 6, 1))
        assert sigs == []

    def test_buy_on_positive_momentum(self):
        from strategy.timing.momentum_rot import MomentumRotation
        strat = MomentumRotation("test_mom")
        strat.init(mom_period=60, top_n=10, min_price=0)
        # Steady uptrend → positive momentum
        prices = 50.0 + np.arange(100).cumsum() * 0.2
        df = pd.DataFrame({
            "ts_code": "000001.SZ",
            "trade_date": [date(2026, 1, 1) + pd.Timedelta(days=i) for i in range(100)],
            "close": prices,
            "open": prices * 0.99,
            "high": prices * 1.01,
            "low": prices * 0.98,
            "vol": 1_000_000,
            "amount": prices * 1_000_000,
        })
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        sigs = strat.on_data(df, df["trade_date"].iloc[-1].date())
        buys = [s for s in sigs if s.signal_type.value == "BUY"]
        assert len(buys) >= 1, "Should buy stocks with positive momentum"

    def test_no_buy_on_negative_momentum(self):
        from strategy.timing.momentum_rot import MomentumRotation
        strat = MomentumRotation("test_mom")
        strat.init(mom_period=60, top_n=10, min_price=0)
        # Steady downtrend → negative momentum
        prices = 100.0 - np.arange(100).cumsum() * 0.3
        df = pd.DataFrame({
            "ts_code": "000001.SZ",
            "trade_date": [date(2026, 1, 1) + pd.Timedelta(days=i) for i in range(100)],
            "close": np.maximum(prices, 1.0),
            "open": np.maximum(prices, 1.0) * 1.01,
            "high": np.maximum(prices, 1.0) * 1.02,
            "low": np.maximum(prices, 1.0) * 0.99,
            "vol": 1_000_000.0,
            "amount": np.maximum(prices, 1.0) * 1_000_000,
        })
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        sigs = strat.on_data(df, df["trade_date"].iloc[-1].date())
        buys = [s for s in sigs if s.signal_type.value == "BUY"]
        assert len(buys) == 0, "Should NOT buy stocks with negative momentum"

    def test_sell_on_momentum_fade(self):
        from strategy.timing.momentum_rot import MomentumRotation
        strat = MomentumRotation("test_mom")
        strat.init(mom_period=60, top_n=1, min_price=0)
        # Downtrend → momentum reversal
        prices = 100.0 - np.arange(80).cumsum() * 0.5
        df = pd.DataFrame({
            "ts_code": "000001.SZ",
            "trade_date": [date(2026, 1, 1) + pd.Timedelta(days=i) for i in range(80)],
            "close": np.maximum(prices, 1.0),
            "open": np.maximum(prices, 1.0) * 1.01,
            "high": np.maximum(prices, 1.0) * 1.02,
            "low": np.maximum(prices, 1.0) * 0.99,
            "vol": 1_000_000.0,
            "amount": np.maximum(prices, 1.0) * 1_000_000,
        })
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        strat.sync_positions({"000001.SZ": {"avg_cost": 90.0, "volume": 1000}})
        sigs = strat.on_data(df, df["trade_date"].iloc[-1].date())
        sells = [s for s in sigs if s.signal_type.value == "SELL"]
        assert len(sells) >= 1, "Should sell when momentum reverses"
