#!/usr/bin/env python
"""Incremental data update — only download data since last update.

Run daily after close: python scripts/download_incremental.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging
from datetime import date, timedelta

from config.settings import setup_logging
setup_logging()
logger = logging.getLogger("download_incremental")

from data.collector.akshare_fetcher import fetch_stock_list, fetch_all_stocks_daily
from data.storage.clickhouse_client import get_clickhouse_client


def main():
    ch = get_clickhouse_client()
    if not ch.ping():
        logger.error("ClickHouse not reachable")
        return

    # Find last update date
    r = ch.client.query("SELECT max(trade_date) FROM daily_bars")
    db_latest = r.first_row[0]
    if isinstance(db_latest, str):
        from datetime import datetime
        db_latest = datetime.strptime(db_latest, "%Y-%m-%d").date()

    today = date.today()
    if db_latest >= today:
        logger.info("Data already up to date (latest: %s)", db_latest)
        return

    start = db_latest - timedelta(days=3)  # Small overlap to catch corrections
    end = today
    logger.info("Incremental update: %s → %s (%d days)", start, end, (end - start).days)

    # Get stock list
    stocks = fetch_stock_list()
    codes = [s["ts_code"] for s in stocks]
    logger.info("Updating %d stocks", len(codes))

    # Download only the delta
    processed, bars = fetch_all_stocks_daily(codes, start, end)
    logger.info("Done: %d stocks, %d new bars", processed, bars)


if __name__ == "__main__":
    main()
