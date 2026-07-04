#!/usr/bin/env python
"""
批量下载历史K线数据（近3年）。

Run: python scripts/download_history.py

数据量预估:
- A股约5000只
- 3年约750个交易日
- 总量约 375万行（含退市股）
- ClickHouse单表轻松承载
"""

import logging
import sys
from datetime import date, timedelta
from pathlib import Path

# Ensure project root is on sys.path regardless of where script is run
_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))

from config.settings import HISTORY_LOOKBACK_YEARS
from data.collector.akshare_fetcher import (
    fetch_all_stocks_daily,
    fetch_stock_list,
)
from data.storage.clickhouse_client import get_clickhouse_client

# Use centralized logging config
from config.settings import setup_logging
setup_logging()
logger = logging.getLogger("download_history")


def main():
    """Main entry point for bulk history download."""
    logger.info("=" * 60)
    logger.info("Starting historical data download (%d years back)", HISTORY_LOOKBACK_YEARS)
    logger.info("=" * 60)

    # 1. Health check — ensure ClickHouse is up
    ch = get_clickhouse_client()
    if not ch.ping():
        logger.error("ClickHouse is not reachable. Start with: docker compose up -d")
        sys.exit(1)

    logger.info("ClickHouse connection OK")

    # 2. Fetch stock list
    stocks = fetch_stock_list()
    if not stocks:
        logger.error("Failed to fetch stock list — check network / akshare version")
        sys.exit(1)

    codes = [s["ts_code"] for s in stocks]
    logger.info("Stock universe: %d codes", len(codes))

    # 3. Download batch historical data
    end_date = date.today()
    start_date = end_date - timedelta(days=int(HISTORY_LOOKBACK_YEARS * 365.25))

    logger.info("Date range: %s to %s", start_date.isoformat(), end_date.isoformat())

    processed, bars = fetch_all_stocks_daily(
        stock_list=codes,
        start_date=start_date,
        end_date=end_date,
    )

    # 4. Summary
    logger.info("=" * 60)
    logger.info("Download complete!")
    logger.info("  Stocks processed: %d / %d", processed, len(codes))
    logger.info("  Total bars inserted: %d", bars)

    # 5. Verify — quick count check
    try:
        result = ch.client.query("""
            SELECT
                min(trade_date) AS earliest,
                max(trade_date) AS latest,
                count() AS total_rows,
                uniqExact(ts_code) AS unique_stocks
            FROM daily_bars
        """)
        row = result.first_row
        logger.info("  DB stats: %s to %s, %s rows, %s unique stocks", row[0], row[1], row[2], row[3])
    except Exception:
        logger.warning("Could not run verification query")

    logger.info("=" * 60)


if __name__ == "__main__":
    main()
