"""
RSI-only strategy — simple oversold/overbought.
Buy when RSI deeply oversold, sell when RSI recovers.
"""

from datetime import date
import numpy as np
import pandas as pd

from strategy.base.strategy_template import BaseStrategy, Signal, SignalType


class RSIStrategy(BaseStrategy):
    """Pure RSI mean reversion — no Bollinger Bands needed.

    Parameters:
    - rsi_period: RSI calculation period (default 14)
    - rsi_oversold: Buy threshold (default 25)
    - rsi_overbought: Sell threshold (default 60)
    - top_n: Max holdings
    """

    def __init__(self, name: str = "rsi_only"):
        super().__init__(name)
        self._rsi_period = 14
        self._rsi_oversold = 25
        self._rsi_overbought = 60
        self._top_n = 10
        self._min_price = 5.0
        self._min_turnover = 1.0

    def init(self, **params) -> None:
        self._rsi_period = params.get("rsi_period", 14)
        self._rsi_oversold = params.get("rsi_oversold", 25)
        self._rsi_overbought = params.get("rsi_overbought", 60)
        self._top_n = params.get("top_n", 10)
        self._min_price = params.get("min_price", 5.0)
        self._min_turnover = params.get("min_turnover", 1.0)
        super().init(**params)

    def on_data(self, data: pd.DataFrame, current_date: date) -> list[Signal]:
        if data.empty:
            return []

        signals = []
        codes = data["ts_code"].unique()
        scored = []

        for code in codes:
            subset = data[data["ts_code"] == code].sort_values("trade_date")
            if len(subset) < 50:
                continue

            close = subset["close"]
            price = close.iloc[-1]
            if price < self._min_price:
                continue

            if "turnover_rate" in subset.columns:
                avg_to = subset["turnover_rate"].rolling(20).mean().iloc[-1]
                if avg_to > 0 and avg_to < self._min_turnover:
                    continue

            rsi = self._calc_rsi(close, self._rsi_period)
            # Score: lower RSI = more oversold = higher score
            if rsi <= self._rsi_oversold:
                score = (self._rsi_oversold - rsi) + 1.0  # RSI 20 → score 6, RSI 25 → score 1
                scored.append((code, score, price, f"RSI={rsi:.0f}"))

        scored.sort(key=lambda x: x[1], reverse=True)
        top_codes = {s[0] for s in scored[:self._top_n] if s[1] >= 1.0}

        for code, score, price, reason in scored[:self._top_n]:
            if code not in self.holdings and score >= 1.0:
                signals.append(Signal(
                    ts_code=code, signal_type=SignalType.BUY,
                    confidence=min(score / 10.0, 1.0),
                    reason=f"RSI oversold: {reason}",
                    target_weight=1.0 / self._top_n,
                    timestamp=current_date,
                ))

        for code, pos_data in self.holdings.items():
            entry_price = pos_data.get("avg_cost", 0.0)
            subset = data[data["ts_code"] == code].sort_values("trade_date")
            if subset.empty:
                continue
            close = subset["close"]
            price = close.iloc[-1]
            rsi = self._calc_rsi(close, self._rsi_period)
            pnl_pct = (price - entry_price) / entry_price if entry_price > 0 else 0

            should_sell = False
            reason = ""
            if rsi >= self._rsi_overbought:
                should_sell = True
                reason = f"RSI recovered ({rsi:.0f})"
            elif pnl_pct < -self._stop_loss:
                should_sell = True
                reason = f"Stop loss ({pnl_pct*100:.1f}%)"
            elif code not in top_codes:
                should_sell = True
                reason = "Fell out of top selection"

            if should_sell:
                signals.append(Signal(
                    ts_code=code, signal_type=SignalType.SELL,
                    confidence=0.7, reason=reason, target_weight=0.0,
                    timestamp=current_date,
                ))

        return signals

    @staticmethod
    def _calc_rsi(prices: pd.Series, period: int = 14) -> float:
        from strategy.timing._rsi import calc_rsi
        return calc_rsi(prices, period)
