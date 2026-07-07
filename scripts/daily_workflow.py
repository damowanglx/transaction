#!/usr/bin/env python
"""Daily trading workflow — runs at 9:00, 11:30, 14:30, 15:00.

Usage:
    python scripts/daily_workflow.py morning   # 9:00 盘前
    python scripts/daily_workflow.py midday    # 11:30 午间
    python scripts/daily_workflow.py afternoon # 14:30 尾盘
    python scripts/daily_workflow.py close     # 15:00 收盘
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json, logging, os
from datetime import date, datetime

from config.settings import setup_logging
setup_logging()
logger = logging.getLogger("workflow")

POSITIONS_FILE = Path(__file__).resolve().parent / "positions.json"
TOTAL_CAPITAL = 200_000
STOP_LOSS_PCT = 0.05


def load_positions():
    return json.loads(POSITIONS_FILE.read_text()) if POSITIONS_FILE.exists() else {}


def get_latest_prices():
    """Get latest close prices from ClickHouse."""
    try:
        from data.storage.clickhouse_client import get_clickhouse_client
        ch = get_clickhouse_client()
        if ch.ping():
            r = ch.client.query("SELECT max(trade_date) FROM daily_bars WHERE ts_code != '000300.SH'")
            latest = r.first_row[0]
            if isinstance(latest, str):
                from datetime import datetime as dt
                latest = dt.strptime(latest, "%Y-%m-%d").date()
            codes = list(load_positions().keys())
            if codes:
                df = ch.client.query_df(
                    "SELECT ts_code, close FROM daily_bars "
                    "WHERE ts_code IN %(codes)s AND trade_date = %(d)s",
                    parameters={"codes": tuple(codes), "d": latest.isoformat()},
                )
                return latest, dict(zip(df["ts_code"], df["close"]))
    except Exception as e:
        logger.warning("Price lookup: %s", e)
    return None, {}


def morning_routine():
    """9:00 AM — Pre-market check."""
    print(f"\n{'='*60}")
    print(f"  🌅 盘前检查 — {datetime.now().strftime('%H:%M')}")
    print(f"{'='*60}")

    positions = load_positions()
    if not positions:
        print("  无持仓")
        return

    trade_date, prices = get_latest_prices()

    # Check stop-loss and near-stop
    alerts = []
    for code, pos in positions.items():
        entry = pos["entry_price"]
        vol = pos["volume"]
        name = pos.get("name", "")
        stop = pos.get("stop_loss", entry * (1 - STOP_LOSS_PCT))
        tp = pos.get("take_profit", entry * 1.15)

        price = prices.get(code, entry)
        if price <= 0:
            continue

        pnl_pct = (price - entry) / entry * 100
        mv = price * vol

        # Check gap from stop
        gap_to_stop = (price - stop) / stop * 100 if stop > 0 else 0

        if price <= stop:
            alerts.append(f"🔴 {code} {name} 已触发止损! ¥{price:.2f} ≤ ¥{stop:.2f} → 今日必须卖出")
        elif gap_to_stop < 3:
            alerts.append(f"🟡 {code} {name} 接近止损 ¥{price:.2f} (止损¥{stop:.2f} 差{gap_to_stop:.1f}%) → 盯盘")
        elif pnl_pct >= 14:
            alerts.append(f"💰 {code} {name} 接近止盈 +{pnl_pct:.1f}% → 考虑卖出")

    # Summary
    total_mv = sum(prices.get(c, pos["entry_price"]) * pos["volume"] for c, pos in positions.items())
    print(f"  持仓: {len(positions)}只 | 市值: ¥{total_mv:,.0f} | 仓位: {total_mv/TOTAL_CAPITAL*100:.1f}%")
    print(f"  数据日期: {trade_date}")

    if alerts:
        print(f"\n  ⚠️ 今日操作提醒:")
        for a in alerts:
            print(f"    {a}")
    else:
        print(f"\n  ✅ 所有持仓正常，无告警")

    # Show stop-loss prices for easy reference
    print(f"\n  📋 止损参考价:")
    for code, pos in positions.items():
        entry = pos["entry_price"]
        stop = pos.get("stop_loss", entry * 0.95)
        name = pos.get("name", "")
        print(f"    {code} {name}: 止损¥{stop:.2f} | 入场¥{entry:.2f}")

    print(f"{'='*60}\n")


def midday_routine():
    """11:30 AM — Manual check reminder. No real-time data available."""
    print(f"\n{'='*60}")
    print(f"  ☀️ 午间提醒 — {datetime.now().strftime('%H:%M')}")
    print(f"  ⚠️ 以下为昨日收盘价，请打开券商APP核对实时价格")
    print(f"{'='*60}")

    positions = load_positions()
    if not positions:
        return

    print(f"\n  打开 QMT 或券商APP，逐只检查:")
    for code, pos in positions.items():
        entry = pos["entry_price"]
        stop = pos.get("stop_loss", entry * 0.95)
        name = pos.get("name", "")
        print(f"    {code} {name}: 止损 ¥{stop:.2f} | 入场 ¥{entry:.2f} → 现价低于止损就卖")

    print(f"\n  📱 不知道现价？打开券商APP → 持仓 → 看最新价")
    print(f"{'='*60}\n")


def afternoon_routine():
    """14:30 PM — Final reminder before market close."""
    print(f"\n{'='*60}")
    print(f"  🌤️ 尾盘提醒 — {datetime.now().strftime('%H:%M')}")
    print(f"  距收盘30分钟 — 打开券商APP核对现价")
    print(f"{'='*60}")

    positions = load_positions()

    print(f"\n  最后机会 — 止损线参考:")
    critical = []
    for code, pos in positions.items():
        entry = pos["entry_price"]
        stop = pos.get("stop_loss", entry * 0.95)
        name = pos.get("name", "")
        print(f"    {code} {name}: 止损 ¥{stop:.2f}")
        # Flag stocks that were near stop yesterday
        _, prices = get_latest_prices()
        price = prices.get(code, entry)
        if price <= stop:
            critical.append(f"  🚨 {code} {name} 昨日收盘已破止损 → 今天必须卖!")

    if critical:
        print(f"\n  ⚠️ 紧急:")
        for c in critical:
            print(c)
    else:
        print(f"\n  ✅ 基于昨日收盘价，暂无突破止损")

    print(f"\n  收盘后运行: py -3.12 scripts/daily_workflow.py close")
    print(f"{'='*60}\n")


def close_routine():
    """15:00 PM — After close, generate tomorrow's signals."""
    print(f"\n{'='*60}")
    print(f"  🌙 收盘后 — {datetime.now().strftime('%H:%M')}")
    print(f"{'='*60}")
    print(f"  运行: py -3.12 scripts/daily_signal.py --dry-run")
    print(f"  运行: py -3.12 scripts/position_tracker.py")
    print(f"  运行: py -3.12 scripts/backfill_baostock.py  (如需补数据)")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "morning"

    routines = {
        "morning": morning_routine,
        "midday": midday_routine,
        "afternoon": afternoon_routine,
        "close": close_routine,
    }

    if cmd in routines:
        routines[cmd]()
    else:
        print(f"用法: python {__file__} morning|midday|afternoon|close")
        print(f"  morning  — 9:00 盘前检查止损止盈")
        print(f"  midday   — 11:30 午间持仓快照")
        print(f"  afternoon — 14:30 尾盘最后提醒")
        print(f"  close    — 15:00 收盘后操作指引")
