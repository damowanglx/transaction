"""
Volatility factors for A-Share stock selection.

In A-shares, low-volatility anomaly exists but is weaker than
in developed markets. Chinese retail investors often prefer
high-volatility stocks, creating a "lottery preference" effect.

Factors implemented:
- historical_volatility(period): Annualized standard deviation of returns
- ATR(period): Average True Range
- downside_deviation(period): Semi-deviation (downside only)
- max_drawdown(period): Maximum peak-to-trough decline
- beta(market_returns): Market sensitivity (CAPM beta)
"""

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def historical_volatility(
    prices: pd.Series,
    period: int = 20,
    ann_factor: int = 244,
) -> pd.Series:
    """Annualized historical volatility over a rolling window."""
    returns = prices.pct_change()
    vol = returns.rolling(period).std() * np.sqrt(ann_factor)
    return vol


def atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> pd.Series:
    """Average True Range (ATR).

    True Range = max(high-low, |high-prev_close|, |low-prev_close|)
    """
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return true_range.rolling(period).mean()


def downside_deviation(
    prices: pd.Series,
    period: int = 60,
    mar: float = 0.0,
    ann_factor: int = 244,
) -> pd.Series:
    """Downside deviation (semi-deviation) — volatility of negative returns only.

    Args:
        prices: Close prices.
        period: Rolling window.
        mar: Minimum acceptable return (default 0 = only negative returns count).
        ann_factor: Annualization factor.

    Returns:
        Annualized downside deviation.
    """
    returns = prices.pct_change()
    downside = returns.where(returns < mar, 0.0)
    sq = downside ** 2
    result = sq.rolling(period).mean().apply(np.sqrt) * np.sqrt(ann_factor)
    return result


def max_drawdown(prices: pd.Series, period: int = 60) -> pd.Series:
    """Maximum drawdown over a rolling window.

    Returns negative values. -0.15 means 15% max drawdown.
    """
    rolling_max = prices.rolling(period, min_periods=1).max()
    drawdown = (prices - rolling_max) / rolling_max
    return drawdown.rolling(period).min()


def beta(
    stock_prices: pd.Series,
    market_prices: pd.Series,
    period: int = 60,
) -> pd.Series:
    """CAPM beta: covariance(stock, market) / variance(market).

    Args:
        stock_prices: Stock close prices.
        market_prices: Market index close prices (aligned by index).
        period: Rolling window.

    Returns:
        Series of rolling beta values.
    """
    stock_returns = stock_prices.pct_change()
    market_returns = market_prices.pct_change()

    aligned = pd.concat([stock_returns, market_returns], axis=1).dropna()
    if aligned.empty:
        return pd.Series(np.nan, index=stock_prices.index)

    cov = aligned.iloc[:, 0].rolling(period).cov(aligned.iloc[:, 1])
    var = aligned.iloc[:, 1].rolling(period).var()
    beta_values = cov / var.replace(0, np.nan)
    return beta_values


def volatility_factor_bundle(
    price_df: pd.DataFrame,
    market_prices: pd.Series | None = None,
) -> pd.DataFrame:
    """Compute all volatility factors for a universe of stocks.

    Args:
        price_df: DataFrame with columns [ts_code, trade_date, open, high, low, close].
        market_prices: Optional market index close for beta calculation.

    Returns:
        DataFrame with columns: ts_code, trade_date,
        vol_20, vol_60, atr_14, downside_dev_60, max_dd_60, beta_60
    """
    results = []
    codes = price_df["ts_code"].unique()

    for code in codes:
        subset = price_df[price_df["ts_code"] == code].sort_values("trade_date")
        if len(subset) < 120:
            continue

        df = pd.DataFrame({
            "trade_date": subset["trade_date"],
            "ts_code": code,
        })

        close = subset["close"]
        high = subset["high"]
        low = subset["low"]

        df["vol_20"] = historical_volatility(close, 20).values
        df["vol_60"] = historical_volatility(close, 60).values
        df["atr_14"] = atr(high, low, close, 14).values
        df["downside_dev_60"] = downside_deviation(close, 60).values
        df["max_dd_60"] = max_drawdown(close, 60).values

        if market_prices is not None and len(market_prices) == len(close):
            # Align by reindexing market to stock's dates
            aligned_market = market_prices.reindex(close.index)
            df["beta_60"] = beta(close, aligned_market, 60).values

        results.append(df)

    if not results:
        return pd.DataFrame()

    return pd.concat(results, ignore_index=True)
