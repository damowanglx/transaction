"""
Tests for risk management system (Phase 4).

Verifies:
- PositionManager: position size limits, liquidity checks, count limits
- CircuitBreaker: daily loss trigger, consecutive loss trigger, cooldown
- RiskEngine: full pipeline check
- Notification: message formatting (unit)
"""

import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, ".")

from config.risk_params import RiskConfig


# ============================================================
# Test: PositionManager
# ============================================================

class TestPositionManager:
    """Position size validation tests."""

    def test_buy_allowed(self):
        from risk.position_mgr import PositionManager

        pm = PositionManager()
        check = pm.check_buy(
            ts_code="000001.SZ",
            price=10.00,
            budget=20_000,
            total_value=200_000,
            current_positions={},
        )
        assert check.allowed
        assert check.max_shares == 2000  # 20000 / 10

    def test_buy_exceeds_single_position_limit(self):
        from risk.position_mgr import PositionManager

        config = RiskConfig(max_single_position_pct=0.20)  # 20% = 40k at 200k
        pm = PositionManager(config)

        # Budget 100k but position capped at 20% of 200k = 40k
        check = pm.check_buy(
            ts_code="000001.SZ",
            price=10.00,
            budget=100_000,
            total_value=200_000,
            current_positions={},
        )
        # Capped, not rejected — max_shares reduced to fit limit
        assert check.allowed
        assert check.max_shares == 4000  # 40k / 10 = 4000 shares (was 10000)

    def test_buy_exceeds_total_position_limit(self):
        from risk.position_mgr import PositionManager

        config = RiskConfig(max_total_position_pct=0.80)
        pm = PositionManager(config)

        # Already at 78% total, adding 45k would push past 80%
        # Current: 156k / 200k = 78%
        # Target: (156k + 45k) / (200k + 45k) = 201k/245k = 82% > 80%
        # Current total MV = 156k, cap at 80% of 200k = 160k
        # Allowable new MV = 160k - 156k = 4000, shares = 4000/10 = 400
        check = pm.check_buy(
            ts_code="NEW.SZ",
            price=10.00,
            budget=45_000,  # Would exceed, gets capped
            total_value=200_000,
            current_positions={
                "EXIST.SZ": {"volume": 12000, "market_value": 156_000, "avg_cost": 13.0},
            },
        )
        assert check.allowed  # Capped, not rejected
        assert check.max_shares == 400  # 4000/10 = 400 shares (was 4500)

    def test_buy_at_max_holdings(self):
        from risk.position_mgr import PositionManager

        config = RiskConfig(max_holdings_count=3)
        pm = PositionManager(config)

        positions = {f"T{i:02d}.SZ": {"volume": 100, "market_value": 5000, "avg_cost": 50.0} for i in range(3)}

        check = pm.check_buy(
            ts_code="NEW.SZ",
            price=10.00,
            budget=5000,
            total_value=200_000,
            current_positions=positions,
        )
        assert not check.allowed
        assert "holdings count" in check.reason.lower()

    def test_buy_insufficient_liquidity(self):
        from risk.position_mgr import PositionManager

        config = RiskConfig(min_daily_volume_yuan=500_000)
        pm = PositionManager(config)

        check = pm.check_buy(
            ts_code="000001.SZ",
            price=10.00,
            budget=20_000,
            total_value=200_000,
            current_positions={},
            daily_volume=100_000,  # Below 500k minimum
        )
        assert not check.allowed

    def test_buy_penny_stock_filter(self):
        from risk.position_mgr import PositionManager

        config = RiskConfig(min_stock_price=3.0)
        pm = PositionManager(config)

        check = pm.check_buy(
            ts_code="000001.SZ",
            price=2.50,
            budget=5000,
            total_value=200_000,
            current_positions={},
        )
        assert not check.allowed
        assert "Price" in check.reason

    def test_sell_position_exists(self):
        from risk.position_mgr import PositionManager

        pm = PositionManager()
        check = pm.check_sell(
            ts_code="000001.SZ",
            current_positions={
                "000001.SZ": {"volume": 1000, "market_value": 10000, "avg_cost": 10.0},
            },
        )
        assert check.allowed
        assert check.max_shares == 1000

    def test_sell_no_position(self):
        from risk.position_mgr import PositionManager

        pm = PositionManager()
        check = pm.check_sell("NOEXIST.SZ", {})
        assert not check.allowed

    def test_round_lot(self):
        from risk.position_mgr import PositionManager

        pm = PositionManager()
        check = pm.check_buy(
            ts_code="000001.SZ",
            price=10.00,
            budget=1050,  # 105 shares
            total_value=200_000,
            current_positions={},
        )
        assert check.max_shares == 100  # Rounded down to lot


# ============================================================
# Test: CircuitBreaker
# ============================================================

class TestCircuitBreaker:
    """Circuit breaker trigger logic tests."""

    def test_normal_state(self):
        from risk.circuit_breaker import BreakerState, CircuitBreaker

        cb = CircuitBreaker()
        status = cb.check(
            capital=200_000,
            pnl=+1000,
            pnl_pct=+0.005,
            history=[],
            today=date.today(),
        )
        assert status.state == BreakerState.NORMAL

    def test_daily_loss_trigger(self):
        from risk.circuit_breaker import BreakerState, CircuitBreaker

        config = RiskConfig(max_daily_loss_pct=0.02)  # 2% of 200k = 4000
        cb = CircuitBreaker(config)

        status = cb.check(
            capital=200_000,
            pnl=-5_000,  # Exceeds 4000 limit
            pnl_pct=-0.025,
            history=[],
            today=date.today(),
        )
        assert status.state == BreakerState.TRIPPED
        assert status.resume_date is not None
        assert (status.resume_date - date.today()).days == 7  # Default pause (7 calendar days ≈ 5 trading)

    def test_warning_approaching_limit(self):
        from risk.circuit_breaker import BreakerState, CircuitBreaker

        config = RiskConfig(max_daily_loss_pct=0.02)
        cb = CircuitBreaker(config)

        # Loss at 80% of limit: 4000 * 0.8 = 3200
        status = cb.check(
            capital=200_000,
            pnl=-3_500,  # >70% of limit → WARNING
            pnl_pct=-0.0175,
            history=[],
            today=date.today(),
        )
        assert status.state == BreakerState.WARNING

    def test_consecutive_loss_trigger(self):
        from risk.circuit_breaker import BreakerState, CircuitBreaker

        config = RiskConfig(max_consecutive_loss_days=3)
        cb = CircuitBreaker(config)

        # Simulate 2 loss days (check + record for each day)
        cb.check(capital=200_000, pnl=-500, pnl_pct=-0.0025, history=[], today=date.today())
        cb.record_day(-500)
        cb.check(capital=200_000, pnl=-500, pnl_pct=-0.0025, history=[], today=date.today())
        cb.record_day(-500)

        # 3rd loss day should trip
        status = cb.check(
            capital=200_000,
            pnl=-500,  # Small loss but 3rd consecutive
            pnl_pct=-0.0025,
            history=[],
            today=date.today(),
        )
        assert status.state == BreakerState.TRIPPED

    def test_consecutive_reset_on_gain(self):
        from risk.circuit_breaker import BreakerState, CircuitBreaker

        config = RiskConfig(max_consecutive_loss_days=3)
        cb = CircuitBreaker(config)

        cb.check(capital=200_000, pnl=-500, pnl_pct=-0.0025, history=[], today=date.today())
        cb.record_day(-500)
        cb.check(capital=200_000, pnl=-500, pnl_pct=-0.0025, history=[], today=date.today())
        cb.record_day(-500)

        # Gain day resets
        cb.record_day(+1000)
        status = cb.check(capital=200_000, pnl=+1000, pnl_pct=+0.005, history=[], today=date.today())
        assert status.state == BreakerState.NORMAL
        assert status.consecutive_loss_days == 0

    def test_cooldown_period(self):
        from risk.circuit_breaker import BreakerState, CircuitBreaker

        config = RiskConfig(max_daily_loss_pct=0.02, pause_duration_days=5)
        cb = CircuitBreaker(config)

        # Trip
        cb.check(capital=200_000, pnl=-5_000, pnl_pct=-0.025, history=[], today=date.today())
        assert cb.state == BreakerState.TRIPPED

        # 2 days later — still cooling
        status = cb.check(
            capital=200_000, pnl=0, pnl_pct=0, history=[],
            today=date.today() + timedelta(days=2),
        )
        assert status.state == BreakerState.COOLING

        # 6 days later — should reset
        status = cb.check(
            capital=200_000, pnl=0, pnl_pct=0, history=[],
            today=date.today() + timedelta(days=6),
        )
        assert status.state == BreakerState.NORMAL

    def test_manual_reset(self):
        from risk.circuit_breaker import BreakerState, CircuitBreaker

        cb = CircuitBreaker()
        cb.check(capital=200_000, pnl=-10_000, pnl_pct=-0.05, history=[], today=date.today())
        assert cb.state == BreakerState.TRIPPED

        cb.reset()
        assert cb.state == BreakerState.NORMAL


# ============================================================
# Test: RiskEngine
# ============================================================

class TestRiskEngine:
    """Full risk engine pipeline tests."""

    def test_pass_all_checks(self):
        from risk.risk_engine import RiskEngine

        engine = RiskEngine()
        engine.set_initial_capital(200_000)

        result = engine.check_order(
            direction="BUY",
            ts_code="000001.SZ",
            price=10.00,
            budget=20_000,
            total_value=200_000,
            current_positions={},
            daily_pnl=+500,
            daily_pnl_pct=+0.0025,
            today=date.today(),
        )
        assert result.allowed

    def test_blocked_by_breaker(self):
        from risk.risk_engine import RiskEngine

        config = RiskConfig(max_daily_loss_pct=0.02)
        engine = RiskEngine(config)
        engine.set_initial_capital(200_000)

        # Huge loss trips breaker
        result = engine.check_order(
            direction="BUY",
            ts_code="000001.SZ",
            price=10.00,
            budget=20_000,
            total_value=200_000,
            current_positions={},
            daily_pnl=-6_000,  # Exceeds 4000 limit
            daily_pnl_pct=-0.03,
            today=date.today(),
        )
        assert not result.allowed
        assert "Circuit breaker" in result.reason

    def test_stop_loss_detection(self):
        from risk.risk_engine import RiskEngine

        config = RiskConfig(stop_loss_pct=0.05)
        engine = RiskEngine(config)
        engine.set_initial_capital(200_000)

        result = engine.check_order(
            direction="BUY",
            ts_code="NEW.SZ",
            price=10.00,
            budget=10_000,
            total_value=200_000,
            current_positions={
                "BAD.SZ": {
                    "volume": 1000,
                    "market_value": 9_300,
                    "avg_cost": 10.0,  # Bought at 10
                    "current_price": 9.3,  # Now -7%
                },
            },
            daily_pnl=+100,
            daily_pnl_pct=+0.0005,
            today=date.today(),
        )
        assert result.allowed  # The buy itself is allowed
        assert "BAD.SZ" in result.stop_loss_hit  # But stop loss is flagged

    def test_record_day(self):
        from risk.risk_engine import RiskEngine

        engine = RiskEngine()
        engine.set_initial_capital(200_000)

        engine.record_day(date.today(), pnl=+2000, pnl_pct=+0.01)
        # After recording, no exception = success


# ============================================================
# Test: Notifications (unit — no webhook needed)
# ============================================================

class TestNotify:
    """Notification formatting tests (no actual webhook calls)."""

    def test_dingtalk_message_format(self):
        from notify.dingtalk import DingTalkNotifier

        notifier = DingTalkNotifier(webhook_url="https://example.com/test")
        # Should not raise — just validates message construction
        notifier.send_signal("000001.SZ", "BUY", 10.50, "Test reason", 0.85)

    def test_wechat_message_format(self):
        from notify.wechat import WeChatNotifier

        notifier = WeChatNotifier(webhook_key="test-key")
        # Should not raise
        notifier.send_text("Test message")
        notifier.send_markdown("**Test** markdown")

    def test_dingtalk_disabled(self):
        from notify.dingtalk import DingTalkNotifier

        notifier = DingTalkNotifier(webhook_url="")  # Empty = disabled
        # Should not raise
        notifier.send_signal("000001.SZ", "BUY", 10.50, "Test")
        notifier.send_risk_alert("TEST", "detail", "WARN")
        notifier.send_daily_summary(date.today(), 200_000, +1000, +0.005, 3, "NORMAL")

    def test_wechat_disabled(self):
        from notify.wechat import WeChatNotifier

        notifier = WeChatNotifier(webhook_key="")
        notifier.send_text("Should not raise")
        notifier.send_markdown("Test")
