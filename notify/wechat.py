"""
WeChat (企业微信) notification.

Uses 企业微信 robot webhook — similar to DingTalk.
Webhook docs: https://developer.work.weixin.qq.com/document/path/91770

Setup:
    1. Create a 企业微信 group
    2. Add a group robot
    3. Copy the webhook key
    4. Set WECHAT_WEBHOOK_KEY in environment or config
"""

import json
import logging
from datetime import date, datetime
from typing import Optional

import requests

logger = logging.getLogger(__name__)


class WeChatNotifier:
    """企业微信 robot notification client.

    Usage:
        notifier = WeChatNotifier(webhook_key="your-key-here")
        notifier.send_text("Trade executed: BUY 000001.SZ @ ¥10.50")
    """

    def __init__(self, webhook_key: Optional[str] = None):
        import os
        key = webhook_key or os.getenv("WECHAT_WEBHOOK_KEY", "")
        self._webhook_url = f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={key}" if key else ""
        self._enabled = bool(key)

    def send_text(self, content: str, mentioned_list: Optional[list[str]] = None):
        """Send plain text message."""
        if not self._enabled:
            logger.debug("WeChat not enabled")
            return

        payload = {
            "msgtype": "text",
            "text": {
                "content": content,
                "mentioned_list": mentioned_list or [],
            },
        }
        self._post(payload)

    def send_markdown(self, content: str):
        """Send markdown-formatted message."""
        if not self._enabled:
            logger.debug("WeChat not enabled")
            return

        payload = {
            "msgtype": "markdown",
            "markdown": {
                "content": content,
            },
        }
        self._post(payload)

    def send_signal_card(
        self,
        ts_code: str,
        signal_type: str,
        price: float,
        reason: str = "",
    ):
        """Send a trading signal as a WeChat markdown card."""
        emoji = "🟢" if signal_type == "BUY" else "🔴"
        text = (
            f"## {emoji} {signal_type}\n"
            f"> Stock: <font color=\"info\">{ts_code}</font>\n"
            f"> Price: <font color=\"warning\">¥{price:.2f}</font>\n"
            f"> Reason: {reason}\n"
            f"> Time: {datetime.now().strftime('%H:%M:%S')}"
        )
        self.send_markdown(text)

    def _post(self, payload: dict):
        """Post payload to webhook."""
        try:
            resp = requests.post(self._webhook_url, json=payload, timeout=10)
            result = resp.json()
            if result.get("errcode") != 0:
                logger.error("WeChat send failed: %s", result.get("errmsg"))
        except Exception:
            logger.warning("WeChat send failed — webhook may not be configured")
