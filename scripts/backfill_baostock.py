#!/usr/bin/env python
"""Fast data backfill using baostock — multi-threaded for speed.
Downloads last N days for all A-share stocks in parallel.

Speed: ~25min (single) → ~4min (8 threads) for 5500 stocks.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging, time
from datetime import date, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
import baostock as bs

from config.settings import setup_logging
setup_logging()
logger = logging.getLogger("backfill")

from data.storage.clickhouse_client import get_clickhouse_client

THREADS = 1  # Single-thread: avoids ClickHouse concurrent write issues
BATCH_SIZE = 100


def download_one(code: str, start: str, end: str) -> list[dict]:
    """Download one stock's daily bars. Returns list of record dicts."""
    market = "sh" if code.startswith(("6", "9")) else "sz"
    bs_code = f"{market}.{code.replace('.SH','').replace('.SZ','').replace('.BJ','')}"

    try:
        rs = bs.query_history_k_data_plus(
            bs_code, "date,open,high,low,close,preclose,volume,amount,turn",
            start_date=start, end_date=end, frequency="d", adjustflag="2"
        )
        if rs.error_code != "0":
            return []

        rows = []
        while rs.next():
            rows.append(rs.get_row_data())

        if not rows:
            return []

        records = []
        market_suffix = ".SH" if code.startswith(("6", "9")) else ".SZ"
        if code.startswith("8"):
            market_suffix = ".BJ"

        for row in rows:
            try:
                trade_date = pd.Timestamp(row[0]).date()
                vol = float(row[5]) if row[5] and row[5] != '' else 0
                amt = float(row[6]) if row[6] and row[6] != '' else 0
                if vol <= 0 or amt <= 0:
                    continue
                records.append({
                    "ts_code": code + market_suffix,
                    "trade_date": trade_date,
                    "open": float(row[1]) if row[1] else 0,
                    "high": float(row[2]) if row[2] else 0,
                    "low": float(row[3]) if row[3] else 0,
                    "close": float(row[4]) if row[4] else 0,
                    "pre_close": float(row[4]) if row[4] else 0,
                    "change": 0.0, "pct_chg": 0.0,
                    "vol": vol, "amount": amt,
                    "turnover_rate": float(row[7]) if row[7] and row[7] != '' else 0.0,
                    "pe": None, "pb": None, "is_st": 0,
                })
            except Exception:
                continue
        return records
    except Exception:
        return []


def insert_batch(ch, records: list[dict]):
    """Insert a batch of records into ClickHouse."""
    if not records:
        return 0
    try:
        ch.insert_daily_bars(records)
        return len(records)
    except Exception:
        return 0


def main():
    lg = bs.login()
    if lg.error_code != "0":
        logger.error("baostock login failed: %s", lg.error_msg)
        return
    logger.info("baostock login OK")

    ch = get_clickhouse_client()
    r = ch.client.query(
        "SELECT DISTINCT ts_code FROM daily_bars WHERE trade_date >= '2026-06-20'"
    )
    codes = list(set(
        row[0].replace(".SH","").replace(".SZ","").replace(".BJ","")
        for row in r.result_rows
    ))
    # Add CSI 300
    all_codes = codes + ["000300"]
    logger.info("Downloading %d stocks with %d threads", len(all_codes), THREADS)

    today = date.today()
    start = (today - timedelta(days=10)).strftime("%Y-%m-%d")
    end = today.strftime("%Y-%m-%d")
    logger.info("Range: %s → %s", start, end)

    total_bars = 0
    done = 0
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=THREADS) as pool:
        futures = {pool.submit(download_one, c, start, end): c for c in all_codes}

        for f in as_completed(futures):
            records = f.result()
            if records:
                n = insert_batch(ch, records)
                total_bars += n
            done += 1
            if done % 500 == 0:
                elapsed = time.time() - t0
                logger.info("Progress: %d/%d | %d bars | %.0fs | ~%.0fs remaining",
                             done, len(all_codes), total_bars,
                             elapsed, elapsed / done * (len(all_codes) - done))

    elapsed = time.time() - t0
    logger.info("Complete: %d bars in %.0f seconds (%.0f stocks/sec)",
                 total_bars, elapsed, len(all_codes) / elapsed)

    bs.logout()


if __name__ == "__main__":
    main()
