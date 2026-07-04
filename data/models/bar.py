"""
K-line (OHLCV) data models.

Both daily and minute bar representations.
Immutable — always create new instances, never mutate.
"""

from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass(frozen=True)
class DailyBar:
    """Single daily K-line bar. Frozen — treat as a value object."""

    ts_code: str
    trade_date: date
    open: float
    high: float
    low: float
    close: float
    pre_close: float
    change: float
    pct_chg: float
    vol: float
    amount: float
    turnover_rate: float = 0.0
    pe: float | None = None
    pb: float | None = None
    is_st: int = 0

    @property
    def is_up(self) -> bool:
        """Return True if bar closed higher than it opened."""
        return self.close >= self.open

    @property
    def daily_range(self) -> float:
        """Return (high - low) range."""
        return self.high - self.low

    @property
    def body_pct(self) -> float:
        """Return body size as percentage of range."""
        rng = self.daily_range
        if rng == 0:
            return 0.0
        return abs(self.close - self.open) / rng

    @property
    def is_limit_up(self) -> bool:
        """Check if bar hit 10% limit up (main board)."""
        return self.pct_chg >= 9.9

    @property
    def is_limit_down(self) -> bool:
        """Check if bar hit 10% limit down (main board)."""
        return self.pct_chg <= -9.9


@dataclass(frozen=True)
class MinuteBar:
    """Single minute K-line bar."""

    ts_code: str
    trade_time: datetime
    open: float
    high: float
    low: float
    close: float
    vol: float
    amount: float


@dataclass(frozen=True)
class StockInfo:
    """Stock basic information."""

    ts_code: str
    name: str
    area: str
    industry: str
    market: str
    list_date: date
    delist_date: date | None = None


