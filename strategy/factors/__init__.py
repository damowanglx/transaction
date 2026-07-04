"""
Factor computation pipeline.

Connects raw OHLCV data → factor calculation → selector-ready DataFrame.
"""

import logging

import pandas as pd

from strategy.factors.momentum import momentum_factor_bundle
from strategy.factors.volatility import volatility_factor_bundle
from strategy.factors.turnover import turnover_factor_bundle
from strategy.factors.technical import technical_factor_bundle

logger = logging.getLogger(__name__)


def compute_all_factors(
    price_df: pd.DataFrame,
    benchmark_df: pd.DataFrame | None = None,
    market_prices: pd.Series | None = None,
) -> pd.DataFrame:
    """Compute all four factor groups and merge into a single DataFrame.

    Args:
        price_df: DataFrame with columns
            [ts_code, trade_date, open, high, low, close, vol, amount, turnover_rate]
        benchmark_df: Optional benchmark close data for relative strength.
        market_prices: Optional market index close for beta.

    Returns:
        DataFrame with all factor columns merged by (ts_code, trade_date).
        Ready to pass directly to StockSelector.on_data().
    """
    logger.info("Computing all factors for %d stocks", price_df["ts_code"].nunique())

    momentum = momentum_factor_bundle(price_df, benchmark_df)
    volatility = volatility_factor_bundle(price_df, market_prices)
    turnover = turnover_factor_bundle(price_df)
    technical = technical_factor_bundle(price_df)

    # Merge all factor groups on (trade_date, ts_code)
    result = price_df[["ts_code", "trade_date", "close", "vol", "amount"]].copy()

    for name, df in [
        ("momentum", momentum),
        ("volatility", volatility),
        ("turnover", turnover),
        ("technical", technical),
    ]:
        if df.empty:
            logger.warning("Factor group '%s' returned empty", name)
            continue
        result = result.merge(df, on=["ts_code", "trade_date"], how="left")

    logger.info(
        "Factor pipeline complete: %d rows, %d columns",
        len(result), len(result.columns),
    )
    return result
