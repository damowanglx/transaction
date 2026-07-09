"""Market regime detection + strategy switching.

Detects 4 market states and picks the right strategy for each:
- BULL_TREND:  Trend following (ride the wave)
- BEAR_TREND:  Cash preservation (short or cash)
- RANGE_BOUND: Mean reversion (buy dips, sell rips)
- VOLATILE:    Reduced sizing (half position, wide stops)
"""

import numpy as np
import pandas as pd
from enum import Enum


class MarketRegime(str, Enum):
    BULL_TREND = "BULL_TREND"      # Uptrend — trend follow
    BEAR_TREND = "BEAR_TREND"      # Downtrend — stay cash
    RANGE_BOUND = "RANGE_BOUND"    # Sideways — mean revert
    VOLATILE = "VOLATILE"          # High vol — caution


def detect_regime(
    market_data: pd.DataFrame,
    lookback: int = 20,
) -> MarketRegime:
    """Detect current market regime from index data.

    Args:
        market_data: DataFrame with [trade_date, close] for CSI 300.
        lookback: Trading days for trend detection.

    Returns:
        MarketRegime enum.
    """
    if market_data.empty or len(market_data) < lookback:
        return MarketRegime.RANGE_BOUND  # Default: neutral

    close = market_data.set_index("trade_date")["close"].sort_index()
    current = close.iloc[-1]

    # Moving averages
    ma20 = close.rolling(20).mean().iloc[-1]
    ma60 = close.rolling(60).mean().iloc[-1] if len(close) >= 60 else ma20

    # Trend strength: slope of MA20 over last 10 days
    ma20_10d_ago = close.rolling(20).mean().iloc[-10] if len(close) >= 30 else ma20
    trend_slope = (ma20 - ma20_10d_ago) / ma20_10d_ago * 100

    # Volatility
    returns = close.pct_change().dropna()
    recent_vol = returns.tail(lookback).std() * np.sqrt(244)
    long_vol = returns.tail(60).std() * np.sqrt(244) if len(returns) >= 60 else recent_vol
    vol_ratio = recent_vol / long_vol if long_vol > 0 else 1.0

    # Drawdown from 60-day high
    high_60d = close.tail(60).max() if len(close) >= 60 else close.max()
    drawdown = (current - high_60d) / high_60d * 100

    # Regime logic
    if drawdown < -15:
        return MarketRegime.BEAR_TREND  # Deep correction
    if drawdown < -8 and trend_slope < -0.5:
        return MarketRegime.BEAR_TREND  # Accelerating down

    if vol_ratio > 1.5:
        return MarketRegime.VOLATILE  # Volatility spike

    if trend_slope > 1.0 and current > ma20 > ma60:
        return MarketRegime.BULL_TREND  # Strong uptrend

    if abs(trend_slope) < 1.0:
        return MarketRegime.RANGE_BOUND  # Flat → range-bound

    return MarketRegime.RANGE_BOUND


def get_position_config(regime: MarketRegime) -> dict:
    """Return risk parameters for each market regime."""
    configs = {
        MarketRegime.BULL_TREND: {
            "max_positions": 8,
            "total_capital_pct": 0.70,
            "per_stock_pct": 0.10,
            "stop_loss": 0.05,
            "description": "趋势向上 — 积极做多",
        },
        MarketRegime.BEAR_TREND: {
            "max_positions": 0,
            "total_capital_pct": 0.00,
            "per_stock_pct": 0.00,
            "stop_loss": 0.03,
            "description": "下跌趋势 — 空仓防守",
        },
        MarketRegime.RANGE_BOUND: {
            "max_positions": 5,
            "total_capital_pct": 0.40,
            "per_stock_pct": 0.08,
            "stop_loss": 0.05,
            "description": "震荡市 — 均值回归",
        },
        MarketRegime.VOLATILE: {
            "max_positions": 3,
            "total_capital_pct": 0.25,
            "per_stock_pct": 0.05,
            "stop_loss": 0.03,
            "description": "高波动 — 谨慎小仓",
        },
    }
    return configs[regime]
