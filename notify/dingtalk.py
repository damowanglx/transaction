"""
DingTalk (钉钉) robot notification.

Sends trading signals, risk alerts, and daily summaries
via DingTalk webhook. Free, real-time, supports markdown.

Setup:
    1. Create a DingTalk group
    2. Add a custom robot (webhook)
    3. Copy the webhook URL
    4. Set DINGTALK_WEBHOOK_URL in environment or config
"""

import json
import logging
import time
from datetime import date, datetime
from typing import Optional

import requests

logger = logging.getLogger(__name__)


class DingTalkNotifier:
    """DingTalk robot notification client.

    Usage:
        notifier = DingTalkNotifier(webhook_url)
        notifier.send_signal("000001.SZ", "BUY", 10.50, reason="Trend following")
        notifier.send_risk_alert("STOP_LOSS triggered on 000001.SZ")
    """

    def __init__(self, webhook_url: Optional[str] = None):
        import os
        self._webhook_url = webhook_url or os.getenv("DINGTALK_WEBHOOK_URL", "")
        self._enabled = bool(self._webhook_url)

    # ============================================================
    # Public API
    # ============================================================

    def send_signal(
        self,
        ts_code: str,
        signal_type: str,
        price: float,
        reason: str = "",
        confidence: float = 0.0,
    ):
        """Send a trading signal notification."""
        emoji = "🟢" if signal_type == "BUY" else "🔴"
        text = (
            f"## {emoji} {signal_type} Signal\n\n"
            f"**Stock:** {ts_code}\n"
            f"**Price:** ¥{price:.2f}\n"
            f"**Confidence:** {confidence:.0%}\n"
            f"**Reason:** {reason}\n"
            f"**Time:** {datetime.now().strftime('%H:%M:%S')}\n"
        )
        self._send_markdown(f"{signal_type} Signal — {ts_code}", text)

    def send_risk_alert(
        self,
        event_type: str,
        detail: str,
        severity: str = "WARN",
    ):
        """Send a risk event alert."""
        emoji = {"CRITICAL": "🚨", "WARN": "⚠️", "INFO": "ℹ️"}.get(severity, "📢")
        text = (
            f"## {emoji} {severity} — {event_type}\n\n"
            f"{detail}\n\n"
            f"**Time:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"**Action:** Check risk dashboard immediately\n"
        )
        self._send_markdown(f"Risk Alert: {event_type}", text)

    def send_daily_summary(
        self,
        trade_date: date,
        total_value: float,
        daily_pnl: float,
        daily_return: float,
        trades_today: int,
        breaker_status: str,
    ):
        """Send end-of-day trading summary."""
        pnl_emoji = "📈" if daily_pnl >= 0 else "📉"
        text = (
            f"## {pnl_emoji} Daily Summary — {trade_date}\n\n"
            f"**Portfolio:** ¥{total_value:,.0f}\n"
            f"**Daily P&L:** ¥{daily_pnl:+,.0f} ({daily_return:+.2%})\n"
            f"**Trades Today:** {trades_today}\n"
            f"**Breaker Status:** {breaker_status}\n"
        )
        self._send_markdown(f"Daily Summary {trade_date}", text)

    def send_batch_signals(
        self,
        buys: list,
        sells: list,
        prices: dict,
        stops: dict,
        names: dict,
        per_stock_budget: float,
    ):
        """Send all daily signals in a single notification (avoids rate limit)."""
        if not self._enabled:
            logger.debug("DingTalk not enabled — skipping batch signals")
            return

        lines = [f"## 📊 明日交易信号 ({len(buys)}买 {len(sells)}卖)\n"]
        if buys:
            lines.append(f"### 🟢 买入 (每只约 ¥{per_stock_budget:,.0f})")
            for s in buys:
                p = prices.get(s.ts_code, 0)
                name = names.get(s.ts_code, "")
                stop = stops.get(s.ts_code, p * 0.95)
                lines.append(
                    f"- {s.ts_code} {name} @ ¥{p:.2f} "
                    f"止损¥{stop:.2f} 置信度{s.confidence:.0%}"
                )
        if sells:
            lines.append(f"### 🔴 卖出")
            for s in sells:
                p = prices.get(s.ts_code, 0)
                lines.append(f"- {s.ts_code} @ ¥{p:.2f} — {s.reason[:40]}")
        lines.append(f"\n⚠️ 操作时间: 明天 9:30 开盘")

        self._send_markdown(
            f"Trading Signals — {date.today()}",
            "\n".join(lines),
        )

    def send_test(self):
        """Send a test message to verify webhook works."""
        self._send_markdown(
            "Quant System Test",
            "✅ Notification system is working.\n\nTrade signals will appear here.",
        )

    # ============================================================
    # Internal
    # ============================================================

    def _send_markdown(self, title: str, text: str):
        """Send a markdown-formatted message via webhook."""
        if not self._enabled:
            logger.debug("DingTalk not enabled — skipping: %s", title)
            return

        payload = {
            "msgtype": "markdown",
            "markdown": {
                "title": title,
                "text": text,
            },
        }

        try:
            resp = requests.post(
                self._webhook_url,
                json=payload,
                timeout=10,
            )
            result = resp.json()
            if result.get("errcode") != 0:
                logger.error("DingTalk send failed: %s", result.get("errmsg"))
            else:
                logger.info("DingTalk message sent: %s", title)
            time.sleep(0.1)  # Rate limit: 20 msg/min
        except Exception:
            logger.exception("DingTalk send error")
