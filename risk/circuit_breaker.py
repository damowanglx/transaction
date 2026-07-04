"""
Circuit breaker — stops trading when risk thresholds are breached.

Triggers:
- Daily loss exceeds max_daily_loss_pct (default 2% of capital)
- Consecutive loss days exceed max_consecutive_loss_days (default 3)
- Single position stop loss (5%)

Once tripped, trading is paused for pause_duration_days (default 7).
Pure logic, no database dependency during checks.
"""

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum
from typing import Optional

from config.risk_params import RiskConfig, get_risk_config

logger = logging.getLogger(__name__)


class BreakerState(str, Enum):
    NORMAL = "NORMAL"           # Trading allowed
    WARNING = "WARNING"         # Approaching limit
    TRIPPED = "TRIPPED"         # Trading suspended
    COOLING = "COOLING"         # In cooldown period


@dataclass(frozen=True)
class BreakerStatus:
    """Immutable circuit breaker status."""
    state: BreakerState
    reason: str
    tripped_date: Optional[date]
    resume_date: Optional[date]
    consecutive_loss_days: int
    today_loss: float
    today_loss_pct: float


@dataclass(frozen=True)
class DayRecord:
    """Immutable trading day result for breaker tracking."""
    date: date
    pnl: float
    pnl_pct: float


class CircuitBreaker:
    """Tracks daily P&L and trips when limits are exceeded.

    Usage:
        cb = CircuitBreaker(risk_config)

        # Phase 1: Evaluate (pure query, no side effects)
        status = cb.check(capital=200000, pnl=-5000, pnl_pct=-0.025,
                          today=date.today())
        if status.state in (BreakerState.TRIPPED, BreakerState.COOLING):
            halt_trading()

        # Phase 2: Record (applies mutations at end of day)
        cb.record_day(pnl=-5000)  # Updates consecutive loss counter
    """

    def __init__(self, risk_config: Optional[RiskConfig] = None):
        self._config = risk_config or get_risk_config("default")
        self._state = BreakerState.NORMAL
        self._tripped_date: Optional[date] = None
        self._consecutive_losses = 0

    @property
    def state(self) -> BreakerState:
        return self._state

    def check(
        self,
        capital: float,
        pnl: float,
        pnl_pct: float,
        history: list[DayRecord],
        today: date,
    ) -> BreakerStatus:
        """Evaluate breaker status and apply state transitions.

        State transitions (NORMAL→TRIPPED, COOLING→NORMAL) are applied here.
        Consecutive loss counter is NOT updated here — use record_day().

        Args:
            capital: Current total portfolio value.
            pnl: Today's absolute profit/loss in yuan.
            pnl_pct: Today's return as decimal (-0.02 = -2%).
            history: List of recent DayRecord objects (most recent last).
            today: Current date.

        Returns:
            BreakerStatus with current state and reason.
        """
        return self._evaluate_and_apply(capital, pnl, pnl_pct, today)

    def record_day(self, pnl: float):
        """Update consecutive loss counter after trading day ends (MUTATES).

        Must be called exactly ONCE per trading day, after all check() calls.
        """
        if pnl < 0:
            self._consecutive_losses += 1
        else:
            self._consecutive_losses = 0

    def _evaluate_and_apply(
        self,
        capital: float,
        pnl: float,
        pnl_pct: float,
        today: date,
    ) -> BreakerStatus:
        """Evaluate breaker status and APPLY state transitions.

        State mutation is centralized here for clarity:
        - TRIPPED/COOLING → NORMAL when cooldown expires
        - NORMAL/WARNING → TRIPPED when limit breached
        - Consecutive loss counter is NOT mutated (record_day handles it).
        """

        # --- Cooldown check: if tripped, assess remaining cooldown ---
        if self._state in (BreakerState.TRIPPED, BreakerState.COOLING):
            if self._tripped_date:
                resume_date = self._tripped_date + timedelta(
                    days=self._config.pause_duration_days
                )
                if today < resume_date:
                    return BreakerStatus(
                        state=BreakerState.COOLING,
                        reason=f"Cooldown until {resume_date}",
                        tripped_date=self._tripped_date,
                        resume_date=resume_date,
                        consecutive_loss_days=self._consecutive_losses,
                        today_loss=pnl,
                        today_loss_pct=pnl_pct,
                    )
                else:
                    # Cooldown expired — reset
                    self._state = BreakerState.NORMAL
                    self._tripped_date = None
                    self._consecutive_losses = 0
                    logger.info("Circuit breaker reset — cooldown complete")

        # --- Daily loss limit check ---
        daily_loss_limit = capital * self._config.max_daily_loss_pct
        if pnl < 0 and abs(pnl) > daily_loss_limit:
            return self._trip(
                today,
                f"Daily loss ¥{abs(pnl):,.0f} exceeds limit "
                f"¥{daily_loss_limit:,.0f} ({self._config.max_daily_loss_pct*100:.1f}%)",
            )

        # --- Warning: approaching limit ---
        if pnl < 0 and abs(pnl) > daily_loss_limit * 0.7:
            return BreakerStatus(
                state=BreakerState.WARNING,
                reason=f"Approaching daily loss limit ({(abs(pnl)/daily_loss_limit)*100:.0f}% of limit)",
                tripped_date=None,
                resume_date=None,
                consecutive_loss_days=self._consecutive_losses,
                today_loss=pnl,
                today_loss_pct=pnl_pct,
            )

        # --- Consecutive loss days check ---
        future_consecutive = self._consecutive_losses + 1 if pnl < 0 else 0
        if future_consecutive >= self._config.max_consecutive_loss_days:
            return self._trip(
                today,
                f"Consecutive loss days ({future_consecutive}) reached limit "
                f"({self._config.max_consecutive_loss_days})",
            )

        return BreakerStatus(
            state=BreakerState.NORMAL,
            reason="All checks passed",
            tripped_date=None,
            resume_date=None,
            consecutive_loss_days=future_consecutive,
            today_loss=pnl,
            today_loss_pct=pnl_pct,
        )

    def _trip(self, today: date, reason: str) -> BreakerStatus:
        """Apply trip transition and return TRIPPED status."""
        self._state = BreakerState.TRIPPED
        self._tripped_date = today
        resume_date = today + timedelta(days=self._config.pause_duration_days)
        logger.warning("Circuit breaker TRIPPED: %s", reason)
        return BreakerStatus(
            state=BreakerState.TRIPPED,
            reason=reason,
            tripped_date=today,
            resume_date=resume_date,
            consecutive_loss_days=self._consecutive_losses,
            today_loss=0.0,
            today_loss_pct=0.0,
        )

    def reset(self):
        """Force reset the breaker (use with caution)."""
        self._state = BreakerState.NORMAL
        self._tripped_date = None
        self._consecutive_losses = 0
        logger.warning("Circuit breaker manually reset")
