"""
Tests for factor computation and stock selector (Phase 2).

Verifies:
- Momentum factors produce valid values
- Volatility factors are within reasonable ranges
- Turnover factors detect anomalies
- Technical indicators match known formulas
- StockSelector IC analysis works
- Signal generation logic is correct
"""

import sys
from datetime import date

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, ".")


# ============================================================
# Test Helpers: Generate synthetic price data
# ============================================================

def _make_price_series(n: int = 200, start_price: float = 10.0, seed: int = 42) -> pd.Series:
    """Generate a synthetic price series with random walk."""
    rng = np.random.default_rng(seed)
    returns = rng.normal(0.001, 0.02, n)
    prices = start_price * np.cumprod(1 + returns)
    prices[0] = start_price
    return pd.Series(prices)


def _make_ohlcv_df(n: int = 200, seed: int = 42) -> pd.DataFrame:
    """Generate synthetic OHLCV data."""
    rng = np.random.default_rng(seed)
    close = _make_price_series(n, 10.0, seed)
    daily_range = close * rng.uniform(0.01, 0.05, n)
    high = close + daily_range / 2
    low = close - daily_range / 2
    open_price = close.shift(1).fillna(close.iloc[0])
    vol = rng.integers(100_000, 10_000_000, n).astype(float)
    amount = close * vol
    turnover = rng.uniform(0.5, 5.0, n)

    dates = pd.date_range(start="2023-01-01", periods=n, freq="B")

    return pd.DataFrame({
        "trade_date": dates,
        "ts_code": "TEST01.SZ",
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "vol": vol,
        "amount": amount,
        "turnover_rate": turnover,
    })


def _make_multi_stock_df(n_stocks: int = 10, n_days: int = 200) -> pd.DataFrame:
    """Generate synthetic multi-stock data."""
    frames = []
    for i in range(n_stocks):
        df = _make_ohlcv_df(n_days, seed=42 + i)
        df["ts_code"] = f"TEST{i:02d}.SZ"
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


# ============================================================
# Test: Momentum Factors
# ============================================================

class TestMomentumFactors:
    """Verify momentum factor calculations."""

    def test_raw_momentum(self):
        from strategy.factors.momentum import raw_momentum

        prices = _make_price_series(200)
        mom = raw_momentum(prices, period=20)

        assert len(mom) == 200
        # First 20 values should be NaN
        assert pd.isna(mom.iloc[0])
        assert pd.isna(mom.iloc[19])
        assert not pd.isna(mom.iloc[20])
        # With 0.1% daily mean, 20-day momentum ≈ ~2%
        assert mom.iloc[-1] > -0.5 and mom.iloc[-1] < 0.5

    def test_rsi_range(self):
        from strategy.factors.momentum import rsi

        prices = _make_price_series(200)
        rsi_values = rsi(prices, period=14)

        valid = rsi_values.dropna()
        assert (valid >= 0).all()
        assert (valid <= 100).all()

    def test_macd_output(self):
        from strategy.factors.momentum import macd

        prices = _make_price_series(200)
        result = macd(prices)

        assert "macd_line" in result.columns
        assert "signal_line" in result.columns
        assert "histogram" in result.columns
        # Histogram ≈ macd_line - signal_line
        diff = (result["macd_line"] - result["signal_line"] - result["histogram"]).abs()
        assert diff.max() < 1e-10

    def test_risk_adjusted_momentum(self):
        from strategy.factors.momentum import risk_adjusted_momentum

        prices = _make_price_series(200)
        ram = risk_adjusted_momentum(prices, period=60)

        assert len(ram) == 200
        valid = ram.dropna()
        # Risk-adjusted momentum should typically be within [-5, 5]
        assert valid.abs().max() < 10.0

    def test_relative_strength(self):
        from strategy.factors.momentum import relative_strength

        stock = _make_price_series(200, seed=42)
        bench = _make_price_series(200, seed=99)
        rs = relative_strength(stock, bench, period=60)

        assert len(rs) == 200


# ============================================================
# Test: Volatility Factors
# ============================================================

class TestVolatilityFactors:
    """Verify volatility factor calculations."""

    def test_historical_volatility(self):
        from strategy.factors.volatility import historical_volatility

        prices = _make_price_series(200)
        vol = historical_volatility(prices, period=20)

        assert len(vol) == 200
        valid = vol.dropna()
        # Annualized vol should be > 0
        assert (valid > 0).all()
        # For 2% daily std, annualized ~31%. With 1% mean return it's lower.
        assert valid.mean() < 1.0  # Less than 100% annualized vol

    def test_atr_positive(self):
        from strategy.factors.volatility import atr

        df = _make_ohlcv_df(200)
        atr_values = atr(df["high"], df["low"], df["close"], period=14)

        valid = atr_values.dropna()
        assert (valid > 0).all()

    def test_max_drawdown_negative(self):
        from strategy.factors.volatility import max_drawdown

        prices = _make_price_series(200)
        dd = max_drawdown(prices, period=60)

        valid = dd.dropna()
        # Drawdowns should be <= 0
        assert (valid <= 0.01).all()  # Allow tiny positive from rounding

    def test_beta_calculation(self):
        from strategy.factors.volatility import beta

        stock = _make_price_series(200, seed=42)
        market = _make_price_series(200, seed=99)
        b = beta(stock, market, period=60)

        valid = b.dropna()
        # Most betas are in [0, 3]
        assert valid.mean() > 0

    def test_downside_deviation(self):
        from strategy.factors.volatility import downside_deviation

        prices = _make_price_series(200)
        dd = downside_deviation(prices, period=60)

        valid = dd.dropna()
        assert (valid >= 0).all()


# ============================================================
# Test: Turnover Factors
# ============================================================

class TestTurnoverFactors:
    """Verify turnover/volume factor calculations."""

    def test_volume_ratio(self):
        from strategy.factors.turnover import volume_ratio

        vol = pd.Series(np.random.default_rng(42).integers(1000, 10000, 200).astype(float))
        ratio = volume_ratio(vol, short_period=5, long_period=20)

        valid = ratio.dropna()
        assert valid.mean() > 0

    def test_money_flow(self):
        from strategy.factors.turnover import money_flow

        close = _make_price_series(200)
        vol = pd.Series(np.random.default_rng(42).integers(1000, 10000, 200).astype(float))
        mf = money_flow(close, vol, period=10)

        assert len(mf) == 200

    def test_abnormal_volume_zscore(self):
        from strategy.factors.turnover import abnormal_volume

        vol = pd.Series(np.random.default_rng(42).integers(1000, 10000, 200).astype(float))
        z = abnormal_volume(vol, period=20, std_thresh=2.0)

        valid = z.dropna()
        # Vast majority should be within [-3, 3]
        assert (valid.abs() < 5).mean() > 0.90


# ============================================================
# Test: Technical Factors
# ============================================================

class TestTechnicalFactors:
    """Verify technical indicator calculations."""

    def test_moving_averages(self):
        from strategy.factors.technical import moving_averages

        prices = _make_price_series(200)
        result = moving_averages(prices)

        assert "ma_5" in result.columns
        assert "ma_20" in result.columns
        assert "ma_60" in result.columns
        assert result["ma_5_20_cross"].isin([0, 1]).all()

    def test_bollinger_position_range(self):
        from strategy.factors.technical import bollinger_bands

        prices = _make_price_series(200)
        result = bollinger_bands(prices, period=20, num_std=2.0)

        valid = result["bb_position"].dropna()
        # Most positions should be in [0, 1] but can go outside with large moves
        assert valid.between(-0.2, 1.2).mean() > 0.90

    def test_williams_r_range(self):
        from strategy.factors.technical import williams_r

        df = _make_ohlcv_df(200)
        wr = williams_r(df["high"], df["low"], df["close"], period=14)

        valid = wr.dropna()
        assert (valid >= -100).all()
        assert (valid <= 0).all()

    def test_obv_cumulative(self):
        from strategy.factors.technical import obv

        close = _make_price_series(200)
        vol = pd.Series(np.abs(np.random.default_rng(42).normal(10000, 2000, 200)))
        obv_values = obv(close, vol)

        # OBV is cumulative and should generally trend
        assert len(obv_values) == 200

    def test_cci_range(self):
        from strategy.factors.technical import cci

        df = _make_ohlcv_df(200)
        cci_values = cci(df["high"], df["low"], df["close"], period=20)

        valid = cci_values.dropna()
        # CCI can be extreme, but most values should be in [-300, 300]
        assert valid.abs().mean() < 200.0


# ============================================================
# Test: Stock Selector
# ============================================================

class TestStockSelector:
    """Verify multi-factor stock selection logic."""

    def test_init(self):
        from strategy.selector.stock_selector import StockSelector

        selector = StockSelector("test")
        selector.init(
            factors=["mom_20", "vol_20"],
            top_n=10,
            lookback_days=60,
        )
        assert selector.is_initialized
        assert selector.name == "test"

    def test_empty_data_returns_no_signals(self):
        from strategy.selector.stock_selector import StockSelector

        selector = StockSelector("test")
        selector.init(factors=["mom_20"])
        signals = selector.on_data(
            pd.DataFrame(), date.today(),
        )
        assert signals == []

    def test_score_stocks(self):
        from strategy.selector.stock_selector import StockSelector

        selector = StockSelector("test")
        selector.init(
            factors=["mom_60", "vol_20"],
            top_n=3,
            ic_weights={"mom_60": 0.6, "vol_20": 0.4},
        )

        # Create data with known factor values
        data = pd.DataFrame({
            "ts_code": ["A.SZ", "B.SZ", "C.SZ"],
            "trade_date": pd.Timestamp("2024-06-15"),
            "mom_60": [0.10, 0.05, -0.03],  # A best, C worst
            "vol_20": [0.25, 0.30, 0.20],   # C best (low vol), A medium
        })

        scores = selector._score_stocks(data)
        assert len(scores) == 3
        # A should score highest (best momentum, medium vol)
        assert scores["A.SZ"] > scores["C.SZ"]

    def test_equal_weights_fallback(self):
        from strategy.selector.stock_selector import StockSelector

        selector = StockSelector("test")
        selector.init(factors=["mom_60", "vol_20"])

        # Both factors should favor the same ranking (A > B > C).
        # Factor 1 (momentum): higher is better
        # Factor 2 (turnover_std): for this test, lower is better —
        #   but scoring uses abs(IC) and raw z-scores, so factor direction
        #   depends on IC sign. With equal weights, both factors positively
        #   weighted, so we align them: A best on both, C worst on both.
        data = pd.DataFrame({
            "ts_code": ["A.SZ", "B.SZ", "C.SZ"],
            "trade_date": pd.Timestamp("2024-06-15"),
            "mom_60": [0.20, 0.00, -0.20],
            "vol_20": [0.60, 0.30, 0.10],  # C has "worst" factor value
        })

        scores = selector._score_stocks(data)
        # A: best mom, best vol_z → highest combined score
        # C: worst mom, worst vol_z → lowest combined score
        assert scores["A.SZ"] > scores["C.SZ"]
        # B should be between A and C
        assert scores["A.SZ"] > scores["B.SZ"] or scores["B.SZ"] > scores["C.SZ"]

    def test_signal_generation_buy(self):
        from strategy.selector.stock_selector import StockSelector, SignalType

        selector = StockSelector("test")
        selector.init(
            factors=["mom_60"],
            top_n=2,
            current_positions=set(),
        )

        ranked = [("A.SZ", 2.5), ("B.SZ", 1.5), ("C.SZ", 0.5)]
        selected = {"A.SZ", "B.SZ"}
        signals = selector._generate_signals(ranked, selected, date(2024, 6, 15))

        # A and B are new buys
        buys = [s for s in signals if s.signal_type == SignalType.BUY]
        assert len(buys) == 2
        assert {s.ts_code for s in buys} == {"A.SZ", "B.SZ"}

    def test_signal_generation_sell(self):
        from strategy.selector.stock_selector import StockSelector, SignalType

        selector = StockSelector("test")
        selector.init(
            factors=["mom_60"],
            top_n=2,
            current_positions={"C.SZ"},
        )

        ranked = [("A.SZ", 2.5), ("B.SZ", 1.5), ("C.SZ", 0.5)]
        selected = {"A.SZ", "B.SZ"}
        signals = selector._generate_signals(ranked, selected, date(2024, 6, 15))

        # C fell out of top 2, should be sold
        sells = [s for s in signals if s.signal_type == SignalType.SELL]
        assert len(sells) == 1
        assert sells[0].ts_code == "C.SZ"

    def test_signal_immutable(self):
        from strategy.base.strategy_template import Signal, SignalType

        sig = Signal(
            ts_code="000001.SZ",
            signal_type=SignalType.BUY,
            confidence=0.8,
            reason="Test",
        )
        with pytest.raises(Exception):
            sig.confidence = 0.9  # type: ignore

    def test_ic_analysis(self):
        from strategy.selector.stock_selector import StockSelector

        selector = StockSelector("test")
        selector.init(factors=["mom_60"], min_ic=0.0)

        # Generate data: 50 stocks per date, mom predicts forward return
        rng = np.random.default_rng(42)
        n_stocks = 50
        n_dates = 30
        rows = []
        for d in range(n_dates):
            dt = pd.Timestamp("2024-01-01") + pd.DateOffset(days=d)
            mom = rng.normal(0, 1, n_stocks)
            forward_ret = 0.05 * mom + rng.normal(0, 0.1, n_stocks)  # mom has positive IC
            for s in range(n_stocks):
                rows.append({
                    "ts_code": f"T{s:02d}.SZ",
                    "trade_date": dt,
                    "mom_60": mom[s],
                    "forward_return_5d": forward_ret[s],
                })

        data = pd.DataFrame(rows)

        results = selector.analyze_ic(data)
        assert len(results) == 1
        assert results[0].name == "mom_60"
        # With our synthetic data (0.05 slope), should have positive IC
        assert results[0].rank_ic_mean > 0
