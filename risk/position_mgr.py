"""
Position manager — enforces position sizing rules.

Pure logic, no database dependency. Validates every order
against risk limits before execution.

Rules enforced:
- Single stock max position (default 20% of total value)
- Total portfolio max position (default 80%)
- Max number of holdings (default 8)
- Minimum volume/liquidity filter
- Lot size rounding (100 shares)
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

from config.risk_params import RiskConfig, get_risk_config

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PositionCheck:
    """Immutable result of a position validation check."""
    allowed: bool
    max_shares: int          # Max shares that can be bought (0 if sell)
    reason: str              # Reason for rejection
    current_weight: float    # Current position weight in portfolio
    target_weight: float     # Target position weight after trade


class PositionManager:
    """Validates position sizes against risk constraints.

    Usage:
        pm = PositionManager(risk_config)
        check = pm.check_buy("000001.SZ", price=10.50, budget=20000,
                              total_value=200000, current_positions={...})
        if check.allowed:
            place_order(check.max_shares)
    """

    def __init__(self, risk_config: Optional[RiskConfig] = None):
        self._config = risk_config or get_risk_config("default")

    def check_buy(
        self,
        ts_code: str,
        price: float,
        budget: float,
        total_value: float,
        current_positions: dict[str, dict],
        daily_volume: Optional[float] = None,
    ) -> PositionCheck:
        """Validate a prospective buy order.

        Args:
            ts_code: Stock code.
            price: Order price.
            budget: Cash allocated for this buy (before commission).
            total_value: Total portfolio value (cash + holdings).
            current_positions: {ts_code: {volume, market_value, ...}}.
            daily_volume: Stock's daily volume (yuan) for liquidity check.

        Returns:
            PositionCheck with allowed/max_shares/reason.
        """
        # 1. Max holdings count
        if len(current_positions) >= self._config.max_holdings_count:
            return PositionCheck(
                allowed=False, max_shares=0,
                reason=f"Max holdings count reached ({self._config.max_holdings_count})",
                current_weight=0, target_weight=0,
            )

        # 2. Single stock weight limit
        current_mv = current_positions.get(ts_code, {}).get("market_value", 0.0)
        current_weight = current_mv / total_value if total_value > 0 else 0.0
        target_weight = (current_mv + budget) / total_value

        if target_weight > self._config.max_single_position_pct:
            # Cap at max single position
            max_mv = total_value * self._config.max_single_position_pct
            allowed_budget = max(max_mv - current_mv, 0)
            max_shares = self._round_lot(int(allowed_budget / price))
            if max_shares <= 0:
                return PositionCheck(
                    allowed=False, max_shares=0,
                    reason=f"Single stock limit: {current_weight*100:.1f}% → {target_weight*100:.1f}% exceeds {self._config.max_single_position_pct*100:.0f}%",
                    current_weight=current_weight, target_weight=target_weight,
                )
            budget = max_shares * price

        # 3. Total portfolio position limit
        current_total_mv = sum(p.get("market_value", 0) for p in current_positions.values())
        future_total_mv = current_total_mv + budget
        # Budget comes from existing cash in total_value; total_value unchanged (-fees)
        future_total_weight = future_total_mv / total_value if total_value > 0 else 0

        if future_total_weight > self._config.max_total_position_pct:
            max_new_mv = total_value * self._config.max_total_position_pct - current_total_mv
            allowed_budget = max(max_new_mv, 0)
            max_shares = self._round_lot(int(allowed_budget / price))
            if max_shares <= 0:
                return PositionCheck(
                    allowed=False, max_shares=0,
                    reason=f"Total position limit: {future_total_weight*100:.1f}% exceeds {self._config.max_total_position_pct*100:.0f}%",
                    current_weight=current_weight, target_weight=future_total_weight,
                )
            budget = max_shares * price

        # 4. Liquidity filter
        if daily_volume is not None and daily_volume < self._config.min_daily_volume_yuan:
            return PositionCheck(
                allowed=False, max_shares=0,
                reason=f"Daily volume ¥{daily_volume:,.0f} below minimum ¥{self._config.min_daily_volume_yuan:,.0f}",
                current_weight=current_weight, target_weight=target_weight,
            )

        # 5. Position vs volume ratio (avoid moving the market)
        if daily_volume is not None and daily_volume > 0:
            vol_ratio = budget / daily_volume
            if vol_ratio > self._config.max_position_volume_ratio:
                max_budget = daily_volume * self._config.max_position_volume_ratio
                max_shares = self._round_lot(int(max_budget / price))
                return PositionCheck(
                    allowed=False, max_shares=0,
                    reason=f"Order ¥{budget:,.0f} exceeds {self._config.max_position_volume_ratio*100:.0f}% of daily volume ¥{daily_volume:,.0f}",
                    current_weight=current_weight, target_weight=target_weight,
                )

        if price <= 0:
            return PositionCheck(
                allowed=False, max_shares=0,
                reason="Price is zero or negative",
                current_weight=0, target_weight=0,
            )

        # 6. Price filter
        if price < self._config.min_stock_price:
            return PositionCheck(
                allowed=False, max_shares=0,
                reason=f"Price ¥{price:.2f} below minimum ¥{self._config.min_stock_price:.2f}",
                current_weight=current_weight, target_weight=target_weight,
            )
        if price > self._config.max_stock_price:
            return PositionCheck(
                allowed=False, max_shares=0,
                reason=f"Price ¥{price:.2f} exceeds maximum ¥{self._config.max_stock_price:.2f}",
                current_weight=current_weight, target_weight=target_weight,
            )

        # All checks passed
        max_shares = self._round_lot(int(budget / price))
        return PositionCheck(
            allowed=True, max_shares=max_shares, reason="OK",
            current_weight=current_weight, target_weight=(current_mv + max_shares * price) / total_value,
        )

    def check_sell(
        self,
        ts_code: str,
        current_positions: dict[str, dict],
    ) -> PositionCheck:
        """Validate a prospective sell order (usually just checks holdings exist)."""
        if ts_code not in current_positions:
            return PositionCheck(
                allowed=False, max_shares=0,
                reason=f"No position for {ts_code}",
                current_weight=0, target_weight=0,
            )

        pos = current_positions[ts_code]
        return PositionCheck(
            allowed=True,
            max_shares=pos.get("volume", 0),
            reason="OK",
            current_weight=pos.get("market_value", 0) / (sum(p.get("market_value", 0) for p in current_positions.values()) or 1),
            target_weight=0,
        )

    @staticmethod
    def _round_lot(shares: int) -> int:
        return (shares // 100) * 100
