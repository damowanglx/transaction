"""Pre-compute indicators for backtest speed optimization.

Instead of recalculating MA/BB/RSI for every stock on every trading day,
compute all indicators once and store as DataFrame columns.
"""

import numpy as np
import pandas as pd


def precompute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add indicator columns to stock data — computed once per stock.

    Adds: ma_20, bb_lower, bb_upper, bb_position, rsi_14, vol_ratio,
          weekly_ma20, weekly_close

    Args:
        df: DataFrame with [ts_code, trade_date, open, high, low, close, vol].

    Returns:
        Same DataFrame with added indicator columns.
    """
    result = df.copy()
    result["trade_date"] = pd.to_datetime(result["trade_date"])
    result = result.sort_values(["ts_code", "trade_date"])

    codes = result["ts_code"].unique()
    all_frames = []

    for i, code in enumerate(codes):
        mask = result["ts_code"] == code
        subset = result.loc[mask].copy()
        if len(subset) < 30:
            all_frames.append(subset)
            continue

        close = subset["close"]
        vol = subset["vol"]

        # MA20
        subset["ma_20"] = close.rolling(20).mean()

        # Bollinger Bands (2σ)
        std20 = close.rolling(20).std()
        subset["bb_upper_2"] = close.rolling(20).mean() + 2 * std20
        subset["bb_lower_2"] = close.rolling(20).mean() - 2 * std20

        # Bollinger Bands (3σ) — for our 3σ strategy
        subset["bb_upper_3"] = close.rolling(20).mean() + 3 * std20
        subset["bb_lower_3"] = close.rolling(20).mean() - 3 * std20
        subset["bb_position_3"] = np.where(
            (subset["bb_upper_3"] - subset["bb_lower_3"]) > 0,
            (close - subset["bb_lower_3"]) / (subset["bb_upper_3"] - subset["bb_lower_3"]),
            np.nan,
        )

        # RSI 14
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        avg_gain = gain.rolling(14).mean()
        avg_loss = loss.rolling(14).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        subset["rsi_14"] = 100.0 - (100.0 / (1.0 + rs))

        # Volume ratio (5 vs 20 day)
        subset["vol_ratio"] = vol.rolling(5).mean() / vol.rolling(20).mean().replace(0, 1)

        all_frames.append(subset)

    out = pd.concat(all_frames, ignore_index=True)
    return out.sort_values(["trade_date", "ts_code"])


def precompute_benchmark(bench_df: pd.DataFrame) -> pd.Series:
    """Build market regime indicator series from CSI 300 data.

    Returns:
        Series with index=date, value=True when market is in safe regime.
    """
    if bench_df.empty:
        return pd.Series(dtype=bool)

    close = bench_df.set_index("trade_date")["close"]
    ma50 = close.rolling(50).mean()
    # Safe: market NOT in strong downtrend (>5% below MA50)
    safe = ~(close < ma50 * 0.95)
    return safe
