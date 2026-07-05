#!/usr/bin/env python
"""Multi-factor selector standalone backtest — separate from mean_revert."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datetime import date, timedelta
import logging

from config.settings import setup_logging
setup_logging()
logging.getLogger("backtest.engine").setLevel(logging.WARNING)
logging.getLogger("risk.circuit_breaker").setLevel(logging.WARNING)
logger = logging.getLogger("selector_bt")

from backtest.engine import BacktestEngine
from strategy.selector.stock_selector import StockSelector
from strategy.factors import compute_all_factors
from data.storage.clickhouse_client import get_clickhouse_client


def main():
    ch = get_clickhouse_client()
    r = ch.client.query("SELECT max(trade_date) FROM daily_bars WHERE ts_code != '000300.SH'")
    db_latest = r.first_row[0]
    if isinstance(db_latest, str):
        from datetime import datetime
        db_latest = datetime.strptime(db_latest, "%Y-%m-%d").date()
    end = db_latest
    start = end - timedelta(days=730)

    codes = ch.get_all_codes_on_date(end)
    codes = [c for c in codes if c != '000300.SH']
    import random
    import time as _time
    random.seed(int(_time.time() * 1000) % (2**31))
    codes = random.sample(codes, min(len(codes), 500))

    logger.info("Loading %d stocks from %s to %s", len(codes), start, end)
    df = ch.client.query_df(
        "SELECT ts_code, trade_date, open, high, low, close, vol, amount, turnover_rate "
        "FROM daily_bars WHERE ts_code IN %(codes)s "
        "AND trade_date >= %(start)s AND trade_date <= %(end)s "
        "ORDER BY ts_code, trade_date",
        parameters={"codes": tuple(codes), "start": start.isoformat(), "end": end.isoformat()},
    )
    df = df.drop_duplicates(subset=["ts_code", "trade_date"])
    logger.info("Loaded %d rows", len(df))

    # Pre-compute factors ONCE
    logger.info("Computing factors...")
    factor_df = compute_all_factors(df)
    logger.info("Factors ready: %d rows, %d columns", len(factor_df), len(factor_df.columns))

    sel = StockSelector("multi_factor")
    sel.init(
        factors=["mom_60", "mom_120", "rsi_14", "vol_20", "vol_ratio"],
        top_n=10,
        min_ic=0.01,
    )

    engine = BacktestEngine(initial_cash=200_000)
    result = engine.run(sel, factor_df, start, end)

    from backtest.reporter import format_report
    report = format_report(result, "Multi-Factor Selector")
    safe = report.replace("¥", "CNY").replace("万", "wan").replace("千", "qian")
    try:
        print(safe)
    except Exception:
        print(report.encode("ascii", errors="replace").decode("ascii"))


if __name__ == "__main__":
    main()
