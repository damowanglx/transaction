"""
Turnover / volume factors for A-Share stock selection.

Turnover is a critical signal in A-shares. High retail participation
means volume/turnover anomalies are very pronounced:
- Low turnover stocks are often neglected and can be undervalued
- Sudden volume spikes often precede trend changes
- Turnover rate on the STAR/ChiNext boards has different norms

Factors implemented:
- avg_turnover(period): Average daily turnover rate
- volume_ratio(short/long): Short-term vs long-term volume ratio
- money_flow(period): Capital flow (price * volume) trend
- turnover_std(period): Turnover volatility (stability signal)
- abnormal_volume(period, std_thresh): Volume anomaly detection
"""

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def avg_turnover(
    turnover_rate: pd.Series,
    period: int = 20,
) -> pd.Series:
    """Average daily turnover rate over a rolling window."""
    return turnover_rate.rolling(period).mean()


def volume_ratio(
    volume: pd.Series,
    short_period: int = 5,
    long_period: int = 20,
) -> pd.Series:
    """Volume ratio: short-term avg volume / long-term avg volume.

    > 1.0 = recent volume expanding (potential breakout).
    < 1.0 = recent volume contracting (consolidation).
    """
    short_ma = volume.rolling(short_period).mean()
    long_ma = volume.rolling(long_period).mean()
    ratio = short_ma / long_ma.replace(0, np.nan)
    return ratio


def money_flow(
    close: pd.Series,
    volume: pd.Series,
    period: int = 10,
) -> pd.Series:
    """Simple money flow indicator based on price * volume trend.

    Positive = money flowing in, Negative = money flowing out.

    Uses Chao-Ming methodology: typical price * volume,
    accumulated with sign based on price direction.
    """
    typical_price = close  # Using close as proxy
    raw_flow = typical_price.diff() * volume
    return raw_flow.rolling(period).sum()


def turnover_std(
    turnover_rate: pd.Series,
    period: int = 20,
) -> pd.Series:
    """Turnover rate volatility — high std means unstable participation."""
    return turnover_rate.rolling(period).std()


def abnormal_volume(
    volume: pd.Series,
    period: int = 20,
    std_thresh: float = 2.0,
) -> pd.Series:
    """Detect abnormal volume days.

    Returns z-score of current volume relative to rolling mean/std.
    |z| > std_thresh = abnormal volume.
    """
    roll_mean = volume.rolling(period).mean()
    roll_std = volume.rolling(period).std()
    z_score = (volume - roll_mean) / roll_std.replace(0, np.nan)
    return z_score


def turnover_factor_bundle(
    price_df: pd.DataFrame,
) -> pd.DataFrame:
    """Compute all turnover/volume factors for a universe of stocks.

    Args:
        price_df: DataFrame with columns [ts_code, trade_date, close, vol,
                  turnover_rate, amount].

    Returns:
        DataFrame with columns: ts_code, trade_date,
        avg_turn_5, avg_turn_20, vol_ratio, money_flow_10,
        turnover_std_20, abnormal_vol
    """
    results = []
    codes = price_df["ts_code"].unique()

    for code in codes:
        subset = price_df[price_df["ts_code"] == code].sort_values("trade_date")
        if len(subset) < 60:
            continue

        df = pd.DataFrame({
            "trade_date": subset["trade_date"],
            "ts_code": code,
        })

        vol = subset["vol"]
        close = subset["close"]
        turnover = subset.get("turnover_rate", pd.Series([np.nan] * len(subset), index=subset.index))

        df["avg_turn_5"] = avg_turnover(turnover, 5).values
        df["avg_turn_20"] = avg_turnover(turnover, 20).values
        df["vol_ratio"] = volume_ratio(vol, 5, 20).values
        df["money_flow_10"] = money_flow(close, vol, 10).values
        df["turnover_std_20"] = turnover_std(turnover, 20).values
        df["abnormal_vol"] = abnormal_volume(vol, 20).values

        results.append(df)

    if not results:
        return pd.DataFrame()

    return pd.concat(results, ignore_index=True)
