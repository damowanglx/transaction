"""
A-Share broker simulator for backtesting.

Models A-share-specific trading rules:
- T+1 settlement: shares bought on day T can only be sold on day T+1
- Price limits: ±10% main board (60xxxx, 00xxxx), ±20% STAR/ChiNext (688xxx, 300xxx)
- Lot size: minimum 100 shares, must be multiples of 100
- Commission: brokerage fee (万三, min ¥5)
- Stamp tax: 千一 on SELL only
- Transfer fee: 万0.2 on SH trades only (simplified: included in commission)

All operations return NEW state — immutable pattern.
"""

from dataclasses import dataclass, field
from datetime import date
import random as _random
from typing import Optional


# ============================================================
# Data types
# ============================================================

@dataclass(frozen=True)
class Order:
    """Immutable order record."""
    order_id: str
    ts_code: str
    direction: str  # BUY | SELL
    price: float
    volume: int     # Shares
    trade_date: date


@dataclass(frozen=True)
class Trade:
    """Immutable filled trade record."""
    order_id: str
    ts_code: str
    direction: str
    price: float
    volume: int
    amount: float
    commission: float
    stamp_tax: float
    trade_date: date


@dataclass(frozen=True)
class Position:
    """Immutable single-stock position snapshot."""
    ts_code: str
    volume: int           # Total shares held
    available_volume: int  # Shares available to sell (T+1: bought shares locked for 1 day)
    avg_cost: float
    current_price: float

    @property
    def market_value(self) -> float:
        return self.volume * self.current_price

    @property
    def unrealized_pnl(self) -> float:
        return self.volume * (self.current_price - self.avg_cost)

    @property
    def pnl_pct(self) -> float:
        if self.avg_cost == 0:
            return 0.0
        return (self.current_price - self.avg_cost) / self.avg_cost


@dataclass(frozen=True)
class AccountState:
    """Immutable account state snapshot."""
    cash: float
    positions: dict[str, Position] = field(default_factory=dict)  # ts_code → Position
    pending_settlement: dict[str, tuple[int, float]] = field(default_factory=dict)  # ts_code → (shares, cost) locked T+1
    trade_history: list[Trade] = field(default_factory=list)

    @property
    def total_market_value(self) -> float:
        return sum(p.market_value for p in self.positions.values())

    @property
    def total_value(self) -> float:
        return self.cash + self.total_market_value


# ============================================================
# Broker Simulator
# ============================================================

class BrokerSim:
    """A-Share broker simulator for backtesting.

    Usage:
        broker = BrokerSim(initial_cash=200_000, commission_rate=0.0003)
        # Each trading day:
        broker = broker.settle_t_plus_1(current_date)
        broker = broker.update_market_prices(price_dict)
        result = broker.place_order("BUY", "000001.SZ", 10.50, 1000, current_date)
        broker = result.remaining_state
        print(result.trades)
    """

    def __init__(
        self,
        initial_cash: float = 200_000.0,
        commission_rate: float = 0.0003,  # 万三
        min_commission: float = 5.0,
        stamp_tax_rate: float = 0.001,     # 千一 (sell only)
        slippage_rate: float = 0.001,       # 0.1% slippage (liquid stocks, not penny)
    ):
        self._state = AccountState(cash=initial_cash)
        self._commission_rate = commission_rate
        self._min_commission = min_commission
        self._stamp_tax_rate = stamp_tax_rate
        self._slippage_rate = slippage_rate
        self._next_order_id = 1
        self._rng = _random.Random(42)  # Seeded for reproducibility

    # ============================================================
    # Properties
    # ============================================================

    @property
    def state(self) -> AccountState:
        return self._state

    @property
    def cash(self) -> float:
        return self._state.cash

    @property
    def positions(self) -> dict[str, Position]:
        return self._state.positions

    @property
    def total_value(self) -> float:
        return self._state.total_value

    # ============================================================
    # Settlement (call once per day, before trading)
    # ============================================================

    def settle_t_plus_1(self, current_date: date) -> "BrokerSim":
        """Process T+1 settlement: release locked shares to available."""
        new_positions = {}
        for code, pos in self._state.positions.items():
            locked_shares, _ = self._state.pending_settlement.get(code, (0, 0.0))
            new_positions[code] = Position(
                ts_code=code,
                volume=pos.volume,
                available_volume=pos.volume,  # All shares now available
                avg_cost=pos.avg_cost,
                current_price=pos.current_price,
            )

        new_state = AccountState(
            cash=self._state.cash,
            positions=new_positions,
            pending_settlement={},  # Clear settlement queue
            trade_history=self._state.trade_history,
        )
        self._state = new_state
        return self

    # ============================================================
    # Market price update
    # ============================================================

    def update_market_prices(self, prices: dict[str, float]) -> "BrokerSim":
        """Update current prices for all positions."""
        new_positions = {}
        for code, pos in self._state.positions.items():
            price = prices.get(code, pos.current_price)
            new_positions[code] = Position(
                ts_code=code,
                volume=pos.volume,
                available_volume=pos.available_volume,
                avg_cost=pos.avg_cost,
                current_price=price,
            )
        new_state = AccountState(
            cash=self._state.cash,
            positions=new_positions,
            pending_settlement=self._state.pending_settlement,
            trade_history=self._state.trade_history,
        )
        self._state = new_state
        return self

    # ============================================================
    # Order placement
    # ============================================================

    @dataclass(frozen=True)
    class OrderResult:
        """Result of a placed order — contains trades and new state."""
        trades: list[Trade]
        rejected: bool
        reject_reason: str
        remaining_state: AccountState

    def place_order(
        self,
        direction: str,
        ts_code: str,
        price: float,
        volume: int,
        trade_date: date,
        pre_close: float = 0.0,
    ) -> OrderResult:
        """Place an order. Returns new state — caller MUST use remaining_state.

        This method mutates nothing visible. It constructs and returns
        a new AccountState via OrderResult.remaining_state. The caller
        must replace the broker's state with the returned state.

        Args:
            direction: 'BUY' or 'SELL'
            ts_code: Stock code with exchange suffix (e.g. '600000.SH')
            price: Order limit price
            volume: Number of shares (will be rounded to lot size)
            trade_date: Trade date
            pre_close: Previous close price for new-buy limit check (0 = skip)

        Returns:
            OrderResult with filled trades and new AccountState.
        """
        # ---- Validate ----
        volume = self._round_lot(volume)
        if volume <= 0:
            return self.OrderResult(
                trades=[], rejected=True,
                reject_reason=f"Invalid volume: {volume}",
                remaining_state=self._state,
            )

        if price <= 0:
            return self.OrderResult(
                trades=[], rejected=True,
                reject_reason=f"Invalid price: {price}",
                remaining_state=self._state,
            )

        # ---- Check price limit ----
        limit_result = self._check_price_limit(ts_code, price, direction, pre_close)
        if limit_result:
            return self.OrderResult(
                trades=[], rejected=True,
                reject_reason=limit_result,
                remaining_state=self._state,
            )

        # ---- Execute ----
        if direction.upper() == "BUY":
            return self._execute_buy(ts_code, price, volume, trade_date)
        elif direction.upper() == "SELL":
            return self._execute_sell(ts_code, price, volume, trade_date)
        else:
            return self.OrderResult(
                trades=[], rejected=True,
                reject_reason=f"Unknown direction: {direction}",
                remaining_state=self._state,
            )

    # ============================================================
    # Internal: Buy execution
    # ============================================================

    def _execute_buy(
        self, ts_code: str, price: float, volume: int, trade_date: date,
    ) -> "BrokerSim.OrderResult":
        """Execute a buy order. Lock shares for T+1 settlement."""
        # Apply slippage: buy slightly higher
        exec_price = price * (1.0 + self._rng.uniform(0, self._slippage_rate))
        amount = exec_price * volume
        commission = max(self._commission_rate * amount, self._min_commission)
        total_cost = amount + commission

        if self._state.cash < total_cost:
            max_vol = self._round_lot(
                int((self._state.cash - self._min_commission) / (exec_price * (1 + self._commission_rate)))
            )
            if max_vol <= 0:
                return self.OrderResult(
                    trades=[], rejected=True,
                    reject_reason=f"Insufficient cash: need ¥{total_cost:.2f}, have ¥{self._state.cash:.2f}",
                    remaining_state=self._state,
                )
            volume = max_vol
            amount = exec_price * volume
            commission = max(self._commission_rate * amount, self._min_commission)
            total_cost = amount + commission

        # Create trade
        order_id = f"B{self._next_order_id:06d}"
        self._next_order_id += 1
        trade = Trade(
            order_id=order_id,
            ts_code=ts_code,
            direction="BUY",
            price=exec_price,
            volume=volume,
            amount=amount,
            commission=commission,
            stamp_tax=0.0,  # No stamp tax on buy
            trade_date=trade_date,
        )

        # Update position
        old_pos = self._state.positions.get(ts_code)
        old_vol = old_pos.volume if old_pos else 0
        old_cost = old_pos.avg_cost if old_pos else 0.0
        new_vol = old_vol + volume
        new_avg_cost = ((old_cost * old_vol) + (exec_price * volume)) / new_vol if new_vol > 0 else 0.0

        new_pos = Position(
            ts_code=ts_code,
            volume=new_vol,
            available_volume=old_pos.available_volume if old_pos else 0,  # New shares NOT available
            avg_cost=new_avg_cost,
            current_price=price,
        )

        new_positions = {**self._state.positions, ts_code: new_pos}
        new_settlement = {
            **self._state.pending_settlement,
            ts_code: (volume, exec_price),  # Lock these shares until T+1
        }
        new_cash = self._state.cash - total_cost

        new_state = AccountState(
            cash=new_cash,
            positions=new_positions,
            pending_settlement=new_settlement,
            trade_history=self._state.trade_history + [trade],
        )

        return self.OrderResult(
            trades=[trade], rejected=False, reject_reason="",
            remaining_state=new_state,
        )

    # ============================================================
    # Internal: Sell execution
    # ============================================================

    def _execute_sell(
        self, ts_code: str, price: float, volume: int, trade_date: date,
    ) -> "BrokerSim.OrderResult":
        """Execute a sell order."""
        # Apply slippage: sell slightly lower
        exec_price = price * (1.0 - self._rng.uniform(0, self._slippage_rate))
        pos = self._state.positions.get(ts_code)
        if pos is None:
            return self.OrderResult(
                trades=[], rejected=True,
                reject_reason=f"No position for {ts_code}",
                remaining_state=self._state,
            )

        if volume > pos.available_volume:
            volume = pos.available_volume
            if volume <= 0:
                return self.OrderResult(
                    trades=[], rejected=True,
                    reject_reason=f"All shares of {ts_code} locked (T+1). Available: {pos.available_volume}",
                    remaining_state=self._state,
                )

        amount = exec_price * volume
        commission = max(self._commission_rate * amount, self._min_commission)
        stamp_tax = self._stamp_tax_rate * amount  # Stamp tax on sell only

        # Create trade
        order_id = f"S{self._next_order_id:06d}"
        self._next_order_id += 1
        trade = Trade(
            order_id=order_id,
            ts_code=ts_code,
            direction="SELL",
            price=exec_price,
            volume=volume,
            amount=amount,
            commission=commission,
            stamp_tax=stamp_tax,
            trade_date=trade_date,
        )

        # Update position
        new_vol = pos.volume - volume
        new_cash = self._state.cash + amount - commission - stamp_tax

        if new_vol > 0:
            new_positions = {
                **self._state.positions,
                ts_code: Position(
                    ts_code=ts_code,
                    volume=new_vol,
                    available_volume=pos.available_volume - volume,
                    avg_cost=pos.avg_cost,
                    current_price=pos.current_price,
                ),
            }
        else:
            new_positions = {k: v for k, v in self._state.positions.items() if k != ts_code}

        new_state = AccountState(
            cash=new_cash,
            positions=new_positions,
            pending_settlement=self._state.pending_settlement,
            trade_history=self._state.trade_history + [trade],
        )

        return self.OrderResult(
            trades=[trade], rejected=False, reject_reason="",
            remaining_state=new_state,
        )

    # ============================================================
    # Helpers
    # ============================================================

    @staticmethod
    def _round_lot(volume: int) -> int:
        """Round down to nearest lot (100 shares)."""
        return (volume // 100) * 100

    def _check_price_limit(
        self, ts_code: str, price: float, direction: str, pre_close: float = 0.0,
    ) -> Optional[str]:
        """Check if order price violates A-share price limits.

        Uses:
        - Position's current_price for existing positions,
        - pre_close for new buys (no existing position),
        - Skips check if neither is available.

        Returns: Error message string, or None if price is within limits.
        """
        limit_pct = 0.20 if ts_code.startswith(("688", "300")) else 0.10

        # Find reference price
        ref_price = 0.0
        pos = self._state.positions.get(ts_code)
        if pos is not None:
            ref_price = pos.current_price
        elif pre_close > 0:
            ref_price = pre_close
        else:
            return None  # No reference available — skip check

        if ref_price <= 0:
            return None

        pct_from_ref = abs(price - ref_price) / ref_price
        if pct_from_ref > limit_pct * 1.01:  # Small tolerance
            return f"Price {price:.2f} exceeds {limit_pct*100:.0f}% limit from reference {ref_price:.2f}"

        return None
