"""
Risk engine — central coordination for all risk checks.

Combines PositionManager, CircuitBreaker, and stop-loss
into a single pass/fail pipeline for every order.

Usage:
    engine = RiskEngine(risk_config)
    result = engine.check_order("BUY", ts_code="000001.SZ", price=10.50,
                                 budget=20000, total_value=200000,
                                 current_positions={...}, daily_pnl=-1000,
                                 today=date.today())
    if result.allowed:
        execute(order)
"""

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from config.risk_params import RiskConfig, get_risk_config
from risk.circuit_breaker import BreakerState, BreakerStatus, CircuitBreaker, DayRecord
from risk.position_mgr import PositionCheck, PositionManager

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RiskResult:
    """Immutable result of a comprehensive risk check."""
    allowed: bool
    reason: str
    breaker_status: BreakerStatus
    position_check: Optional[PositionCheck]
    stop_loss_hit: list[str]  # List of ts_codes that hit stop loss


class RiskEngine:
    """Central risk engine — validates every order against all risk rules.

    Checks in order (fast-fail):
    1. Circuit breaker — is trading allowed?
    2. Position limits — will this order violate size rules?
    3. Stop loss — should any position be liquidated?
    """

    def __init__(self, risk_config: Optional[RiskConfig] = None):
        self._config = risk_config or get_risk_config("default")
        self._position_mgr = PositionManager(self._config)
        self._circuit_breaker = CircuitBreaker(self._config)
        self._loss_history: list[DayRecord] = []
        self._initial_capital = 0.0

    @property
    def breaker_state(self) -> BreakerState:
        return self._circuit_breaker.state

    def set_initial_capital(self, capital: float):
        """Set initial capital for loss percentage calculations."""
        self._initial_capital = capital

    def check_order(
        self,
        direction: str,
        ts_code: str,
        price: float,
        budget: float,
        total_value: float,
        current_positions: dict[str, dict],
        daily_pnl: float,
        daily_pnl_pct: float,
        today: date,
        daily_volume: Optional[float] = None,
    ) -> RiskResult:
        """Comprehensive risk check for a single order.

        Args:
            direction: 'BUY' or 'SELL'
            ts_code: Stock code
            price: Order limit price
            budget: Cash committed to this order
            total_value: Total portfolio value
            current_positions: Current holdings {code: {volume, market_value, avg_cost}}
            daily_pnl: Day's P&L so far (in yuan)
            daily_pnl_pct: Day's return so far
            today: Trading date
            daily_volume: Stock's daily volume for liquidity check

        Returns:
            RiskResult — allowed + detailed reason.
        """
        # 1. Circuit breaker check
        breaker_status = self._circuit_breaker.check(
            capital=total_value,
            pnl=daily_pnl,
            pnl_pct=daily_pnl_pct,
            history=self._loss_history,
            today=today,
        )

        if breaker_status.state in (BreakerState.TRIPPED, BreakerState.COOLING):
            return RiskResult(
                allowed=False,
                reason=f"Circuit breaker: {breaker_status.reason}",
                breaker_status=breaker_status,
                position_check=None,
                stop_loss_hit=[],
            )

        # 2. Stop loss check (sell-side only for held positions)
        stop_loss_hit: list[str] = []
        for code, pos in current_positions.items():
            entry_cost = pos.get("avg_cost", 0.0)
            current_price = pos.get("current_price", entry_cost)
            if entry_cost > 0:
                pnl_pct = (current_price - entry_cost) / entry_cost
                if pnl_pct <= -self._config.stop_loss_pct:
                    stop_loss_hit.append(code)

        # 3. Position check
        if direction.upper() == "BUY":
            pos_check = self._position_mgr.check_buy(
                ts_code=ts_code,
                price=price,
                budget=budget,
                total_value=total_value,
                current_positions=current_positions,
                daily_volume=daily_volume,
            )
        else:
            pos_check = self._position_mgr.check_sell(
                ts_code=ts_code,
                current_positions=current_positions,
            )

        if not pos_check.allowed:
            return RiskResult(
                allowed=False,
                reason=f"Position check: {pos_check.reason}",
                breaker_status=breaker_status,
                position_check=pos_check,
                stop_loss_hit=stop_loss_hit,
            )

        return RiskResult(
            allowed=True,
            reason="All risk checks passed",
            breaker_status=breaker_status,
            position_check=pos_check,
            stop_loss_hit=stop_loss_hit,
        )

    def record_day(
        self,
        trade_date: date,
        pnl: float,
        pnl_pct: float,
    ):
        """Record a completed trading day for breaker tracking."""
        self._loss_history.append(DayRecord(
            date=trade_date,
            pnl=pnl,
            pnl_pct=pnl_pct,
        ))
        self._circuit_breaker.record_day(pnl)
        # Keep only last 60 days
        if len(self._loss_history) > 60:
            self._loss_history = self._loss_history[-60:]

    def reset(self):
        """Reset all risk state (use with caution)."""
        self._circuit_breaker.reset()
        self._loss_history.clear()
        logger.warning("Risk engine fully reset")
