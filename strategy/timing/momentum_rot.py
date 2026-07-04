"""
Momentum rotation strategy — buy strongest performers, rotate monthly.

In A-shares, momentum is one of the strongest anomalies.
Buy stocks with the highest recent returns, hold until momentum fades.
"""

from datetime import date
import numpy as np
import pandas as pd

from strategy.base.strategy_template import BaseStrategy, Signal, SignalType


class MomentumRotation(BaseStrategy):
    """Buy top momentum stocks, rotate when momentum ranking changes.

    Parameters:
    - mom_period: Momentum lookback days (60 = ~3 months)
    - top_n: Number of stocks to hold
    - rebalance_days: Days between rebalancing (20 = ~1 month)
    """

    def __init__(self, name: str = "momentum_rot"):
        super().__init__(name)
        self._mom_period = 60
        self._top_n = 10
        self._rebalance_days = 20
        self._min_price = 5.0
        self._last_rebalance: dict[str, date] = {}

    def init(self, **params) -> None:
        self._mom_period = params.get("mom_period", 60)
        self._top_n = params.get("top_n", 10)
        self._rebalance_days = params.get("rebalance_days", 20)
        self._min_price = params.get("min_price", 5.0)
        super().init(**params)

    def on_data(self, data: pd.DataFrame, current_date: date) -> list[Signal]:
        if data.empty:
            return []

        signals = []
        codes = data["ts_code"].unique()
        scored = []

        for code in codes:
            subset = data[data["ts_code"] == code].sort_values("trade_date")
            if len(subset) < self._mom_period + 20:
                continue

            close = subset["close"]
            price = close.iloc[-1]
            if price < self._min_price:
                continue

            # Compute momentum: return over the lookback period
            mom_ret = (close.iloc[-1] / close.iloc[-self._mom_period] - 1.0) if len(close) >= self._mom_period else 0.0
            if mom_ret <= 0:
                continue  # Only buy positive momentum

            scored.append((code, mom_ret, price, f"mom={mom_ret*100:.1f}%"))

        # Sort by momentum (highest first)
        scored.sort(key=lambda x: x[1], reverse=True)
        top_codes = {s[0] for s in scored[:self._top_n]}

        # Generate buy signals for top momentum stocks not held
        for code, mom, price, reason in scored[:self._top_n]:
            conf = min(mom / 0.30, 1.0) if mom > 0 else 0.0  # 30%+ momentum = 100% confidence
            if code not in self.holdings:
                signals.append(Signal(
                    ts_code=code, signal_type=SignalType.BUY,
                    confidence=conf,
                    reason=f"Momentum: {reason}",
                    target_weight=1.0 / self._top_n,
                    timestamp=current_date,
                ))

        # Sell stocks that fell out of top momentum
        for code in self.holdings:
            if code not in top_codes:
                signals.append(Signal(
                    ts_code=code, signal_type=SignalType.SELL,
                    confidence=0.7,
                    reason="Momentum faded — fell out of top",
                    target_weight=0.0, timestamp=current_date,
                ))

            # Also sell if momentum turned negative
            subset = data[data["ts_code"] == code].sort_values("trade_date")
            if not subset.empty and len(subset["close"]) >= self._mom_period:
                close = subset["close"]
                mom = (close.iloc[-1] / close.iloc[-self._mom_period] - 1.0)
                if mom < -0.05:  # -5% momentum → cut loss
                    signals.append(Signal(
                        ts_code=code, signal_type=SignalType.SELL,
                        confidence=0.8,
                        reason=f"Momentum reversed ({mom*100:.1f}%)",
                        target_weight=0.0, timestamp=current_date,
                    ))

        return signals
