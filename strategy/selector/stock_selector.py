"""
Multi-factor stock selector with IC analysis and scoring.

Workflow:
1. Compute all factor values for the universe
2. IC analysis: measure each factor's predictive power
3. Score stocks: combine factors (equal-weight or IC-weighted)
4. Rank and select top N stocks
5. Generate buy/sell/hold signals based on position changes

IC (Information Coefficient) = rank correlation between factor values
    at time t and forward returns at time t+k.
    - |IC| > 0.05: Strong predictive power
    - |IC| > 0.02: Moderate (minimum usable)
    - |IC| < 0.02: Weak (noise)
"""

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

import numpy as np
import pandas as pd
from scipy import stats

from strategy.base.strategy_template import BaseStrategy, Signal, SignalType

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FactorResult:
    """Immutable factor evaluation result."""

    name: str
    ic_mean: float
    ic_std: float
    ic_ir: float           # IC / IC_std (information ratio of the factor)
    rank_ic_mean: float
    rank_ic_std: float
    positive_rate: float    # Fraction of periods with positive IC
    effective: bool         # Whether factor passes minimum thresholds


@dataclass(frozen=True)
class SelectionResult:
    """Immutable stock selection output."""

    date: date
    ranked_stocks: list[tuple[str, float]]  # [(ts_code, score), ...] descending
    factor_weights: dict[str, float]         # Factor name → weight
    signals: list[Signal]


class StockSelector(BaseStrategy):
    """Multi-factor stock selector with IC-weighted scoring.

    Usage:
        selector = StockSelector("momentum_quality")
        selector.init(
            factors=["mom_60", "vol_20", "avg_turn_20", "tech_bb_position"],
            top_n=20,
            lookback_days=60,
        )
        signals = selector.on_data(data, current_date)
    """

    def __init__(self, name: str = "stock_selector"):
        super().__init__(name)
        self._factor_names: list[str] = []
        self._top_n: int = 20
        self._lookback_days: int = 60
        self._ic_weights: dict[str, float] = {}
        self._current_positions: set[str] = set()
        self._min_ic: float = 0.02
        self._auto_compute_factors: bool = False

    def with_auto_factors(self, enabled: bool = True) -> "StockSelector":
        """Enable automatic factor computation from raw OHLCV data.

        When enabled, on_data() will call compute_all_factors() on the
        incoming data before running selection logic.
        """
        self._auto_compute_factors = enabled
        return self

    def init(self, **params) -> None:
        """Initialize selector parameters.

        Args:
            factors: List of factor column names to use.
            top_n: Number of stocks to select.
            lookback_days: Days of history for IC calculation.
            min_ic: Minimum |IC| for a factor to be included.
            current_positions: Set of currently held ts_codes.
        """
        self._factor_names = params.get("factors", [])
        self._top_n = params.get("top_n", 20)
        self._lookback_days = params.get("lookback_days", 60)
        self._min_ic = params.get("min_ic", 0.02)
        self._current_positions = set(params.get("current_positions", []))
        self._ic_weights = params.get("ic_weights", {})
        super().init(**params)

    def on_data(self, data: pd.DataFrame, current_date: date) -> list[Signal]:
        """Run factor scoring and generate signals for a trading date.

        Args:
            data: DataFrame with factor columns. Must contain:
                  ts_code, trade_date, forward_return_5d (for IC),
                  and all factor columns specified in init().
            current_date: The trading date.

        Returns:
            List of buy/sell signals.
        """
        if not self._factor_names:
            logger.warning("No factors configured — returning empty signals")
            return []

        if data.empty or "trade_date" not in data.columns:
            logger.warning("No data or missing trade_date column — returning empty signals")
            return []

        # Auto-compute factors from raw OHLCV if enabled
        if self._auto_compute_factors:
            from strategy.factors import compute_all_factors
            data = compute_all_factors(data)

        today_data = data[data["trade_date"] == pd.Timestamp(current_date)].copy()
        if today_data.empty:
            logger.warning("No data for date %s", current_date)
            return []

        # 1. Calculate IC weights (if not provided)
        if not self._ic_weights:
            self._ic_weights = self._calc_ic_weights(data)

        # 2. Score stocks
        scores = self._score_stocks(today_data)

        # 3. Rank and select
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        selected_codes = {code for code, _ in ranked[:self._top_n]}

        # 4. Generate signals
        signals = self._generate_signals(
            ranked, selected_codes, current_date
        )

        return signals

    def _calc_ic_weights(self, data: pd.DataFrame) -> dict[str, float]:
        """Calculate factor weights based on rolling IC.

        Uses rank IC (Spearman) as it's more robust to outliers than Pearson IC.
        """
        weights: dict[str, float] = {}
        forward_col = "forward_return_5d"

        if forward_col not in data.columns:
            logger.warning("No forward_return_5d column — using equal weights")
            n = len(self._factor_names)
            return {f: 1.0 / n for f in self._factor_names} if n > 0 else {}

        recent = data[data["trade_date"] >= data["trade_date"].max() - pd.Timedelta(days=self._lookback_days)]

        for factor in self._factor_names:
            if factor not in recent.columns:
                weights[factor] = 0.0
                continue

            valid = recent[[factor, forward_col]].dropna()
            if len(valid) < 30:
                weights[factor] = 0.0
                continue

            ic, _ = stats.spearmanr(valid[factor], valid[forward_col])

            if np.isnan(ic):
                weights[factor] = 0.0
            elif abs(ic) < self._min_ic:
                weights[factor] = 0.0
            else:
                weights[factor] = abs(ic)

        # Normalize
        total = sum(weights.values())
        if total > 0:
            weights = {k: v / total for k, v in weights.items()}
        else:
            # Fallback: equal weights for all
            n = len(self._factor_names)
            weights = {f: 1.0 / n for f in self._factor_names}

        logger.info("IC weights (latest): %s", weights)
        return weights

    def _score_stocks(self, data: pd.DataFrame) -> dict[str, float]:
        """Score each stock as weighted sum of factor z-scores.

        Vectorized — O(n_cols * m_factors) instead of O(n_rows * n_factors).
        """
        # Fallback: equal weights if no IC weights computed yet
        weights = self._ic_weights if self._ic_weights else {
            f: 1.0 / len(self._factor_names) for f in self._factor_names
        }

        # Only use factors that exist in the data
        available_factors = [f for f in self._factor_names if f in data.columns]
        if not available_factors:
            return {code: 0.0 for code in data["ts_code"]}

        # Compute z-scores for all factors in one pass
        z_cols = {}
        for factor in available_factors:
            col = data[factor]
            mean = col.mean()
            std = col.std()
            if std > 0:
                z = ((col - mean) / std).clip(-3.0, 3.0)
            else:
                z = pd.Series(0.0, index=col.index)
            z_cols[factor] = z

        # Weighted sum of z-scores (fully vectorized)
        score_series = pd.Series(0.0, index=data.index)
        for factor in available_factors:
            score_series += weights.get(factor, 0.0) * z_cols[factor]

        # Build result dict
        result: dict[str, float] = {}
        for code, s in zip(data["ts_code"], score_series):
            result[code] = float(s)

        return result

    def _generate_signals(
        self,
        ranked: list[tuple[str, float]],
        selected: set[str],
        current_date: date,
    ) -> list[Signal]:
        """Generate buy/sell signals by comparing to current positions."""
        signals = []

        for code, score in ranked[:self._top_n]:
            if code not in self._current_positions:
                signals.append(Signal(
                    ts_code=code,
                    signal_type=SignalType.BUY,
                    confidence=min(score / 3.0 + 0.5, 1.0) if score > 0 else 0.5,
                    reason=f"Selected #{(self._top_n - (self._top_n - ranked.index((code, score))))}, score={score:.3f}",
                    target_weight=1.0 / self._top_n,
                    timestamp=current_date,
                ))

        for code in self._current_positions - selected:
            signals.append(Signal(
                ts_code=code,
                signal_type=SignalType.SELL,
                confidence=0.8,
                reason="Fell out of top selection",
                target_weight=0.0,
                timestamp=current_date,
            ))

        return signals

    def analyze_ic(
        self,
        data: pd.DataFrame,
        forward_col: str = "forward_return_5d",
    ) -> list[FactorResult]:
        """Perform full IC analysis on all factors.

        Returns a list of FactorResult, one per factor.
        Useful for:
        - Identifying which factors actually work
        - Iterating on factor design
        - Detecting factor decay over time
        """
        results = []
        data = data.dropna(subset=[forward_col])

        for factor in self._factor_names:
            if factor not in data.columns:
                results.append(FactorResult(
                    name=factor, ic_mean=0, ic_std=0, ic_ir=0,
                    rank_ic_mean=0, rank_ic_std=0,
                    positive_rate=0, effective=False,
                ))
                continue

            valid = data[[factor, forward_col]].dropna()
            if len(valid) < 30:
                results.append(FactorResult(
                    name=factor, ic_mean=0, ic_std=0, ic_ir=0,
                    rank_ic_mean=0, rank_ic_std=0,
                    positive_rate=0, effective=False,
                ))
                continue

            # Compute rolling IC by date
            ics = []
            rank_ics = []
            dates = valid.index.unique() if isinstance(valid.index, pd.DatetimeIndex) else []

            for d in data["trade_date"].unique():
                day_data = data[data["trade_date"] == d][[factor, forward_col]].dropna()
                if len(day_data) < 10:
                    continue

                pearson_r, _ = stats.pearsonr(day_data[factor], day_data[forward_col])
                spearman_r, _ = stats.spearmanr(day_data[factor], day_data[forward_col])

                if not np.isnan(pearson_r):
                    ics.append(pearson_r)
                if not np.isnan(spearman_r):
                    rank_ics.append(spearman_r)

            ic_mean = np.mean(ics) if ics else 0.0
            ic_std = np.std(ics) if ics else 0.0
            ic_ir = ic_mean / ic_std if ic_std > 0 else 0.0
            rank_ic_mean = np.mean(rank_ics) if rank_ics else 0.0
            rank_ic_std = np.std(rank_ics) if rank_ics else 0.0
            positive_rate = sum(1 for v in ics if v > 0) / len(ics) if ics else 0.0

            effective = (
                abs(rank_ic_mean) >= self._min_ic
                and positive_rate >= 0.55
            )

            results.append(FactorResult(
                name=factor,
                ic_mean=ic_mean,
                ic_std=ic_std,
                ic_ir=ic_ir,
                rank_ic_mean=rank_ic_mean,
                rank_ic_std=rank_ic_std,
                positive_rate=positive_rate,
                effective=effective,
            ))

        return results


