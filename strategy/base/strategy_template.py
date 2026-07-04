"""
Abstract strategy base class.

All strategies inherit from this. Defines the contract:
- init(): Initialize the strategy with config
- on_bar(bar): Process a single bar event
- on_signal(): Generate buy/sell/hold signal
- get_positions(): Return target portfolio weights

Immutable pattern: strategies return NEW signal objects, never mutate state.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Optional

import pandas as pd


class SignalType(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass(frozen=True)
class Signal:
    """Immutable trading signal. Always create new, never mutate."""

    ts_code: str
    signal_type: SignalType
    confidence: float  # 0.0 to 1.0
    reason: str
    target_weight: float = 0.0  # Target portfolio weight (0.0-1.0)
    timestamp: date = field(default_factory=date.today)

    @property
    def is_tradeable(self) -> bool:
        """Signal is actionable (buy or sell, not hold)."""
        return self.signal_type in (SignalType.BUY, SignalType.SELL)

    @property
    def is_confident(self) -> bool:
        """Signal confidence exceeds threshold for action."""
        return self.confidence >= 0.5


class BaseStrategy(ABC):
    """Abstract base for all trading strategies.

    Subclasses must implement:
    - init(params): Configure strategy parameters
    - on_data(data): Process market data and return signals

    Lifecycle:
        strategy.init(**params)
        for each trading day:
            strategy.sync_positions(holdings_dict)  # sync from broker
            signals = strategy.on_data(data, date)   # generate signals
            execute(signals)                         # broker fills orders
            strategy.sync_positions(holdings_dict)  # sync after fills
    """

    def __init__(self, name: str):
        self.name = name
        self._initialized = False
        self._holdings: dict[str, dict] = {}  # ts_code → {volume, avg_cost, market_value}

    @abstractmethod
    def init(self, **params) -> None:
        """Initialize strategy with parameters. Called once before first use."""
        self._initialized = True

    @abstractmethod
    def on_data(self, data: pd.DataFrame, current_date: date) -> list[Signal]:
        """Process market data and return list of trading signals.

        Args:
            data: DataFrame with columns [ts_code, trade_date, open, high, low,
                  close, vol, amount, ...]
            current_date: Current trading date (signals are generated for this date)

        Returns:
            List of Signal objects. Empty list means no action.
        """
        ...

    # ============================================================
    # CTA-style convenience methods (vnpy-inspired)
    # ============================================================

    def on_bar(self, ts_code: str, bar: pd.Series) -> Signal | None:
        """CTA-style: process a single bar for a single stock.

        Override this for simple per-stock strategies.
        Default: returns None (no signal). Use on_data() for complex logic.
        """
        return None

    def on_bars(self, stock_data: dict[str, pd.DataFrame]) -> list[Signal]:
        """CTA-style: process latest bars for all stocks at once.

        Override for cross-sectional strategies.
        Default: calls on_bar for each stock. Override for full control.

        Args:
            stock_data: {ts_code: DataFrame with all historical bars for this stock}

        Returns:
            List of Signal objects.
        """
        signals = []
        for code, df in stock_data.items():
            if df.empty:
                continue
            bar = df.iloc[-1]
            sig = self.on_bar(code, bar)
            if sig is not None:
                signals.append(sig)
        return signals

    def get_history(self, data: pd.DataFrame, ts_code: str, days: int) -> pd.DataFrame:
        """Get N days of history for a single stock from the full dataset.

        Args:
            data: Full backtest data (all stocks, all dates).
            ts_code: Target stock code.
            days: Number of recent trading days.

        Returns:
            Subset of data for this stock, last N days.
        """
        subset = data[data["ts_code"] == ts_code].sort_values("trade_date")
        return subset.tail(days)

    def sync_positions(self, holdings: dict[str, dict]) -> None:
        """Sync internal state with actual broker holdings.

        Called by the backtest engine (or live trader) after order execution.
        Strategies use this to track what they actually own.

        Args:
            holdings: {ts_code: {volume, avg_cost, market_value, current_price}}
        """
        self._holdings = holdings

    @property
    def holdings(self) -> dict[str, dict]:
        """Current holdings known to the strategy."""
        return self._holdings

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    def is_held(self, ts_code: str) -> bool:
        """Check if a stock is currently held."""
        return ts_code in self._holdings

    def entry_price(self, ts_code: str) -> float:
        """Return the entry/avg cost for a held stock."""
        return self._holdings.get(ts_code, {}).get("avg_cost", 0.0)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name='{self.name}')"
