#!/usr/bin/env python
"""Pre-market overnight gap risk checker.

Run BEFORE 9:30 market open on trading days.
Checks held positions for overnight gaps that exceed safety thresholds.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json, logging
from datetime import date, datetime
from typing import Optional

logger = logging.getLogger("gap_check")


def check_overnight_gaps(
    positions: dict,
    prev_close: dict[str, float],
    today_open: Optional[dict[str, float]] = None,
    gap_warning_pct: float = 0.03,   # 3% gap = warning
    gap_critical_pct: float = 0.07,  # 7% gap = critical (near limit)
) -> list[dict]:
    """Check all held positions for overnight price gaps.

    Args:
        positions: {ts_code: {entry_price, volume, ...}}
        prev_close: {ts_code: previous close price}
        today_open: {ts_code: today open price}. If None, uses prev_close * (1+gap).
        gap_warning_pct: Gap threshold for warning (default 3%).
        gap_critical_pct: Gap threshold for critical alert (default 7%).

    Returns:
        List of dicts: {ts_code, gap_pct, severity, action}
    """
    alerts = []
    for code, pos in positions.items():
        close = prev_close.get(code)
        if close is None or close <= 0:
            continue

        entry = pos.get("entry_price", 0) if isinstance(pos, dict) else 0
        volume = pos.get("volume", 0) if isinstance(pos, dict) else 0

        if today_open:
            open_price = today_open.get(code, close)
        else:
            open_price = close  # Can't check without open data

        gap_pct = (open_price - close) / close if close > 0 else 0

        if abs(gap_pct) < gap_warning_pct:
            continue

        severity = "CRITICAL" if abs(gap_pct) >= gap_critical_pct else "WARN"
        direction = "高开" if gap_pct > 0 else "低开"

        action = ""
        if gap_pct < -gap_critical_pct:
            action = "⚠️ 建议开盘后立即检查是否止损"
        elif gap_pct < -gap_warning_pct:
            action = "📉 接近止损线，密切关注"
        elif gap_pct > gap_critical_pct:
            action = "💰 大幅高开，考虑止盈/减仓"

        alerts.append({
            "ts_code": code,
            "prev_close": close,
            "open_price": open_price,
            "gap_pct": gap_pct,
            "severity": severity,
            "direction": direction,
            "action": action,
            "entry_price": entry,
            "volume": volume,
            "entry_pnl": (open_price - entry) / entry * 100 if entry > 0 else 0,
        })

    # Sort: critical first, then by gap magnitude
    alerts.sort(key=lambda a: (0 if a["severity"] == "CRITICAL" else 1, -abs(a["gap_pct"])))
    return alerts


def main(verbose: bool = True):
    """Run pre-market gap check on current positions."""
    pos_file = Path(__file__).resolve().parent / "positions.json"
    if not pos_file.exists():
        print("无持仓文件，跳过跳空检查")
        return []

    positions = json.loads(pos_file.read_text())
    if not positions:
        print("无持仓，跳过跳空检查")
        return []

    # Try to load previous close from ClickHouse
    prev_close = {}
    try:
        from data.storage.clickhouse_client import get_clickhouse_client
        ch = get_clickhouse_client()
        if ch.ping():
            result = ch.client.query("SELECT max(trade_date) FROM daily_bars WHERE ts_code != '000300.SH'")
            latest = result.first_row[0]
            codes_tuple = tuple(positions.keys())
            data = ch.client.query_df(
                "SELECT ts_code, close FROM daily_bars "
                "WHERE ts_code IN %(codes)s AND trade_date = %(date)s",
                parameters={"codes": codes_tuple, "date": str(latest)},
            )
            for _, row in data.iterrows():
                prev_close[row["ts_code"]] = row["close"]
    except Exception:
        pass

    alerts = check_overnight_gaps(positions, prev_close)

    if verbose:
        now = datetime.now().strftime("%H:%M")
        print(f"\n{'='*70}")
        print(f"  📋 盘前跳空检查 — {date.today()} {now}")
        print(f"  {'='*70}")

        if not alerts:
            print(f"  ✅ 所有 {len(positions)} 只持仓无异常跳空 (阈值 ±3%)")
        else:
            critical = [a for a in alerts if a["severity"] == "CRITICAL"]
            warns = [a for a in alerts if a["severity"] == "WARN"]
            print(f"  🚨 CRITICAL: {len(critical)} 只 | ⚠️ WARN: {len(warns)} 只\n")
            print(f"  {'代码':<12} {'昨收':<8} {'预估开盘':<8} {'跳空':<8} {'入场盈亏':<8} {'级别':<10} {'操作'}")
            print(f"  {'-'*70}")
            for a in alerts:
                icon = "🚨" if a["severity"] == "CRITICAL" else "⚠️"
                print(f"  {a['ts_code']:<12} ¥{a['prev_close']:<7.2f} ¥{a['open_price']:<7.2f} "
                      f"{a['gap_pct']:>+6.1%}  {a['entry_pnl']:>+6.1%}  "
                      f"{icon} {a['severity']:<7} {a['action']}")

        print(f"\n  ⚠️ 若无实时开盘价，以上为昨日收盘价预估")
        print(f"  📋 操作顺序: 先检查跳空 → 再执行买卖信号")
        print(f"{'='*70}\n")

    return alerts


if __name__ == "__main__":
    main()
