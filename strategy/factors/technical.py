"""
Technical indicator factors for A-Share stock selection.

Common technical indicators that serve as features for
the multi-factor model. These are standard calculations
that would be expensive to recompute repeatedly.

Factors implemented:
- MA cross signals (5/20/60 MA)
- Bollinger Bands (position, width)
- KDJ indicator
- OBV (On-Balance Volume)
- WR (Williams %R)
- CCI (Commodity Channel Index)
"""

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def moving_averages(close: pd.Series) -> pd.DataFrame:
    """Compute MA(5), MA(20), MA(60) and cross signals.

    Returns DataFrame with:
    - ma_5, ma_20, ma_60
    - ma_5_20_cross: ma_5 > ma_20 (1 = golden cross, 0 = death cross)
    - ma_20_60_cross: ma_20 > ma_60
    - price_vs_ma_20: close / ma_20 - 1 (deviation from 20-day MA)
    """
    ma_5 = close.rolling(5).mean()
    ma_20 = close.rolling(20).mean()
    ma_60 = close.rolling(60).mean()

    return pd.DataFrame({
        "ma_5": ma_5,
        "ma_20": ma_20,
        "ma_60": ma_60,
        "ma_5_20_cross": (ma_5 > ma_20).astype(int),
        "ma_20_60_cross": (ma_20 > ma_60).astype(int),
        "price_vs_ma_20": close / ma_20 - 1.0,
    })


def bollinger_bands(
    close: pd.Series,
    period: int = 20,
    num_std: float = 2.0,
) -> pd.DataFrame:
    """Bollinger Bands.

    Returns DataFrame with:
    - bb_upper, bb_middle, bb_lower
    - bb_position: (close - lower) / (upper - lower)  [0~1]
    - bb_width: (upper - lower) / middle  [band width ratio]
    - bb_squeeze: bool — width at N-period low (potential breakout)
    """
    middle = close.rolling(period).mean()
    std = close.rolling(period).std()

    upper = middle + num_std * std
    lower = middle - num_std * std
    position = (close - lower) / (upper - lower).replace(0, np.nan)
    width = (upper - lower) / middle.replace(0, np.nan)

    # Squeeze: bandwidth at 20-period minimum (use tolerance for float comparison)
    width_min = width.rolling(20).min()
    squeeze = (width - width_min).abs() < 1e-10

    return pd.DataFrame({
        "bb_upper": upper,
        "bb_middle": middle,
        "bb_lower": lower,
        "bb_position": position,
        "bb_width": width,
        "bb_squeeze": squeeze.astype(int),
    })


def williams_r(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> pd.Series:
    """Williams %R: (highest_high - close) / (highest_high - lowest_low) * -100.

    Values range from -100 (oversold) to 0 (overbought).
    """
    highest = high.rolling(period).max()
    lowest = low.rolling(period).min()
    wr = (highest - close) / (highest - lowest).replace(0, np.nan) * -100.0
    return wr


def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    """On-Balance Volume (OBV).

    Cumulative volume with sign based on price direction.
    OBV rising = accumulation, OBV falling = distribution.
    """
    direction = np.sign(close.diff())
    direction = direction.copy()
    direction.iloc[0] = 0
    return (direction * volume).cumsum()


def cci(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 20,
) -> pd.Series:
    """Commodity Channel Index (CCI).

    CCI = (typical_price - SMA) / (0.015 * mean_deviation)
    Values > +100 = overbought, < -100 = oversold.
    """
    tp = (high + low + close) / 3.0
    sma = tp.rolling(period).mean()
    mad = tp.rolling(period).apply(lambda x: np.abs(x - x.mean()).mean())
    cci_value = (tp - sma) / (0.015 * mad)
    return cci_value


def technical_factor_bundle(price_df: pd.DataFrame) -> pd.DataFrame:
    """Compute all technical factors for a universe of stocks.

    Args:
        price_df: DataFrame with columns [ts_code, trade_date, open, high, low,
                  close, vol].

    Returns:
        DataFrame with technical factor values.
    """
    results = []
    codes = price_df["ts_code"].unique()

    for code in codes:
        subset = price_df[price_df["ts_code"] == code].sort_values("trade_date")
        if len(subset) < 120:
            continue

        close_series = subset["close"].reset_index(drop=True)
        high_series = subset["high"].reset_index(drop=True)
        low_series = subset["low"].reset_index(drop=True)
        vol_series = subset["vol"].reset_index(drop=True)
        dates = subset["trade_date"].reset_index(drop=True)

        df = pd.DataFrame({"trade_date": dates, "ts_code": code})

        # MA
        ma_df = moving_averages(close_series)
        for col in ma_df.columns:
            df[f"tech_{col}"] = ma_df[col].values

        # Bollinger
        bb_df = bollinger_bands(close_series)
        for col in ["bb_position", "bb_width", "bb_squeeze"]:
            df[f"tech_{col}"] = bb_df[col].values

        # Williams %R
        df["tech_wr_14"] = williams_r(high_series, low_series, close_series, 14).values

        # OBV
        df["tech_obv"] = obv(close_series, vol_series).values

        # CCI
        df["tech_cci_20"] = cci(high_series, low_series, close_series, 20).values

        results.append(df)

    if not results:
        return pd.DataFrame()

    return pd.concat(results, ignore_index=True)
