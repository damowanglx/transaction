#!/usr/bin/env python
"""Precompute and cache indicators for full universe — speed up daily signals.

Run once daily after data backfill. Creates a parquet cache with all indicators
so daily_signal.py can skip per-stock computation entirely.

Speed improvement: 4-5 min → ~15 seconds for 5000 stocks.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging, time
from datetime import date, timedelta
import numpy as np
import pandas as pd

from config.settings import setup_logging
setup_logging()
logger = logging.getLogger("precompute")

from data.storage.clickhouse_client import get_clickhouse_client

CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
CACHE_FILE = CACHE_DIR / "indicators.csv"
LOOKBACK_DAYS = 120  # Enough for 60-day MA + 20-day BB + 14-day RSI
BB_PERIOD = 23
BB_STD = 3.0


def load_full_universe():
    """Load full universe OHLCV data from ClickHouse."""
    ch = get_clickhouse_client()
    r = ch.client.query("SELECT max(trade_date) FROM daily_bars WHERE ts_code != '000300.SH'")
    latest = r.first_row[0]
    if isinstance(latest, str):
        from datetime import datetime
        latest = datetime.strptime(latest, "%Y-%m-%d").date()

    start = latest - timedelta(days=LOOKBACK_DAYS)
    logger.info("Loading %s to %s...", start, latest)

    # Load ALL stocks in one query — no sampling
    df = ch.client.query_df(
        "SELECT ts_code, trade_date, open, high, low, close, vol, amount, turnover_rate "
        "FROM daily_bars "
        "WHERE trade_date >= %(start)s AND trade_date <= %(end)s "
        "  AND ts_code != '000300.SH' "
        "ORDER BY ts_code, trade_date",
        parameters={"start": start.isoformat(), "end": latest.isoformat()},
    )
    df = df.drop_duplicates(subset=["ts_code", "trade_date"])
    logger.info("Loaded %d rows, %d stocks", len(df), df["ts_code"].nunique())
    return df, latest


def compute_indicators_vectorized(df: pd.DataFrame):
    """Compute all strategy indicators — explicit loop with vectorized per-stock ops."""
    codes = df["ts_code"].unique()
    logger.info("Computing indicators for %d stocks...", len(codes))

    t0 = time.time()
    results = []

    for code in codes:
        group = df[df["ts_code"] == code].sort_values("trade_date")
        if len(group) < BB_PERIOD + 10:
            results.append(group)
            continue

        close = group["close"]
        vol = group["vol"]

        # Bollinger Bands (3σ)
        ma = close.rolling(BB_PERIOD).mean()
        std = close.rolling(BB_PERIOD).std()
        upper = ma + BB_STD * std
        lower = ma - BB_STD * std
        band_range = upper - lower
        group = group.copy()
        group["bb_position_3"] = (close - lower) / band_range.replace(0, np.nan)

        # RSI 14
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        avg_gain = gain.rolling(14).mean()
        avg_loss = loss.rolling(14).mean()
        rs = avg_gain / avg_loss.clip(lower=1e-10)
        group["rsi_14"] = 100.0 - (100.0 / (1.0 + rs))

        # MA20
        group["ma_20"] = close.rolling(20).mean()
        group["dev_from_ma20"] = (close - group["ma_20"]) / group["ma_20"].replace(0, np.nan)

        # Volume ratio
        group["vol_ratio"] = vol.rolling(5).mean() / vol.rolling(20).mean().clip(lower=1e-10)

        # ATR 14
        h, l, c = group["high"], group["low"], close
        tr = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
        group["atr_14"] = tr.rolling(14).mean()

        # Trend indicators
        group["ma_5"] = close.rolling(5).mean()
        group["ma_60"] = close.rolling(60).mean()
        group["price_vs_ma60"] = (close - group["ma_60"]) / group["ma_60"].replace(0, np.nan)

        # Volatility
        ret = close.pct_change()
        group["vol_20"] = ret.rolling(20).std() * np.sqrt(244)

        results.append(group)

    result = pd.concat(results, ignore_index=True)
    elapsed = time.time() - t0
    logger.info("Indicators computed in %.1f seconds (%.0f stocks/sec)",
                 elapsed, len(codes) / elapsed)
    return result


def save_cache(df: pd.DataFrame, data_date: date):
    """Save computed indicators to parquet cache."""
    CACHE_DIR.mkdir(exist_ok=True)

    keep_cols = [c for c in [
        "ts_code", "trade_date", "close", "vol", "amount",
        "bb_position_3", "rsi_14", "ma_20", "dev_from_ma20",
        "vol_ratio", "atr_14", "ma_5", "ma_60", "price_vs_ma60", "vol_20",
        "turnover_rate",
    ] if c in df.columns]
    cache_df = df[keep_cols].copy()
    cache_df["cached_at"] = str(data_date)

    cache_df.to_csv(CACHE_FILE, index=False)
    logger.info("Cache saved: %s (%.1f MB, %d rows)",
                 CACHE_FILE, CACHE_FILE.stat().st_size / 1e6, len(cache_df))


def main():
    df, latest = load_full_universe()

    # Compute indicators
    df_with_indicators = compute_indicators_vectorized(df)

    # Save cache
    save_cache(df_with_indicators, latest)

    # Quick stats
    last_day = df_with_indicators[df_with_indicators["trade_date"] == latest]
    oversold = (last_day["rsi_14"] < 30).sum()
    logger.info("Latest day: %d stocks | RSI<30: %d | Cache ready",
                 len(last_day), oversold)

    print(f"\n✅ 指标缓存已生成: {CACHE_FILE}")
    print(f"   股票数: {df['ts_code'].nunique()}")
    print(f"   日期范围: {df['trade_date'].min()} → {df['trade_date'].max()}")
    print(f"   下次运行 daily_signal.py 将自动使用缓存 (无需重复计算)")


if __name__ == "__main__":
    main()
