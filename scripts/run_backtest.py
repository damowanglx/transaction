#!/usr/bin/env python
"""
Quick backtest runner.

Usage:
    python scripts/run_backtest.py trend_follow   # Run trend following backtest
    python scripts/run_backtest.py mean_revert    # Run mean reversion backtest
    python scripts/run_backtest.py all            # Run both and compare
"""

import logging
import sys
from datetime import date, timedelta

import pandas as pd
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))

from backtest.engine import BacktestEngine
from backtest.reporter import format_report, export_json
from config.settings import HISTORY_LOOKBACK_YEARS
from data.storage.clickhouse_client import get_clickhouse_client
from strategy.timing.trend_follow import TrendFollowStrategy
from strategy.timing.mean_revert import MeanRevertStrategy
from strategy.selector.stock_selector import StockSelector

from config.settings import setup_logging
setup_logging()
logging.getLogger("backtest.engine").setLevel(logging.WARNING)
logging.getLogger("risk.circuit_breaker").setLevel(logging.WARNING)
logger = logging.getLogger("run_backtest")


def load_data_from_clickhouse(start_date: date, end_date: date, sample_stocks: int = 100):
    """Load historical data from ClickHouse for backtesting."""
    ch = get_clickhouse_client()
    if not ch.ping():
        logger.error("ClickHouse not reachable")
        return None

    # Use last available date in DB if end_date is today or future
    r = ch.client.query("SELECT max(trade_date) FROM daily_bars")
    db_latest = r.first_row[0]
    if isinstance(db_latest, str):
        from datetime import datetime
        db_latest = datetime.strptime(db_latest, "%Y-%m-%d").date()
    actual_end = min(end_date, db_latest) if db_latest else end_date
    logger.info("Using end_date=%s (DB latest=%s)", actual_end, db_latest)

    # Get stock universe from latest date
    codes = ch.get_all_codes_on_date(actual_end)
    # Fallback: if CSI 300 shifted the latest date, try previous days
    if len(codes) < 100:
        for day_offset in range(1, 7):
            fallback_date = actual_end - timedelta(days=day_offset)
            codes = ch.get_all_codes_on_date(fallback_date)
            if len(codes) >= 100:
                break
    if not codes:
        logger.error("No stock codes found on %s", actual_end)
        return None

    # Sample for speed (0 = use all)
    import random
    random.seed(42)
    if sample_stocks > 0 and len(codes) > sample_stocks:
        codes = random.sample(codes, sample_stocks)

    logger.info("Loading full OHLCV for %d stocks from %s to %s", len(codes), start_date, actual_end)

    # Load full OHLCV from daily_bars (not just close from price_matrix)
    codes_tuple = tuple(codes)
    df = ch.client.query_df(
        "SELECT ts_code, trade_date, open, high, low, close, vol, amount, turnover_rate "
        "FROM daily_bars "
        "WHERE ts_code IN %(codes)s "
        "  AND trade_date >= %(start)s "
        "  AND trade_date <= %(end)s "
        "ORDER BY ts_code, trade_date",
        parameters={"codes": codes_tuple, "start": start_date.isoformat(), "end": actual_end.isoformat()},
    )

    if df.empty:
        logger.error("No data loaded")
        return None

    # Deduplicate (fallback date logic may cause duplicates)
    df = df.drop_duplicates(subset=["ts_code", "trade_date"])

    # Fill missing columns
    if "turnover_rate" not in df.columns:
        df["turnover_rate"] = 0.0
    if "amount" not in df.columns:
        df["amount"] = df["close"] * df["vol"]

    logger.info("Loaded %d rows, %d stocks", len(df), df["ts_code"].nunique())

    # Precompute indicators for speed (once, not per-day)
    from backtest.precompute import precompute_indicators
    df = precompute_indicators(df)
    logger.info("Precomputed indicators: %d columns", len(df.columns))
    return df


def load_benchmark(start_date: date, end_date: date) -> pd.DataFrame | None:
    """Load CSI 300 index data for benchmark comparison."""
    ch = get_clickhouse_client()
    try:
        df = ch.client.query_df(
            "SELECT trade_date, close FROM daily_bars "
            "WHERE ts_code = '000300.SH' "
            "AND trade_date >= %(start)s AND trade_date <= %(end)s "
            "ORDER BY trade_date",
            parameters={"start": start_date.isoformat(), "end": end_date.isoformat()},
        )
        if df.empty:
            logger.warning("No benchmark data for 000300.SH")
            return None
        return df
    except Exception:
        logger.warning("Failed to load benchmark", exc_info=True)
        return None


def main():
    strategy_name = sys.argv[1] if len(sys.argv) > 1 else "all"

    # Date range: last 2 years from DB latest (for speed)
    ch = get_clickhouse_client()
    r = ch.client.query("SELECT max(trade_date) FROM daily_bars")
    db_latest = r.first_row[0]
    from datetime import datetime
    if isinstance(db_latest, str):
        db_latest = datetime.strptime(db_latest, "%Y-%m-%d").date()
    end_date = db_latest
    start_date = end_date - timedelta(days=730)  # 2 years

    # Load data
    data = load_data_from_clickhouse(start_date, end_date, sample_stocks=500)
    if data is None:
        logger.error("Failed to load data — skipping")
        return

    # Load benchmark before strategies (needed for regime filter)
    benchmark = load_benchmark(start_date, end_date)

    strategies = {}

    if strategy_name in ("trend_follow", "all"):
        tf = TrendFollowStrategy("trend_follow")
        tf.init(ma_fast=5, ma_slow=20, ma_trend=60, top_n=10)
        strategies["trend_follow"] = tf

    if strategy_name in ("mean_revert", "all"):
        mr = MeanRevertStrategy("mean_revert")
        # Build market close series for regime filter
        market_close = None
        if benchmark is not None and not benchmark.empty:
            market_close = pd.Series(
                benchmark.set_index("trade_date")["close"].values,
                index=benchmark["trade_date"].tolist(),
            )
        mr.init(bb_period=23, bb_std=3.0, rsi_oversold=26, rsi_overbought=65,
                stop_loss=0.05, take_profit=0.10, top_n=10,
                min_price=5.0, min_turnover=1.0)
        strategies["mean_revert"] = mr

    if strategy_name in ("selector", "all"):
        # Pre-compute factors once (not per-day)
        logger.info("Pre-computing factors for selector...")
        from strategy.factors import compute_all_factors
        factor_data = compute_all_factors(data)
        logger.info("Factors ready: %d columns", len(factor_data.columns))

        sel = StockSelector("multi_factor")
        sel.init(
            factors=["mom_60", "mom_120", "rsi_14", "vol_20", "vol_ratio"],
            top_n=10,
            min_ic=0.01,
        )
        strategies["selector"] = sel
        data = factor_data  # Use factored data for this strategy

    engine = BacktestEngine(initial_cash=200_000)

    for name, strat in strategies.items():
        logger.info("=" * 60)
        logger.info("Running: %s", name)
        logger.info("=" * 60)

        result = engine.run(strat, data, start_date, end_date, benchmark_data=benchmark)

        print("\n")
        report = format_report(result, name)
        # Replace characters that don't work in Windows GBK terminal
        safe = report.replace("¥", "CNY").replace("万", "wan").replace("千", "qian")
        try:
            print(safe)
        except UnicodeEncodeError:
            print(safe.encode("ascii", errors="replace").decode("ascii"))

        # Export
        export_json(result, f"reports/{name}_result.json")
        logger.info("Report exported to reports/%s_result.json", name)


if __name__ == "__main__":
    # Create reports dir
    Path("reports").mkdir(exist_ok=True)
    main()
