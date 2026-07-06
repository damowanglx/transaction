#!/usr/bin/env python
"""Intraday real-time monitor — polls QMT tick data, checks risk thresholds.

Run during market hours (9:30-15:00): python scripts/intraday_monitor.py
Checks every 60 seconds: position P&L, price limits, circuit breaker.

Features:
- Position P&L alerts (±3% move triggers warning)
- Daily loss circuit breaker (3.5% of capital)
- Price limit detection (涨跌停)
- DingTalk/WeChat push notifications
- Runs standalone, no DB dependency
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json, logging, time
from datetime import date, datetime

from config.settings import setup_logging
setup_logging()
logger = logging.getLogger("intraday")

try:
    from xtquant import xtdata
    QMT_AVAILABLE = True
except ImportError:
    QMT_AVAILABLE = False
    logger.error("xtquant not installed — intraday monitor requires QMT")

from risk.circuit_breaker import CircuitBreaker, BreakerState
from config.risk_params import get_risk_config

# ---- Config ----
POLL_SECONDS = 60               # Check interval
GAP_WARN_PCT = 0.03             # ±3% = warning
GAP_CRITICAL_PCT = 0.05         # ±5% = critical
TOTAL_CAPITAL = 200_000
DAILY_LOSS_LIMIT = TOTAL_CAPITAL * 0.035  # ¥7,000


def get_held_stocks(positions_file: str = "") -> dict:
    """Load held positions from file."""
    if not positions_file:
        positions_file = str(Path(__file__).resolve().parent / "positions.json")
    try:
        return json.loads(Path(positions_file).read_text())
    except Exception:
        return {}


def get_realtime_prices(stocks: list[str]) -> dict[str, dict]:
    """Get real-time quotes from QMT xtdata."""
    if not QMT_AVAILABLE or not stocks:
        return {}
    try:
        ticks = xtdata.get_full_tick(stocks)
        result = {}
        for code, tick in (ticks or {}).items():
            result[code] = {
                "last": getattr(tick, "lastPrice", 0),
                "open": getattr(tick, "open", 0),
                "high": getattr(tick, "high", 0),
                "low": getattr(tick, "low", 0),
                "volume": getattr(tick, "volume", 0),
                "amount": getattr(tick, "amount", 0),
            }
        return result
    except Exception as e:
        logger.warning("Failed to get real-time quotes: %s", e)
        return {}


def check_alerts(positions: dict, prices: dict, breaker: CircuitBreaker) -> list[str]:
    """Check all positions against risk thresholds. Returns alert messages."""
    alerts = []
    daily_pnl = 0.0

    for code, pos in positions.items():
        tick = prices.get(code, {})
        last = tick.get("last", 0)
        entry = pos.get("entry_price", 0) if isinstance(pos, dict) else 0
        volume = pos.get("volume", 0) if isinstance(pos, dict) else 0

        if last <= 0 or entry <= 0:
            continue

        pnl_pct = (last - entry) / entry
        pnl_amount = (last - entry) * volume
        daily_pnl += pnl_amount

        # Price limit detection
        tick_open = tick.get("open", 0)
        tick_high = tick.get("high", 0)
        tick_low = tick.get("low", 0)
        if tick_high > 0 and tick_low > 0:
            limit_pct = 0.20 if code.startswith(("688", "300")) else 0.10
            prev_close = entry  # Approximate
            if (tick_high - prev_close) / prev_close >= limit_pct * 0.99:
                alerts.append(f"🚨 {code} 涨停! ¥{last:.2f} (limit +{limit_pct*100:.0f}%)")
            if (prev_close - tick_low) / prev_close >= limit_pct * 0.99:
                alerts.append(f"🚨 {code} 跌停! ¥{last:.2f} (limit -{limit_pct*100:.0f}%)")

        # Gap alerts
        if abs(pnl_pct) >= GAP_CRITICAL_PCT:
            direction = "↑" if pnl_pct > 0 else "↓"
            alerts.append(f"🔴 {code} {direction}{abs(pnl_pct)*100:.1f}% ¥{pnl_amount:+,.0f}")
        elif abs(pnl_pct) >= GAP_WARN_PCT:
            direction = "↑" if pnl_pct > 0 else "↓"
            alerts.append(f"⚠️ {code} {direction}{abs(pnl_pct)*100:.1f}% ¥{pnl_amount:+,.0f}")

    # Circuit breaker check
    daily_pnl_pct = daily_pnl / TOTAL_CAPITAL
    status = breaker.check(
        capital=TOTAL_CAPITAL, pnl=daily_pnl, pnl_pct=daily_pnl_pct,
        history=[], today=date.today(),
    )
    if status.state in (BreakerState.TRIPPED, BreakerState.COOLING):
        alerts.insert(0, f"🚨 熔断! 日亏损 ¥{abs(daily_pnl):,.0f} ({daily_pnl_pct*100:.1f}%) 超过 3.5% 限额")
        breaker.record_day(daily_pnl)
    elif status.state == BreakerState.WARNING:
        alerts.append(f"⚠️ 接近熔断线: 日亏损 {daily_pnl_pct*100:.1f}% / 3.5%")

    return alerts


def send_alerts(alerts: list[str]):
    """Push alerts via DingTalk and WeChat."""
    if not alerts:
        return
    now = datetime.now().strftime("%H:%M")
    header = f"## 📡 盘中监控 {now}\n"
    body = header + "\n".join(f"- {a}" for a in alerts)

    # DingTalk
    try:
        from notify.dingtalk import DingTalkNotifier
        dt = DingTalkNotifier()
        dt._send_markdown(f"Intraday Alert {now}", body)
    except Exception:
        pass

    # WeChat
    try:
        from notify.wechat import WeChatNotifier
        wx = WeChatNotifier()
        wx.send_markdown(body)
    except Exception:
        pass


def main():
    if not QMT_AVAILABLE:
        print("QMT not available — cannot run intraday monitor")
        return

    positions = get_held_stocks()
    if not positions:
        print("无持仓，监控无需运行")
        return

    codes = list(positions.keys())
    print(f"盘中监控已启动 — 持仓 {len(codes)} 只, 每 {POLL_SECONDS}s 检查")
    print(f"熔断线: ¥{DAILY_LOSS_LIMIT:,.0f} (3.5%) | 告警线: ±{GAP_WARN_PCT*100:.0f}%")
    print("=" * 60)

    breaker = CircuitBreaker(get_risk_config("default"))
    last_alert_time = ""

    try:
        while True:
            now = datetime.now()

            # Only run during market hours
            if now.hour < 9 or (now.hour == 9 and now.minute < 25):
                time.sleep(POLL_SECONDS)
                continue
            if now.hour >= 15 and now.minute > 5:
                print(f"\n{now.strftime('%H:%M')} — 已收盘，监控退出")
                break
            # Lunch break
            if now.hour == 11 and now.minute >= 30:
                if now.hour < 13:
                    time.sleep(POLL_SECONDS)
                    continue

            prices = get_realtime_prices(codes)
            if not prices:
                time.sleep(POLL_SECONDS)
                continue

            alerts = check_alerts(positions, prices, breaker)

            # Print status line
            pnl_total = sum(
                (prices.get(c, {}).get("last", 0) - (pos.get("entry_price", 0) if isinstance(pos, dict) else 0))
                * (pos.get("volume", 0) if isinstance(pos, dict) else 0)
                for c, pos in positions.items()
            )
            status = "🟢" if abs(pnl_total) < DAILY_LOSS_LIMIT * 0.5 else "🟡" if pnl_total < 0 else "🟢"
            ts = now.strftime("%H:%M:%S")
            print(f"\r{ts} {status} 日盈亏 ¥{pnl_total:+,.0f} | 持仓{len(positions)}只", end="", flush=True)

            if alerts:
                alert_key = "|".join(sorted(alerts))
                if alert_key != last_alert_time:  # Don't spam same alerts
                    print()  # New line
                    for a in alerts:
                        print(f"  {a}")
                    send_alerts(alerts)
                    last_alert_time = alert_key

            time.sleep(POLL_SECONDS)

    except KeyboardInterrupt:
        print("\n监控已停止")


if __name__ == "__main__":
    main()
