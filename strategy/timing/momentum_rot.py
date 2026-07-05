"""
Momentum rotation strategy — buy strongest performers, rotate monthly.

In A-shares, momentum is one of the strongest anomalies.
Buy stocks with the highest recent returns, hold until momentum fades.
"""

from datetime import date
import numpy as np
import pandas as pd

from strategy.timing._rsi import calc_rsi
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

            # Compute momentum using date-based lookback (not position index)
            lookback_date = current_date - pd.Timedelta(days=self._mom_period)
            close_before = subset[subset["trade_date"] <= pd.Timestamp(lookback_date)]
            if not close_before.empty:
                ref_price = close_before["close"].iloc[-1]
                mom_ret = (close.iloc[-1] / ref_price - 1.0) if ref_price > 0 else 0.0
            else:
                mom_ret = 0.0
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

        # Sell stocks that fell out of top momentum or momentum turned negative
        sell_codes = set()
        for code in self.holdings:
            if code in sell_codes:
                continue  # Already generated sell for this stock today

            should_sell = False
            reason = ""
            conf = 0.7

            if code not in top_codes:
                should_sell = True
                reason = "Momentum faded — fell out of top"

            subset = data[data["ts_code"] == code].sort_values("trade_date")
            if not subset.empty and len(subset) >= self._mom_period:
                lookback_date = current_date - pd.Timedelta(days=self._mom_period)
                close_before = subset[subset["trade_date"] <= pd.Timestamp(lookback_date)]
                if not close_before.empty:
                    ref_price = close_before["close"].iloc[-1]
                    current_price = subset["close"].iloc[-1]
                    mom = (current_price / ref_price - 1.0) if ref_price > 0 else 0.0
                    if mom < -0.05:  # -5% momentum → cut loss
                        should_sell = True
                        reason = f"Momentum reversed ({mom*100:.1f}%)"
                        conf = 0.8

            if should_sell:
                sell_codes.add(code)
                signals.append(Signal(
                    ts_code=code, signal_type=SignalType.SELL,
                    confidence=conf, reason=reason,
                    target_weight=0.0, timestamp=current_date,
                ))

        return signals
