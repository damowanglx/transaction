"""
Trend-following strategy for A-shares.

Core idea: Buy stocks showing strong upward momentum with
volume confirmation. Hold until trend weakens (MACD death cross
or price breaks below moving average).

Key signals:
- Buy: MA5 > MA20 (golden cross) + volume ratio > 1.2 + RSI 50-80
- Sell: MA5 < MA20 (death cross) OR price drops below MA60

This works well in trending markets and is the most robust
strategy type in momentum-driven A-shares.
"""

from datetime import date

import numpy as np
import pandas as pd

from strategy.base.strategy_template import BaseStrategy, Signal, SignalType

# Use ta library for technical indicators where available
try:
    import ta
except ImportError:
    ta = None


class TrendFollowStrategy(BaseStrategy):
    """Simple moving-average trend following strategy.

    Parameters:
    - ma_fast: Fast MA period (default 5)
    - ma_slow: Slow MA period (default 20)
    - ma_trend: Trend filter MA period (default 60)
    - vol_ratio_min: Minimum volume ratio for confirmation (default 1.2)
    - rsi_low: RSI lower bound (default 40, below = oversold/weak trend)
    - rsi_high: RSI upper bound (default 85, above = overbought/reversal risk)
    - top_n: Max stocks to hold simultaneously
    """

    def __init__(self, name: str = "trend_follow"):
        super().__init__(name)
        self._ma_fast = 5
        self._ma_slow = 20
        self._ma_trend = 60
        self._vol_ratio_min = 1.2
        self._rsi_low = 40
        self._rsi_high = 85
        self._top_n = 10

    def init(self, **params) -> None:
        self._ma_fast = params.get("ma_fast", 5)
        self._ma_slow = params.get("ma_slow", 20)
        self._ma_trend = params.get("ma_trend", 60)
        self._vol_ratio_min = params.get("vol_ratio_min", 1.2)
        self._rsi_low = params.get("rsi_low", 40)
        self._rsi_high = params.get("rsi_high", 85)
        self._top_n = params.get("top_n", 10)
        super().init(**params)

    def on_data(self, data: pd.DataFrame, current_date: date) -> list[Signal]:
        """Generate signals based on trend indicators."""
        if data.empty:
            return []

        signals = []
        codes = data["ts_code"].unique()

        # Score each stock
        scored = []
        for code in codes:
            subset = data[data["ts_code"] == code].sort_values("trade_date")
            if len(subset) < self._ma_trend + 10:
                continue

            close = subset["close"]
            vol = subset["vol"]

            # Compute indicators
            ma_fast = close.rolling(self._ma_fast).mean().iloc[-1]
            ma_slow = close.rolling(self._ma_slow).mean().iloc[-1]
            ma_trend_val = close.rolling(self._ma_trend).mean().iloc[-1]
            current_price = close.iloc[-1]

            # Volume ratio
            vol_short = vol.rolling(5).mean().iloc[-1]
            vol_long = vol.rolling(20).mean().iloc[-1]
            vol_ratio = vol_short / vol_long if vol_long > 0 else 0

            # RSI
            rsi_val = self._calc_rsi(close, 14)

            # Score
            score = 0.0
            reasons = []

            # Golden cross bonus
            if ma_fast > ma_slow:
                score += 2.0
                reasons.append("MA golden cross")

            # Price above trend MA
            if current_price > ma_trend_val:
                score += 1.0
                reasons.append("above MA trend")

            # Volume confirmation
            if vol_ratio > self._vol_ratio_min:
                score += 1.0
                reasons.append(f"vol_ratio={vol_ratio:.1f}")

            # RSI sweet spot
            if self._rsi_low <= rsi_val <= self._rsi_high:
                score += 1.0
                reasons.append(f"RSI={rsi_val:.0f}")
            elif rsi_val > self._rsi_high:
                score -= 1.0  # Overbought penalty

            scored.append((code, score, current_price, " + ".join(reasons)))

        # Sort by score
        scored.sort(key=lambda x: x[1], reverse=True)
        top_codes = {s[0] for s in scored[:self._top_n] if s[1] >= 2.0}

        # Generate buy signals for top scored stocks not held
        for code, score, price, reason in scored[:self._top_n]:
            if code not in self.holdings and score >= 2.0:
                signals.append(Signal(
                    ts_code=code,
                    signal_type=SignalType.BUY,
                    confidence=min(score / 5.0, 1.0),
                    reason=f"Trend follow: {reason}",
                    target_weight=1.0 / self._top_n,
                    timestamp=current_date,
                ))

        # Generate sell signals for held stocks that fell out of top or weakened
        for code in self.holdings:
            if code not in top_codes:
                signals.append(Signal(
                    ts_code=code,
                    signal_type=SignalType.SELL,
                    confidence=0.7,
                    reason="Fell out of trend selection",
                    target_weight=0.0,
                    timestamp=current_date,
                ))

            # Also sell if price breaks below MA_trend
            subset = data[data["ts_code"] == code]
            if not subset.empty:
                close = subset.sort_values("trade_date")["close"]
                current_price = close.iloc[-1]
                ma_trend_val = close.rolling(self._ma_trend).mean().iloc[-1]
                if current_price < ma_trend_val and code not in {s.ts_code for s in signals}:
                    signals.append(Signal(
                        ts_code=code, signal_type=SignalType.SELL,
                        confidence=0.8,
                        reason=f"Price broke below MA{self._ma_trend}",
                        target_weight=0.0, timestamp=current_date,
                    ))

        return signals

    @staticmethod
    def _calc_rsi(prices: pd.Series, period: int = 14) -> float:
        from strategy.timing._rsi import calc_rsi
        return calc_rsi(prices, period)
