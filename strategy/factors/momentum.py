"""
Momentum factors for A-Share stock selection.

Momentum is one of the most robust anomalies in A-shares,
where retail-dominant markets tend to exhibit strong
continuation patterns over medium horizons (1-6 months).

Factors implemented:
- raw_momentum(period): Simple price return over N days
- risk_adjusted_momentum(period): Return / Volatility
- RSI(period): Relative Strength Index (0-100)
- MACD_signal: MACD histogram crossover
- relative_strength: Stock return vs benchmark return
"""

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def raw_momentum(prices: pd.Series, period: int = 60) -> pd.Series:
    """Simple price momentum: (price_t - price_{t-period}) / price_{t-period}.

    Args:
        prices: Series of close prices, sorted by date ascending.
        period: Lookback period in trading days (default 60 ≈ 3 months).

    Returns:
        Series of momentum values, NaN for first `period` observations.
    """
    return prices.pct_change(periods=period)


def risk_adjusted_momentum(
    prices: pd.Series,
    period: int = 60,
    ann_factor: int = 244,
) -> pd.Series:
    """Risk-adjusted momentum: total return / annualized volatility.

    Args:
        prices: Series of close prices.
        period: Lookback period.
        ann_factor: Annualization factor (244 trading days for A-shares).

    Returns:
        Series of risk-adjusted returns (Sharpe-like ratio).
    """
    returns = prices.pct_change()
    rolling_ret = prices.pct_change(periods=period)
    rolling_vol = returns.rolling(period).std() * np.sqrt(ann_factor)
    result = rolling_ret / rolling_vol.replace(0, np.nan)
    return result


def rsi(prices: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index (RSI).

    Standard RSI: 100 - (100 / (1 + avg_gain / avg_loss))
    """
    delta = prices.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)

    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi_values = 100.0 - (100.0 / (1.0 + rs))
    return rsi_values


def macd(
    prices: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> pd.DataFrame:
    """MACD indicator.

    Returns DataFrame with columns: macd_line, signal_line, histogram.
    Histogram > 0 = bullish crossover.
    """
    ema_fast = prices.ewm(span=fast, adjust=False).mean()
    ema_slow = prices.ewm(span=slow, adjust=False).mean()

    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line

    return pd.DataFrame({
        "macd_line": macd_line,
        "signal_line": signal_line,
        "histogram": histogram,
    })


def relative_strength(
    stock_prices: pd.Series,
    benchmark_prices: pd.Series,
    period: int = 60,
) -> pd.Series:
    """Relative strength vs benchmark (excess return).

    Positive = stock outperforming benchmark over the period.
    """
    stock_ret = stock_prices.pct_change(periods=period)
    bench_ret = benchmark_prices.pct_change(periods=period)
    return stock_ret - bench_ret


def momentum_factor_bundle(
    price_df: pd.DataFrame,
    benchmark_df: pd.DataFrame | None = None,
    periods: tuple[int, ...] = (20, 60, 120),
) -> pd.DataFrame:
    """Compute all momentum factors for a universe of stocks.

    Args:
        price_df: DataFrame with columns [ts_code, trade_date, close].
                  Multi-index or long format recommended.
        benchmark_df: Optional benchmark close prices for relative strength.

    Returns:
        DataFrame with columns: ts_code, trade_date,
        mom_20, mom_60, mom_120, rsi_14, risk_adj_mom_60, rel_str_60
    """
    # Pivot to wide format: dates x stocks (dedup first to avoid pivot errors)
    price_df = price_df.drop_duplicates(subset=["trade_date", "ts_code"])
    prices_wide = price_df.pivot(index="trade_date", columns="ts_code", values="close")

    results = []

    for code in prices_wide.columns:
        stock_prices = prices_wide[code].dropna()
        if len(stock_prices) < 120:
            continue

        df = pd.DataFrame({"trade_date": stock_prices.index, "ts_code": code})
        df = df.set_index("trade_date")

        for p in periods:
            df[f"mom_{p}"] = raw_momentum(stock_prices, p)

        df["rsi_14"] = rsi(stock_prices, 14)
        df["risk_adj_mom_60"] = risk_adjusted_momentum(stock_prices, 60)

        macd_data = macd(stock_prices)
        df["macd_hist"] = macd_data["histogram"]

        if benchmark_df is not None:
            bench_prices = benchmark_df.set_index("trade_date")["close"]
            aligned = pd.concat([stock_prices, bench_prices], axis=1).dropna()
            if not aligned.empty:
                df["rel_str_60"] = relative_strength(
                    aligned.iloc[:, 0], aligned.iloc[:, 1], 60
                )

        df = df.reset_index()
        results.append(df)

    if not results:
        return pd.DataFrame()

    return pd.concat(results, ignore_index=True)
