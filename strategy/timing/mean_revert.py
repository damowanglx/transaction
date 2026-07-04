"""
Mean-reversion strategy for A-shares.

Core idea: Stocks that have temporarily deviated from their
mean tend to revert. This strategy identifies oversold conditions
and buys, then sells when price returns to the mean.

Key signals:
- Buy: Price near lower Bollinger Band + RSI < 35 + price below MA20
- Sell: Price near middle Bollinger Band OR RSI > 60 OR price above MA20

This strategy benefits from A-share volatility and works best
in range-bound/sideways markets.
"""

from datetime import date

import numpy as np
import pandas as pd

from strategy.base.strategy_template import BaseStrategy, Signal, SignalType


class MeanRevertStrategy(BaseStrategy):
    """Bollinger Band mean-reversion strategy.

    Parameters:
    - bb_period: Bollinger Band period (default 20)
    - bb_std: Number of standard deviations (default 2.0)
    - rsi_oversold: RSI threshold for buy (default 35)
    - rsi_overbought: RSI threshold for sell (default 65)
    - volume_min: Minimum daily volume for liquidity (default 0, no filter)
    - top_n: Max stocks to hold
    """

    def __init__(self, name: str = "mean_revert"):
        super().__init__(name)
        self._bb_period = 20
        self._bb_std = 2.0
        self._rsi_oversold = 35
        self._rsi_overbought = 65
        self._volume_min = 0
        self._top_n = 10
        self._stop_loss = 0.05
        self._take_profit = 0.15

    def init(self, **params) -> None:
        self._bb_period = params.get("bb_period", 20)
        self._bb_std = params.get("bb_std", 2.0)
        self._rsi_oversold = params.get("rsi_oversold", 35)
        self._rsi_overbought = params.get("rsi_overbought", 65)
        self._volume_min = params.get("volume_min", 0)
        self._top_n = params.get("top_n", 10)
        self._stop_loss = params.get("stop_loss", 0.05)
        self._take_profit = params.get("take_profit", 0.15)
        self._min_price = params.get("min_price", 5.0)
        self._min_turnover = params.get("min_turnover", 1.0)
        self._ma_trend = params.get("ma_trend", 120)        # Trend filter (off by default in main flow)
        self._vol_spike = params.get("vol_spike", 0.0)      # Volume spike: 0=disabled
        self._bb_entry = params.get("bb_entry", 1.0)        # BB entry: 1.0=disabled (pass all)
        self._market_regime = params.get("market_regime", True)  # Skip trending markets
        self._market_data = params.get("market_data", None)       # CSI 300 close series
        self._atr_mult = params.get("atr_mult", 2.0)             # ATR multiplier for stop loss
        self._use_atr_stop = params.get("use_atr_stop", True)    # Use ATR dynamic stop loss
        self._vol_target = params.get("vol_target", 0.15)        # Target annual volatility (15%)
        self._use_vol_target = params.get("use_vol_target", True) # Adaptive position sizing
        self._green_candle = params.get("green_candle", True)      # Require green candle for entry
        super().init(**params)

    def on_data(self, data: pd.DataFrame, current_date: date) -> list[Signal]:
        """Generate mean-reversion signals."""
        if data.empty:
            return []

        # Market regime filter: skip mean reversion in strong downtrends
        if self._market_regime and self._market_data is not None and len(self._market_data) >= 50:
            # Slice to only use data up to current_date (no look-ahead)
            market_up_to_today = self._market_data[self._market_data.index <= pd.Timestamp(current_date)]
            if len(market_up_to_today) >= 50:
                market_ma50 = market_up_to_today.rolling(50).mean().iloc[-1]
                market_price = market_up_to_today.iloc[-1]
                # Market below MA50 (downtrend) → skip to avoid falling knives
                if market_price < market_ma50 * 0.95:
                    return []

        signals = []
        codes = data["ts_code"].unique()

        # Check for precomputed indicators (speed optimization)
        has_precomputed_cols = "bb_position_3" in data.columns and "rsi_14" in data.columns

        # Score stocks for reversion potential
        scored = []
        for code in codes:
            subset = data[data["ts_code"] == code].sort_values("trade_date")
            if len(subset) < self._bb_period + 10:
                continue

            close = subset["close"]
            current_price = close.iloc[-1]

            # Per-stock flag — does NOT leak across stock iterations
            use_precomputed = has_precomputed_cols
            if use_precomputed:
                # Fast path: use pre-computed indicator columns
                last = subset.iloc[-1]
                bb_position = last.get("bb_position_3", np.nan)
                rsi = last.get("rsi_14", np.nan)
                vol_ratio = last.get("vol_ratio", 1.0)
                if pd.isna(bb_position) or pd.isna(rsi):
                    use_precomputed = False  # Fall back for this stock only

            # Quality filter: skip penny stocks
            if current_price < self._min_price:
                continue

            # Quality filter: skip illiquid stocks (low turnover)
            if "turnover_rate" in subset.columns:
                avg_turnover = subset["turnover_rate"].rolling(20).mean().iloc[-1]
                if avg_turnover > 0 and avg_turnover < self._min_turnover:
                    continue

            # Multi-timeframe: weekly trend must not be strongly down
            close_indexed = close.copy()
            close_indexed.index = pd.DatetimeIndex(subset["trade_date"].values)
            weekly = close_indexed.resample("W-FRI").last().dropna()
            if len(weekly) >= 20:
                weekly_ma20 = weekly.rolling(20).mean().iloc[-1]
                weekly_price = weekly.iloc[-1]
                if weekly_price < weekly_ma20 * 0.95:
                    continue  # Weekly downtrend — skip oversold on daily

            if not use_precomputed:
                # Slow path: compute indicators from scratch
                ma = close.rolling(self._bb_period).mean().iloc[-1]
                std = close.rolling(self._bb_period).std().iloc[-1]
                rsi = self._calc_rsi(close, 14)
                upper = ma + self._bb_std * std
                lower = ma - self._bb_std * std

                vol = subset["vol"]
                avg_vol_20 = vol.rolling(20).mean().iloc[-1]
                recent_vol = vol.iloc[-1]
                vol_ratio = recent_vol / avg_vol_20 if avg_vol_20 > 0 else 1.0

                band_range = upper - lower
                bb_position = (current_price - lower) / band_range if band_range > 0 else 0.5
                dev_from_ma = (current_price - ma) / ma if ma > 0 else 0.0
            else:
                # Fast path: precomputed columns already have rsi, bb_position, vol_ratio
                dev_from_ma = 0.0  # Not used in scoring for 3σ strategy

            # Volume confirmation
            if vol_ratio < self._vol_spike:
                continue

            # BB position filter
            if bb_position > self._bb_entry:
                continue

            # Score: higher = better buy candidate (oversold)
            score = 0.0
            reasons = []

            # Deep oversold
            score += 3.0
            reasons.append(f"BB={bb_position:.2f} vol_ratio={vol_ratio:.1f}")

            # Oversold RSI
            if rsi <= self._rsi_oversold:
                score += 2.0
                reasons.append(f"RSI oversold ({rsi:.0f})")
            elif rsi < 40:
                score += 1.0
                reasons.append(f"RSI low ({rsi:.0f})")

            # Below MA20 deviation
            if dev_from_ma < -0.03:
                score += 1.0
                reasons.append(f"dev=-{abs(dev_from_ma)*100:.1f}%")

            # Penalty: near upper band or overbought
            if rsi >= self._rsi_overbought:
                score -= 2.0
                reasons.append(f"RSI overbought ({rsi:.0f})")

            if bb_position > 0.85:
                score -= 2.0
                reasons.append("near upper BB")

            scored.append((code, score, current_price, " + ".join(reasons) if reasons else "no signal", bb_position))

        # Select top oversold stocks
        scored.sort(key=lambda x: x[1], reverse=True)
        top_codes = {s[0] for s in scored[:self._top_n] if s[1] >= 2.0}

        # Generate buy signals
        for code, score, price, reason, stock_bb_pos in scored[:self._top_n]:
            if code not in self.holdings and score >= 2.0:
                # Green candle confirmation: must stop falling before buying
                subset_sorted = data[data["ts_code"] == code].sort_values("trade_date")
                if self._green_candle and len(subset_sorted) >= 2:
                    last = subset_sorted.iloc[-1]
                    prev = subset_sorted.iloc[-2]
                    if last["close"] < last["open"]:  # Red candle today
                        continue  # Still falling — wait
                # Volatility targeting: scale position by stock volatility
                stock_subset = subset_sorted
                base_weight = 1.0 / self._top_n
                if self._use_vol_target and len(stock_subset) >= 20:
                    ret = stock_subset["close"].pct_change().dropna()
                    if len(ret) >= 20:
                        ann_vol = ret.rolling(20).std().iloc[-1] * np.sqrt(244)
                        if ann_vol > 0:
                            vol_scale = self._vol_target / ann_vol
                            vol_scale = max(0.3, min(vol_scale, 2.0))
                            base_weight *= np.sqrt(vol_scale)
                            base_weight = min(base_weight, 0.25)
                # Confidence based on oversold depth
                conf = max(0.3, min(1.0 - stock_bb_pos, 1.0))
                signals.append(Signal(
                    ts_code=code,
                    signal_type=SignalType.BUY,
                    confidence=conf,
                    reason=f"Mean revert: {reason}",
                    target_weight=base_weight,
                    timestamp=current_date,
                ))

        # Generate sell signals for held stocks that reverted to mean
        for code, pos_data in self.holdings.items():
            entry_price = pos_data.get("avg_cost", 0.0)
            subset = data[data["ts_code"] == code].sort_values("trade_date")
            if subset.empty:
                continue

            close = subset["close"]
            current_price = close.iloc[-1]
            ma = close.rolling(self._bb_period).mean().iloc[-1]
            std = close.rolling(self._bb_period).std().iloc[-1]
            rsi = self._calc_rsi(close, 14)
            upper = ma + self._bb_std * std
            lower = ma - self._bb_std * std
            band_range = upper - lower
            bb_position = (current_price - lower) / band_range if band_range > 0 else 0.5

            # Sell conditions
            should_sell = False
            sell_reason = ""

            # Reverted to mean
            if bb_position > 0.5 and current_price > entry_price:
                should_sell = True
                sell_reason = f"reverted to mean (BB pos={bb_position:.2f})"

            # RSI overbought
            if rsi > self._rsi_overbought:
                should_sell = True
                sell_reason = f"RSI overbought ({rsi:.0f})"

            # ATR dynamic stop loss (adaptive to stock volatility)
            if entry_price > 0:
                pnl_pct = (current_price - entry_price) / entry_price
                stop_threshold = -self._stop_loss  # Fixed fallback
                if self._use_atr_stop and "high" in subset.columns:
                    true_range = pd.concat([
                        subset["high"] - subset["low"],
                        (subset["high"] - subset["close"].shift(1)).abs(),
                        (subset["low"] - subset["close"].shift(1)).abs(),
                    ], axis=1).max(axis=1)
                    atr_val = true_range.rolling(14).mean().iloc[-1]
                    if atr_val > 0:
                        stop_threshold = -(self._atr_mult * atr_val / entry_price)
                        stop_threshold = max(stop_threshold, -0.10)
                        stop_threshold = min(stop_threshold, -0.015)
                if pnl_pct < stop_threshold:
                    should_sell = True
                    sell_reason = f"ATR stop ({pnl_pct*100:.1f}% < {stop_threshold*100:.1f}%)"

            if should_sell:
                signals.append(Signal(
                    ts_code=code,
                    signal_type=SignalType.SELL,
                    confidence=0.7,
                    reason=sell_reason,
                    target_weight=0.0,
                    timestamp=current_date,
                ))

        # Also sell holdings that fell out of top reversion candidates
        # (they may have reverted already)
        for code in self.holdings:
            if code not in top_codes:
                subset = data[data["ts_code"] == code].sort_values("trade_date")
                if not subset.empty:
                    close = subset["close"]
                    current_price = close.iloc[-1]
                    pos_data = self.holdings[code]
                    entry_price = pos_data.get("avg_cost", 0.0) if isinstance(pos_data, dict) else pos_data
                    if entry_price > 0:
                        pnl_pct = (current_price - entry_price) / entry_price
                        if pnl_pct > self._take_profit:  # Take profit threshold
                            signals.append(Signal(
                                ts_code=code,
                                signal_type=SignalType.SELL,
                                confidence=0.6,
                                reason=f"Take profit (+{pnl_pct*100:.1f}%), fell out of selection",
                                target_weight=0.0,
                                timestamp=current_date,
                            ))

        return signals

    @staticmethod
    def _calc_rsi(prices: pd.Series, period: int = 14) -> float:
        from strategy.timing._rsi import calc_rsi
        return calc_rsi(prices, period)
