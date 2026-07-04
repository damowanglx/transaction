#!/usr/bin/env python
"""Automated trader — QMT data → strategy → risk → order.
Run: py -3.12 live/trader.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json
import logging
import time
from datetime import date, timedelta
import numpy as np
import pandas as pd

from config.settings import setup_logging
setup_logging()
logger = logging.getLogger("trader")

from xtquant import xtdata, xttrader

from strategy.timing.mean_revert import MeanRevertStrategy
from risk.risk_engine import RiskEngine
from config.risk_params import get_risk_config


def load_data(n_stocks: int = 2000):
    """Load latest market data from QMT."""
    codes = xtdata.get_stock_list_in_sector("沪深A股")  # 沪深A股
    today = date.today()
    end_str = today.strftime("%Y%m%d")
    start_str = (today - timedelta(days=365)).strftime("%Y%m%d")

    logger.info("Downloading %d stocks from QMT...", len(codes))
    xtdata.download_history_data2(codes, "1d", start_str, end_str, callback=lambda d: None)
    time.sleep(3)

    rows = []
    for code in codes:
        data = xtdata.get_market_data_ex(
            field_list=["open", "high", "low", "close", "volume", "amount"],
            stock_list=[code], period="1d",
            start_time=start_str, end_time=end_str, count=-1,
        )
        if data and code in data:
            df = data[code]
            if not df.empty:
                for idx, row in df.iterrows():
                    dt_val = idx.date() if hasattr(idx, 'date') else pd.Timestamp(idx).date()
                    rows.append({
                        "ts_code": code, "trade_date": dt_val,
                        "open": row["open"], "high": row["high"],
                        "low": row["low"], "close": row["close"],
                        "vol": float(row.get("volume", 0) or 0),
                        "amount": float(row.get("amount", 0) or 0),
                        "turnover_rate": 0.0,
                    })

    df = pd.DataFrame(rows)
    return df.drop_duplicates(subset=["ts_code", "trade_date"])


def load_positions():
    """Load current positions from file or query from broker."""
    pos_file = Path(__file__).resolve().parent.parent / "scripts" / "positions.json"
    try:
        if pos_file.exists():
            return json.loads(pos_file.read_text())
    except Exception:
        pass
    return {}


def save_positions(positions: dict):
    pos_file = Path(__file__).resolve().parent.parent / "scripts" / "positions.json"
    from config.settings import atomic_write_json
    atomic_write_json(str(pos_file), positions, indent=2, ensure_ascii=False)


def main(dry_run: bool = True):
    logger.info("=" * 50)
    logger.info("Auto Trader — %s — %s", date.today(),
                 "DRY RUN (no orders)" if dry_run else "LIVE")
    logger.info("=" * 50)

    # 1. Load data
    df = load_data()
    max_dt = df["trade_date"].max()
    current_date = max_dt.date() if hasattr(max_dt, 'date') else max_dt
    logger.info("Data: %d rows, %d stocks, latest=%s",
                len(df), df["ts_code"].nunique(), current_date)

    # 2. Load existing positions
    positions = load_positions()
    logger.info("Positions: %d held", len(positions))

    # 3. Run strategy
    strat = MeanRevertStrategy("auto_mr")
    strat.init(
        bb_period=23, bb_std=3.0, rsi_oversold=26, rsi_overbought=65,
        stop_loss=0.05, take_profit=0.10, top_n=10,
        min_price=5.0, min_turnover=1.0,
        current_holdings=positions,
    )
    signals = strat.on_data(df, current_date)

    buys = [s for s in signals if s.signal_type.value == "BUY"]
    sells = [s for s in signals if s.signal_type.value == "SELL"]
    logger.info("Signals: %d buys, %d sells", len(buys), len(sells))

    # 4. Calculate real portfolio value from positions + estimated cash
    latest = df.sort_values("trade_date").groupby("ts_code").last()
    prices = dict(zip(latest.index, latest["close"]))
    holdings_mv = sum(
        prices.get(code, pos.get("entry_price", 0))
        * pos.get("volume", 0)
        for code, pos in positions.items()
        if isinstance(pos, dict)
    )
    estimated_cash = 200_000 * 0.20  # Assume 20% cash if can't query broker
    total_value = holdings_mv + estimated_cash
    logger.info("Est. portfolio: MV=%.0f + Cash~=%.0f = %.0f", holdings_mv, estimated_cash, total_value)

    risk = RiskEngine(get_risk_config("default"))
    risk.set_initial_capital(total_value)
    executed = 0
    rejected = 0
    executed_buys = []  # Track only successfully executed buys
    executed_sells = []  # Track executed sells

    for sig in signals:
        price = prices.get(sig.ts_code, 0)
        if price <= 0:
            logger.warning("SKIP %s: no price data", sig.ts_code)
            rejected += 1
            continue

        budget = total_value * sig.target_weight
        result = risk.check_order(
            direction=sig.signal_type.value, ts_code=sig.ts_code,
            price=price, budget=budget, total_value=total_value,
            current_positions=positions, daily_pnl=0, daily_pnl_pct=0,
            today=current_date,
        )

        if not result.allowed:
            rejected += 1
            logger.warning("REJECTED %s %s: %s", sig.signal_type.value, sig.ts_code, result.reason)
            continue

        # Calculate shares
        if sig.signal_type.value == "BUY":
            shares = int(budget / price / 100) * 100
        else:
            pos = positions.get(sig.ts_code, {})
            shares = pos.get("volume", 0) if isinstance(pos, dict) else 0

        if shares <= 0:
            logger.warning("SKIP %s: zero shares (budget=%.0f price=%.2f)", sig.ts_code, budget, price)
            rejected += 1
            continue

        # Place order (or simulate in dry run)
        if dry_run:
            logger.info("SIM %s %s %d@%.2f — %s",
                        sig.signal_type.value, sig.ts_code, shares, price, sig.reason[:40])
        else:
            try:
                if sig.signal_type.value == "BUY":
                    order_id = xttrader.order_stock(
                        "", sig.ts_code, 0, shares, 0, price, "mean_revert", "auto"
                    )
                else:
                    order_id = xttrader.order_stock(
                        "", sig.ts_code, 0, shares, 0, price, "mean_revert", "auto"
                    )
                logger.info("ORDER %s %s %d@%.2f → #%s",
                            sig.signal_type.value, sig.ts_code, shares, price, order_id)
            except Exception as e:
                logger.error("ORDER FAILED %s: %s", sig.ts_code, e)
                rejected += 1
                continue

        executed += 1
        if sig.signal_type.value == "BUY":
            executed_buys.append((sig.ts_code, shares, price))
        else:
            executed_sells.append(sig.ts_code)

    # 5. Update positions — ONLY for executed orders
    new_positions = {}
    for code, shares, price in executed_buys:
        new_positions[code] = {
            "volume": shares,
            "entry_price": price,
            "buy_date": str(current_date),
            "amount": shares * price,
        }
    sell_codes = set(executed_sells)
    for code, pos in positions.items():
        if code not in sell_codes and code not in new_positions:
            new_positions[code] = pos if isinstance(pos, dict) else {"volume": pos, "entry_price": 0, "amount": 0}
    save_positions(new_positions)

    # 6. Summary
    print("\n" + "=" * 50)
    print(f"  Auto Trader Summary — {current_date}")
    print(f"  {'Buy':<10} {len(buys)}")
    print(f"  {'Sell':<10} {len(sells)}")
    print(f"  {'Executed':<10} {executed}")
    print(f"  {'Rejected':<10} {rejected}")
    print(f"  {'Holdings':<10} {len(new_positions)}")
    print("=" * 50)


if __name__ == "__main__":
    dry = "--live" not in sys.argv  # Default dry-run
    main(dry)
