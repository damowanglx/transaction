"""Shared RSI calculation — used by both trend_follow and mean_revert."""

import pandas as pd


def calc_rsi(prices: pd.Series, period: int = 14) -> float:
    """Calculate RSI for the last data point."""
    delta = prices.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.rolling(period).mean().iloc[-1]
    avg_loss = loss.rolling(period).mean().iloc[-1]
    if avg_loss == 0 or pd.isna(avg_loss):
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))
