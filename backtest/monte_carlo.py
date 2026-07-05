"""Monte Carlo stress test — assess real risk beyond single-path backtest.

Bootstraps daily returns to simulate thousands of alternate histories,
revealing tail risks hidden in the single observed path.
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass


@dataclass(frozen=True)
class MCResult:
    """Monte Carlo simulation results."""
    median_return: float       # Median final return
    mean_return: float         # Mean final return
    worst_5pct_return: float   # 5th percentile (95% confidence worst case)
    worst_1pct_return: float   # 1st percentile
    best_5pct_return: float    # 95th percentile
    median_max_dd: float       # Median max drawdown
    worst_5pct_max_dd: float   # 95% confidence worst drawdown
    prob_loss: float           # Probability of negative return
    prob_dd_gt_30: float       # Probability of >30% drawdown
    prob_dd_gt_50: float       # Probability of >50% drawdown


def monte_carlo_simulate(
    daily_returns: pd.Series,
    initial_capital: float = 200_000,
    n_simulations: int = 2000,
    horizon_days: int = 244,  # 1 year
    seed: int = 42,
) -> MCResult:
    """Run Monte Carlo simulation on daily returns.

    Uses block bootstrap to preserve some autocorrelation structure.

    Args:
        daily_returns: Series of daily returns (as decimals, e.g. 0.01 = 1%).
        initial_capital: Starting portfolio value.
        n_simulations: Number of simulated paths.
        horizon_days: Investment horizon in trading days.
        seed: Random seed for reproducibility.

    Returns:
        MCResult with distribution statistics.
    """
    rng = np.random.default_rng(seed)
    returns = daily_returns.dropna().values
    if len(returns) < 50:
        return MCResult(0, 0, 0, 0, 0, 0, 0, 0, 0, 0)

    # Block bootstrap: sample blocks of 5 days to preserve autocorrelation
    block_size = 5
    n_blocks = horizon_days // block_size + 1

    final_values = []
    max_drawdowns = []
    sharpes = []

    for _ in range(n_simulations):
        # Bootstrap blocks
        sim_returns = []
        for __ in range(n_blocks):
            start = rng.integers(0, len(returns) - block_size)
            sim_returns.extend(returns[start:start + block_size])
        sim_returns = np.array(sim_returns[:horizon_days])

        # Compute equity curve
        equity = initial_capital * np.cumprod(1 + sim_returns)
        rolling_max = np.maximum.accumulate(equity)
        drawdowns = (equity - rolling_max) / rolling_max

        final_values.append(equity[-1])
        max_drawdowns.append(drawdowns.min())

        mu = np.mean(sim_returns)
        sigma = np.std(sim_returns)
        sharpes.append(mu / sigma * np.sqrt(244) if sigma > 0 else 0)

    final_values = np.array(final_values)
    max_drawdowns = np.array(max_drawdowns)

    return MCResult(
        median_return=(np.median(final_values) / initial_capital - 1),
        mean_return=(np.mean(final_values) / initial_capital - 1),
        worst_5pct_return=(np.percentile(final_values, 5) / initial_capital - 1),
        worst_1pct_return=(np.percentile(final_values, 1) / initial_capital - 1),
        best_5pct_return=(np.percentile(final_values, 95) / initial_capital - 1),
        median_max_dd=np.median(max_drawdowns),
        worst_5pct_max_dd=np.percentile(max_drawdowns, 5),
        prob_loss=np.mean(final_values < initial_capital),
        prob_dd_gt_30=np.mean(max_drawdowns < -0.30),
        prob_dd_gt_50=np.mean(max_drawdowns < -0.50),
    )


def print_mc_report(result: MCResult) -> str:
    """Format Monte Carlo results as readable report."""
    lines = [
        "=" * 60,
        "  MONTE CARLO STRESS TEST (2000 simulations, 1 year)",
        "=" * 60,
        "",
        "  —— Returns ——",
        f"  Median Return:       {result.median_return*100:+.2f}%",
        f"  Mean Return:         {result.mean_return*100:+.2f}%",
        f"  Worst 5% Case:       {result.worst_5pct_return*100:+.2f}%",
        f"  Worst 1% Case:       {result.worst_1pct_return*100:+.2f}%",
        f"  Best 5% Case:        {result.best_5pct_return*100:+.2f}%",
        "",
        "  —— Risk ——",
        f"  Median Max DD:       {result.median_max_dd*100:.1f}%",
        f"  Worst 5% Max DD:     {result.worst_5pct_max_dd*100:.1f}%",
        f"  Prob(Loss):           {result.prob_loss*100:.0f}%",
        f"  Prob(DD > 30%):       {result.prob_dd_gt_30*100:.0f}%",
        f"  Prob(DD > 50%):       {result.prob_dd_gt_50*100:.0f}%",
        "",
        "=" * 60,
    ]
    return "\n".join(lines)


def run_from_backtest_result(result_json_path: str):
    """Load backtest result JSON and run Monte Carlo on it."""
    import json
    from backtest.engine import BacktestResult, DailyRecord

    with open(result_json_path) as f:
        data = json.load(f)

    # We need daily returns — try loading from the full result
    # Fallback: simulate from reported stats
    daily_return_mean = data["total_return"] / data["n_days"]
    daily_return_std = abs(data["max_drawdown"]) / (2.5 * np.sqrt(data["n_days"]))

    print(f"Using estimated daily stats: mean={daily_return_mean*100:.3f}% std={daily_return_std*100:.2f}%")

    rng = np.random.default_rng(42)
    synthetic_returns = pd.Series(rng.normal(daily_return_mean, daily_return_std, data["n_days"]))

    result = monte_carlo_simulate(synthetic_returns, data["initial_cash"])
    print(print_mc_report(result))
