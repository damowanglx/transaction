#!/usr/bin/env python
"""Quick data backfill using baostock — faster and more reliable than AkShare.
Downloads last N days for all A-share stocks and inserts into ClickHouse.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging, time
from datetime import date, timedelta
import pandas as pd
import baostock as bs

from config.settings import setup_logging
setup_logging()
logger = logging.getLogger("backfill")

from data.storage.clickhouse_client import get_clickhouse_client


def download_range(codes: list[str], start: str, end: str) -> int:
    """Download daily bars for all codes in date range. Returns total rows."""
    ch = get_clickhouse_client()
    total = 0
    failed = 0

    for i, code in enumerate(codes):
        try:
            # baostock format: sh.600000 or sz.000001
            market = "sh" if code.startswith(("6", "9")) else "sz"
            bs_code = f"{market}.{code.replace('.SH','').replace('.SZ','').replace('.BJ','')}"

            rs = bs.query_history_k_data_plus(
                bs_code, "date,open,high,low,close,preclose,volume,amount,turn",
                start_date=start, end_date=end, frequency="d", adjustflag="2"
            )

            if rs.error_code != "0":
                failed += 1
                continue

            rows = []
            while rs.next():
                rows.append(rs.get_row_data())

            if not rows:
                continue

            df = pd.DataFrame(rows, columns=rs.fields)

            # Convert to ClickHouse format
            records = []
            ts_code = f"{code}.SH" if code.startswith(("6", "9")) else f"{code}.SZ"
            # Handle BJ prefix
            if code.startswith("8"):
                ts_code = f"{code}.BJ"

            for _, row in df.iterrows():
                try:
                    trade_date = pd.Timestamp(row["date"]).date()
                    vol = float(row["volume"]) if row["volume"] else 0
                    amt = float(row["amount"]) if row["amount"] else 0
                    if vol <= 0 or amt <= 0:
                        continue  # Skip suspended/holiday days
                    records.append({
                        "ts_code": ts_code,
                        "trade_date": trade_date,
                        "open": float(row["open"]) if row["open"] else 0,
                        "high": float(row["high"]) if row["high"] else 0,
                        "low": float(row["low"]) if row["low"] else 0,
                        "close": float(row["close"]) if row["close"] else 0,
                        "pre_close": float(row["preclose"]) if row["preclose"] else 0,
                        "change": 0.0,
                        "pct_chg": 0.0,
                        "vol": vol,
                        "amount": amt,
                        "turnover_rate": float(row["turn"]) if row["turn"] else 0.0,
                        "pe": None, "pb": None, "is_st": 0,
                    })
                except Exception:
                    continue

            if records:
                ch.insert_daily_bars(records)
                total += len(records)

            if (i + 1) % 500 == 0:
                logger.info("Progress: %d/%d stocks, %d bars", i + 1, len(codes), total)

        except Exception:
            failed += 1
            continue

    logger.info("Done: %d bars from %d stocks (%d failed)", total, len(codes) - failed, failed)
    return total


def main():
    lg = bs.login()
    if lg.error_code != "0":
        logger.error("baostock login failed: %s", lg.error_msg)
        return
    logger.info("baostock login OK")

    # Get stock universe from ClickHouse
    ch = get_clickhouse_client()
    r = ch.client.query("SELECT DISTINCT ts_code FROM daily_bars WHERE trade_date >= '2026-06-20'")
    codes = [row[0].replace(".SH","").replace(".SZ","").replace(".BJ","") for row in r.result_rows]
    codes = list(set(codes))  # Dedup
    # Add CSI 300 benchmark to download
    all_to_download = codes + ["000300"]
    logger.info("Downloading %d stocks + CSI 300 benchmark", len(codes))

    # Download the last week (June 29 - July 4)
    today = date.today()
    start = (today - timedelta(days=10)).strftime("%Y-%m-%d")
    end = today.strftime("%Y-%m-%d")
    logger.info("Range: %s to %s", start, end)

    total = download_range(all_to_download, start, end)
    logger.info("Backfill complete: %d bars", total)

    bs.logout()


if __name__ == "__main__":
    main()
