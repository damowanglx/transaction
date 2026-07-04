#!/usr/bin/env python
"""Daily signals using QMT xtdata — no AkShare proxy needed."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json, logging, random, time
from datetime import date, timedelta
import numpy as np
import pandas as pd

from config.settings import setup_logging
setup_logging()
logger = logging.getLogger("qmt_signal")

from xtquant import xtdata

from strategy.timing.mean_revert import MeanRevertStrategy


def main(dry_run: bool = True):
    today = date.today()
    logger.info("QMT Signal — %s", today)

    # 1. Get stock universe from QMT
    all_codes = xtdata.get_stock_list_in_sector("沪深A股")
    logger.info("QMT universe: %d stocks", len(all_codes))

    # 2. Download 1 year history for a sample (full download would be slow)
    sample = all_codes  # Use full universe — QMT is fast enough
    logger.info("Downloading %d stocks history...", len(sample))

    end_str = today.strftime("%Y%m%d")
    start_str = (today - timedelta(days=365)).strftime("%Y%m%d")

    xtdata.download_history_data2(sample, "1d", start_str, end_str, callback=lambda d: None)
    time.sleep(2)

    # 3. Build DataFrame from QMT data
    rows = []
    for code in sample:
        data = xtdata.get_market_data_ex(
            field_list=["open", "high", "low", "close", "volume", "amount"],
            stock_list=[code],
            period="1d",
            start_time=start_str,
            end_time=end_str,
            count=-1,
        )
        if data and code in data:
            df = data[code]
            if not df.empty:
                for idx, row in df.iterrows():
                    rows.append({
                        "ts_code": code,
                        "trade_date": pd.Timestamp(idx).date(),
                        "open": row.get("open", 0),
                        "high": row.get("high", 0),
                        "low": row.get("low", 0),
                        "close": row.get("close", 0),
                        "vol": float(row.get("volume", 0) or 0),
                        "amount": float(row.get("amount", 0) or 0),
                        "turnover_rate": 0.0,
                    })

    df = pd.DataFrame(rows)
    if df.empty:
        logger.error("No data loaded")
        return

    df = df.drop_duplicates(subset=["ts_code", "trade_date"])
    logger.info("Loaded %d rows, %d stocks", len(df), df["ts_code"].nunique())

    # 4. Load positions
    pos_file = Path(__file__).resolve().parent / "positions.json"
    current_positions = {}
    try:
        if pos_file.exists():
            current_positions = json.loads(pos_file.read_text())
    except Exception:
        pass

    # 5. Build lookups
    last_day = df[df["trade_date"] == df["trade_date"].max()]
    price_lookup = dict(zip(last_day["ts_code"], last_day["close"]))

    # Name lookup from QMT (or use code as fallback)
    name_lookup = {}
    try:
        xtdata.download_sector_data()
        info = xtdata.get_stock_list_in_sector("沪深A股")
        # Use stock code as name fallback — QMT doesn't provide Chinese names directly
    except Exception:
        pass

    # ATR stop lookup
    stop_lookup = {}
    for code in last_day["ts_code"].unique():
        s = df[df["ts_code"] == code].sort_values("trade_date")
        if len(s) < 20:
            continue
        h, l, c = s["high"], s["low"], s["close"]
        tr = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14).mean().iloc[-1]
        price = c.iloc[-1]
        if price > 0 and atr > 0:
            stop_lookup[code] = max(price - 2.0 * atr, price * 0.92)

    # 6. Run strategy
    strat = MeanRevertStrategy("qmt_mr")
    strat.init(
        bb_period=23, bb_std=3.0, rsi_oversold=26, rsi_overbought=65,
        stop_loss=0.05, take_profit=0.10, top_n=10,
        min_price=5.0, min_turnover=1.0,
        use_atr_stop=True, use_vol_target=True,
        current_holdings=current_positions,
    )

    signals = strat.on_data(df, df["trade_date"].max().date())

    # 7. Print signals
    from scripts.daily_signal import print_signals
    print_signals(signals, dry_run, price_lookup, name_lookup, stop_lookup,
                  total_capital=200_000, top_n=10)

    # 8. Save positions
    new_positions = {}
    for s in signals:
        if s.signal_type.value == "BUY":
            new_positions[s.ts_code] = {
                "entry_price": price_lookup.get(s.ts_code, 0.0),
                "buy_date": str(today),
            }
    sell_codes = {s.ts_code for s in signals if s.signal_type.value == "SELL"}
    for code, pos in current_positions.items():
        if code not in sell_codes and code not in new_positions:
            new_positions[code] = pos
    pos_file.write_text(json.dumps(new_positions, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv or len(sys.argv) < 2
    main(dry)
