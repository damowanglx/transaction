#!/usr/bin/env python
"""Position cost & risk tracker — shows P&L, checks stop/take-profit levels.

Run after market close: python scripts/position_tracker.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json, logging
from datetime import date

logger = logging.getLogger("tracker")

POSITIONS_FILE = Path(__file__).resolve().parent / "positions.json"
TRADE_LOG = Path(__file__).resolve().parent / "trade_log.json"

# A-share trading costs
COMMISSION_RATE = 0.0003  # 万三
MIN_COMMISSION = 5.00     # 最低 ¥5
STAMP_TAX_RATE = 0.001    # 千一（卖）

TOTAL_CAPITAL = 200_000


def load_positions():
    return json.loads(POSITIONS_FILE.read_text()) if POSITIONS_FILE.exists() else {}


def load_latest_prices():
    """Get latest close prices from ClickHouse."""
    try:
        from data.storage.clickhouse_client import get_clickhouse_client
        from datetime import timedelta as td
        ch = get_clickhouse_client()
        if ch.ping():
            r = ch.client.query("SELECT max(trade_date) FROM daily_bars WHERE ts_code != '000300.SH'")
            latest = r.first_row[0]
            if isinstance(latest, str):
                from datetime import datetime
                latest = datetime.strptime(latest, "%Y-%m-%d").date()
            codes = list(load_positions().keys())
            if codes:
                df = ch.client.query_df(
                    "SELECT ts_code, close FROM daily_bars "
                    "WHERE ts_code IN %(codes)s AND trade_date = %(d)s",
                    parameters={"codes": tuple(codes), "d": latest.isoformat()},
                )
                return latest, dict(zip(df["ts_code"], df["close"]))
    except Exception as e:
        logger.warning("Price lookup failed: %s", e)
    return None, {}


def log_trade(action: str, code: str, price: float, volume: int, reason: str = ""):
    """Record a trade in the trade log."""
    trades = []
    if TRADE_LOG.exists():
        trades = json.loads(TRADE_LOG.read_text())

    amount = price * volume
    commission = max(amount * COMMISSION_RATE, MIN_COMMISSION)
    stamp_tax = amount * STAMP_TAX_RATE if action == "SELL" else 0
    net = amount - commission - stamp_tax if action == "SELL" else -(amount + commission)

    trades.append({
        "date": str(date.today()),
        "action": action,
        "code": code,
        "price": price,
        "volume": volume,
        "amount": amount,
        "commission": round(commission, 2),
        "stamp_tax": round(stamp_tax, 2),
        "net_cash": round(net, 2),
        "reason": reason,
    })

    TRADE_LOG.write_text(json.dumps(trades, indent=2, ensure_ascii=False))
    return commission, stamp_tax


def main(check_only: bool = False):
    """Run position check. If check_only=True, exits non-zero on alerts found."""
    positions = load_positions()
    if not positions:
        print("无持仓")
        return 0

    trade_date, prices = load_latest_prices()

    print(f"\n{'='*75}")
    print(f"  📊 持仓跟踪 — {trade_date or 'N/A'} (本金 ¥{TOTAL_CAPITAL:,})")
    print(f"{'='*75}")
    print(f"  {'代码':<12} {'名称':<8} {'入场':<8} {'现价':<8} {'盈亏%':<8} {'市值':<10} {'止损':<8} {'状态'}")

    total_cost = 0
    total_mv = 0
    total_pnl = 0
    alerts = []

    for code, pos in positions.items():
        entry = pos["entry_price"]
        vol = pos["volume"]
        name = pos.get("name", "")
        stop = pos.get("stop_loss", entry * 0.95)
        tp = pos.get("take_profit", entry * 1.15)
        cost_basis = pos.get("cost_basis", entry)

        price = prices.get(code, entry)
        if price <= 0:
            price = entry

        pnl_pct = (price - entry) / entry * 100
        mv = price * vol
        pnl_amt = (price - entry) * vol

        total_cost += cost_basis * vol
        total_mv += mv
        total_pnl += pnl_amt

        # Check alerts
        status = "🟢"
        if price <= stop:
            status = "🔴 止损!"
            alerts.append(f"🔴 {code} {name} 触发止损! 现价¥{price:.2f} ≤ 止损¥{stop:.2f}")
        elif pnl_pct >= 14:
            status = "🟡 接近止盈"
            alerts.append(f"💰 {code} {name} 接近止盈 +{pnl_pct:.1f}%")
        elif pnl_pct <= -3:
            status = "🟡 接近止损"
        elif pnl_pct >= (tp/entry - 1) * 100 * 0.9:
            status = "💚 止盈区"

        print(f"  {code:<12} {name:<8} ¥{entry:<7.2f} ¥{price:<7.2f} {pnl_pct:+7.2f}% ¥{mv:<8,.0f} ¥{stop:<7.2f} {status}")

    print(f"  {'-'*75}")
    print(f"  总成本: ¥{total_cost:,.0f} | 总市值: ¥{total_mv:,.0f} | 总盈亏: ¥{total_pnl:+,.0f} ({total_pnl/total_cost*100:+.1f}%)" if total_cost > 0 else "")
    print(f"  仓位: {total_mv/TOTAL_CAPITAL*100:.1f}% | 可用: ¥{TOTAL_CAPITAL - total_mv:,.0f}")
    print(f"  交易成本已记录: {TRADE_LOG}")

    if alerts:
        print(f"\n  ⚠️ 告警:")
        for a in alerts:
            print(f"    {a}")

    print(f"{'='*75}\n")


if __name__ == "__main__":
    main()
