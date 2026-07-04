#!/usr/bin/env python
"""Parameter optimization engine — grid search + genetic algorithm.

Usage:
    python scripts/optimizer.py grid       # Grid search over defined ranges
    python scripts/optimizer.py genetic    # Genetic algorithm optimization
"""

import sys
import time
import random
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging
import itertools
import random
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable

import numpy as np

from config.settings import setup_logging
setup_logging(level="INFO")
# Suppress breaker noise in optimizer
logging.getLogger("backtest.engine").setLevel(logging.ERROR)
logging.getLogger("risk.circuit_breaker").setLevel(logging.ERROR)
logger = logging.getLogger("optimizer")

from backtest.engine import BacktestEngine
from strategy.timing.mean_revert import MeanRevertStrategy
from data.storage.clickhouse_client import get_clickhouse_client


@dataclass
class ParamRange:
    """Parameter range for optimization."""
    name: str
    min_val: float
    max_val: float
    step: float = 1.0
    is_int: bool = False

    def values(self, n_grid: int = 5) -> list:
        """Generate N evenly-spaced values in range."""
        vals = np.linspace(self.min_val, self.max_val, n_grid)
        if self.is_int:
            return [int(v) for v in vals]
        return [round(v, 3) for v in vals]


@dataclass
class TrialResult:
    """Single optimization trial result."""
    params: dict
    sharpe: float
    total_return: float
    max_drawdown: float
    win_rate: float
    profit_factor: float
    trades: int

    @property
    def score(self) -> float:
        """Composite score: Sharpe-weighted with drawdown penalty."""
        return self.sharpe * 0.5 + self.total_return * 0.3 - abs(self.max_drawdown) * 0.2


def load_data(n_stocks: int = 50):
    """Load small sample for fast optimization."""
    ch = get_clickhouse_client()
    r = ch.client.query("SELECT max(trade_date) FROM daily_bars")
    db_latest = r.first_row[0]
    if isinstance(db_latest, str):
        from datetime import datetime
        db_latest = datetime.strptime(db_latest, "%Y-%m-%d").date()
    end = db_latest
    start = end - timedelta(days=365)
    codes = ch.get_all_codes_on_date(end)
    # Use time-based seed for diversity across runs. Pass --seed=42 for reproducibility.
    random.seed(int(time.time() * 1000) % (2**31))
    codes = random.sample(codes, min(len(codes), n_stocks))
    df = ch.client.query_df(
        "SELECT ts_code, trade_date, open, high, low, close, vol, amount, turnover_rate "
        "FROM daily_bars WHERE ts_code IN %(codes)s "
        "AND trade_date >= %(start)s AND trade_date <= %(end)s "
        "ORDER BY ts_code, trade_date",
        parameters={"codes": tuple(codes), "start": start.isoformat(), "end": end.isoformat()},
    )
    return df, start, end


def evaluate(params: dict, data, start, end, engine) -> TrialResult:
    """Run a single backtest and return metrics."""
    strat = MeanRevertStrategy("opt")
    strat.init(**params)
    result = engine.run(strat, data, start, end)
    return TrialResult(
        params=params,
        sharpe=result.sharpe_ratio,
        total_return=result.total_return,
        max_drawdown=result.max_drawdown,
        win_rate=result.win_rate,
        profit_factor=result.profit_factor,
        trades=result.total_trades,
    )


def grid_search(
    param_ranges: list[ParamRange],
    n_grid: int = 5,
    n_stocks: int = 50,
) -> list[TrialResult]:
    """Exhaustive grid search over parameter space.

    Args:
        param_ranges: List of parameter ranges to sweep.
        n_grid: Number of points per parameter dimension.
        n_stocks: Sample size for speed.

    Returns:
        List of TrialResult, sorted by score descending.
    """
    data, start, end = load_data(n_stocks)
    engine = BacktestEngine(initial_cash=200_000)

    # Generate all parameter combinations
    value_lists = [pr.values(n_grid) for pr in param_ranges]
    total = 1
    for vl in value_lists:
        total *= len(vl)

    results = []
    count = 0
    for combo in itertools.product(*value_lists):
        params = {pr.name: v for pr, v in zip(param_ranges, combo)}
        # Add fixed params
        params.update({
            "min_price": 5.0, "min_turnover": 1.0,
            "take_profit": 0.10,
        })
        count += 1
        print(f"  [{count}/{total}] {params}", end="\r")
        try:
            trial = evaluate(params, data, start, end, engine)
            results.append(trial)
        except Exception as e:
            logger.warning("Trial failed: %s — %s", params, e)

    results.sort(key=lambda r: r.score, reverse=True)
    return results


def genetic_optimize(
    param_ranges: list[ParamRange],
    population_size: int = 12,
    generations: int = 5,
    n_stocks: int = 50,
) -> list[TrialResult]:
    """Genetic algorithm for parameter optimization.

    Args:
        param_ranges: Parameter ranges to optimize.
        population_size: Individuals per generation.
        generations: Number of generations.
        n_stocks: Sample size for speed.

    Returns:
        All evaluated TrialResults, sorted by score.
    """
    data, start, end = load_data(n_stocks)
    engine = BacktestEngine(initial_cash=200_000)
    all_results: list[TrialResult] = []

    # Initialize random population
    population = []
    for _ in range(population_size):
        params = {}
        for pr in param_ranges:
            if pr.is_int:
                params[pr.name] = random.randint(int(pr.min_val), int(pr.max_val))
            else:
                params[pr.name] = round(random.uniform(pr.min_val, pr.max_val), 3)
        params.update({
            "min_price": 5.0, "min_turnover": 1.0,
            "take_profit": 0.10,
        })
        population.append(params)

    for gen in range(generations):
        print(f"\n  Generation {gen + 1}/{generations}")

        # Evaluate
        gen_results = []
        for i, params in enumerate(population):
            print(f"    [{i+1}/{len(population)}] {params}", end="\r")
            try:
                trial = evaluate(params, data, start, end, engine)
                gen_results.append(trial)
            except Exception as e:
                logger.warning("Failed: %s", e)

        gen_results.sort(key=lambda r: r.score, reverse=True)
        all_results.extend(gen_results)

        if gen == generations - 1:
            break

        # Select top half
        elite = gen_results[:population_size // 2]
        new_population = [r.params for r in elite]

        # Crossover + mutation
        while len(new_population) < population_size:
            parent1 = random.choice(elite).params
            parent2 = random.choice(elite).params
            child = {}
            for pr in param_ranges:
                # 50% from each parent + mutation
                if random.random() < 0.5:
                    val = parent1[pr.name]
                else:
                    val = parent2[pr.name]
                # Mutate: ±10% with 20% probability
                if random.random() < 0.2:
                    delta = val * random.uniform(-0.1, 0.1)
                    val = val + delta
                    val = max(pr.min_val, min(pr.max_val, val))
                child[pr.name] = round(val, 3) if not pr.is_int else int(round(val))
            child.update({
                "min_price": 5.0, "min_turnover": 1.0,
                "rsi_overbought": 65, "top_n": 10, "stop_loss": 0.05,
            })
            new_population.append(child)

        population = new_population

    all_results.sort(key=lambda r: r.score, reverse=True)
    return all_results


def print_best(results: list[TrialResult], top_n: int = 5):
    """Print top N results."""
    print("\n" + "=" * 100)
    print(f"  TOP {top_n} RESULTS (by composite score)")
    print("=" * 100)
    print(f"{'Rank':<5} {'Sharpe':<8} {'Return':<10} {'MaxDD':<8} {'Win%':<7} {'PF':<6} {'Trades':<7} {'Params'}")
    print("-" * 100)
    for i, r in enumerate(results[:top_n], 1):
        print(f"{i:<5} {r.sharpe:+6.2f}  {r.total_return*100:+7.2f}%  {r.max_drawdown*100:+6.2f}%  "
              f"{r.win_rate*100:5.1f}%  {r.profit_factor:.2f}  {r.trades:<7} {r.params}")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "grid"

    # Round 2: optimize ALL key parameters
    ranges = [
        ParamRange("bb_period", 20, 30, is_int=True),        # 4 values
        ParamRange("bb_std", 2.5, 3.5, step=0.3),            # 4 values
        ParamRange("rsi_oversold", 22, 32, is_int=True),     # 4 values
        ParamRange("rsi_overbought", 60, 75, is_int=True),   # 4 values
        ParamRange("stop_loss", 0.03, 0.08, step=0.02),      # 3 values
        ParamRange("top_n", 5, 15, is_int=True),              # 4 values
    ]
    # Total: 4×4×4×4×3×4 = 3072 combos — too many.
    # Use genetic instead of grid for this many parameters.

    if mode == "grid":
        print("Running grid search...")
        results = grid_search(ranges, n_grid=4, n_stocks=50)
    elif mode == "genetic":
        print("Running genetic optimization...")
        results = genetic_optimize(ranges, population_size=12, generations=4, n_stocks=50)
    else:
        print(f"Unknown mode: {mode}")
        sys.exit(1)

    print_best(results)
