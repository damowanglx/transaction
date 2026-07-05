#!/usr/bin/env python
"""Multi-sample validation: 5 runs × 1000 stocks, median + CI.
More statistically robust than a single full-universe run.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datetime import date, timedelta
import numpy as np
import logging

from config.settings import setup_logging
setup_logging()
logging.getLogger("backtest.engine").setLevel(logging.WARNING)
logging.getLogger("risk.circuit_breaker").setLevel(logging.WARNING)
logger = logging.getLogger("validate")

from backtest.engine import BacktestEngine
from strategy.timing.mean_revert import MeanRevertStrategy
from data.storage.clickhouse_client import get_clickhouse_client


def run_sample(seed: int, n: int = 1000):
    ch = get_clickhouse_client()
    r = ch.client.query("SELECT max(trade_date) FROM daily_bars")
    db_latest = r.first_row[0]
    if isinstance(db_latest, str):
        from datetime import datetime
        db_latest = datetime.strptime(db_latest, "%Y-%m-%d").date()
    end = db_latest
    start = end - timedelta(days=730)

    codes = ch.get_all_codes_on_date(end)
    codes = [c for c in codes if c != '000300.SH']
    import random
    rng = random.Random(seed)
    codes = rng.sample(codes, min(len(codes), n))

    df = ch.client.query_df(
        "SELECT ts_code, trade_date, open, high, low, close, vol, amount, turnover_rate "
        "FROM daily_bars WHERE ts_code IN %(codes)s "
        "AND trade_date >= %(start)s AND trade_date <= %(end)s ORDER BY ts_code, trade_date",
        parameters={"codes": tuple(codes), "start": start.isoformat(), "end": end.isoformat()},
    )

    strat = MeanRevertStrategy(f"val_{seed}")
    strat.init(bb_period=23, bb_std=3.0, rsi_oversold=26, rsi_overbought=65,
               stop_loss=0.05, take_profit=0.10, top_n=10,
               min_price=5.0, min_turnover=1.0)

    engine = BacktestEngine(initial_cash=200_000)
    result = engine.run(strat, df, start, end)
    return result


def main():
    seeds = [42, 123, 456, 789, 1024]
    results = []

    for i, seed in enumerate(seeds):
        logger.info("Run %d/5 (seed=%d)...", i + 1, seed)
        r = run_sample(seed)
        results.append(r)
        print(f"  Seed {seed}: Return={r.total_return*100:+.1f}% Sharpe={r.sharpe_ratio:.2f} MaxDD={r.max_drawdown*100:.1f}% Trades={r.total_trades}")

    # Compute statistics
    returns = [r.total_return for r in results]
    sharpes = [r.sharpe_ratio for r in results]
    maxdds = [r.max_drawdown for r in results]
    wins = [r.win_rate for r in results]
    pfs = [r.profit_factor for r in results]

    print("\n" + "=" * 70)
    print("  MULTI-SAMPLE VALIDATION (5 runs × 1000 stocks)")
    print("=" * 70)
    print(f"  {'Metric':<16} {'Median':<10} {'Min':<10} {'Max':<10} {'Range':<10}")
    print("-" * 70)
    print(f"  {'Return':<16} {np.median(returns)*100:+7.2f}%  {min(returns)*100:+7.2f}%  {max(returns)*100:+7.2f}%  {(max(returns)-min(returns))*100:+.1f}%")
    print(f"  {'Sharpe':<16} {np.median(sharpes):+7.2f}    {min(sharpes):+7.2f}    {max(sharpes):+7.2f}    {max(sharpes)-min(sharpes):+.2f}")
    print(f"  {'Max DD':<16} {np.median(maxdds)*100:+7.2f}%  {min(maxdds)*100:+7.2f}%  {max(maxdds)*100:+7.2f}%  {(max(maxdds)-min(maxdds))*100:+.1f}%")
    print(f"  {'Win Rate':<16} {np.median(wins)*100:+7.1f}%  {min(wins)*100:+7.1f}%  {max(wins)*100:+7.1f}%  {(max(wins)-min(wins))*100:+.1f}%")
    print(f"  {'Profit Factor':<16} {np.median(pfs):+.2f}      {min(pfs):+.2f}      {max(pfs):+.2f}      {max(pfs)-min(pfs):+.2f}")
    print("-" * 70)

    mean_ret = np.mean(returns)
    std_ret = np.std(returns, ddof=1)
    # t-distribution for n=5 samples (df=4): t_crit = 2.776 at 95%
    from scipy import stats as scipy_stats
    t_crit = scipy_stats.t.ppf(0.975, df=len(returns) - 1)
    ci_95 = t_crit * std_ret / np.sqrt(len(returns))
    print(f"\n  95% CI for Return (t-dist, df={len(returns)-1}): {mean_ret*100:+.1f}% ± {ci_95*100:.1f}%")
    print(f"  Strategy is profitable at 95% confidence: {'YES' if mean_ret - ci_95 > 0 else 'NO'}")

    if all(r > 0 for r in returns):
        print(f"  ALL 5 runs positive ✅")
    else:
        print(f"  Positive runs: {sum(1 for r in returns if r > 0)}/5")


if __name__ == "__main__":
    main()
