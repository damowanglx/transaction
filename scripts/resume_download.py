#!/usr/bin/env python
"""Resume interrupted history download — only fetch missing stocks."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging
from datetime import date, timedelta
from data.storage.clickhouse_client import get_clickhouse_client
from data.collector.akshare_fetcher import fetch_stock_list, fetch_all_stocks_daily

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

ch = get_clickhouse_client()
r = ch.client.query("SELECT DISTINCT ts_code FROM daily_bars")
done = set()
for row in r.result_rows:
    code = row[0].replace(".SH", "").replace(".SZ", "")
    done.add(code)

stocks = fetch_stock_list()
missing = [s["ts_code"] for s in stocks if s["ts_code"] not in done]

print(f"Done: {len(done)} | Missing: {len(missing)} | Total: {len(stocks)}")

if not missing:
    print("All stocks already downloaded!")
    sys.exit(0)

end = date.today()
start = end - timedelta(days=3 * 365)
print(f"Downloading {len(missing)} stocks, {start} to {end}")

processed, bars = fetch_all_stocks_daily(missing, start, end)
print(f"Complete! {processed} stocks, {bars} bars")
