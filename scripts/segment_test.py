#!/usr/bin/env python
"""Segment backtest — verify strategy robustness across different market regimes.

Runs the same strategy on 6-month rolling windows and compares results.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging
from datetime import date, timedelta

import pandas as pd

from config.settings import setup_logging
setup_logging()
logging.getLogger("backtest.engine").setLevel(logging.WARNING)
logging.getLogger("risk.circuit_breaker").setLevel(logging.WARNING)
logger = logging.getLogger("segment_test")

from backtest.engine import BacktestEngine
from strategy.timing.mean_revert import MeanRevertStrategy
from data.storage.clickhouse_client import get_clickhouse_client


def load_segment(start: date, end: date, n_stocks: int = 500):
    """Load data for a specific time segment."""
    ch = get_clickhouse_client()
    codes = ch.get_all_codes_on_date(min(end, date.today()))
    import random
    random.seed(42)
    codes = random.sample(codes, min(len(codes), n_stocks))
    df = ch.client.query_df(
        "SELECT ts_code, trade_date, open, high, low, close, vol, amount, turnover_rate "
        "FROM daily_bars WHERE ts_code IN %(codes)s "
        "AND trade_date >= %(start)s AND trade_date <= %(end)s "
        "ORDER BY ts_code, trade_date",
        parameters={"codes": tuple(codes), "start": start.isoformat(), "end": end.isoformat()},
    )
    return df


def run_segment(name: str, start: date, end: date):
    """Run backtest on one segment."""
    df = load_segment(start, end)
    if df.empty:
        return None

    strat = MeanRevertStrategy(f"seg_{name}")
    strat.init(bb_period=23, bb_std=3.0, rsi_oversold=26, rsi_overbought=65,
               stop_loss=0.05, take_profit=0.10, top_n=10,
               min_price=5.0, min_turnover=1.0)

    engine = BacktestEngine(initial_cash=200_000)
    result = engine.run(strat, df, start, end)
    return result


def main():
    segments = [
        ("2023-H2 反弹", date(2023, 6, 29), date(2023, 12, 29)),
        ("2024-H1 震荡", date(2024, 1, 2), date(2024, 6, 28)),
        ("2024-H2 牛市", date(2024, 7, 1), date(2024, 12, 31)),
        ("2025-H1 整理", date(2025, 1, 2), date(2025, 6, 30)),
        ("2025-H2 调整", date(2025, 7, 1), date(2025, 12, 31)),
        ("2026-H1 当前", date(2026, 1, 2), date(2026, 6, 26)),
    ]

    results = []
    for name, start, end in segments:
        logger.info("Testing: %s (%s to %s)", name, start, end)
        result = run_segment(name, start, end)
        if result:
            results.append({
                "segment": name,
                "return": result.total_return * 100,
                "sharpe": result.sharpe_ratio,
                "max_dd": result.max_drawdown * 100,
                "win_rate": result.win_rate * 100,
                "trades": result.total_trades,
                "pf": result.profit_factor,
            })

    # Print summary
    print("\n" + "=" * 100)
    print("  SEGMENT BACKTEST — Strategy Robustness Check")
    print("  Params: bb=23/3.0, rsi=26/65, sl=5%, tp=10%, top_n=10")
    print("=" * 100)
    print(f"{'Segment':<16} {'Return':<10} {'Sharpe':<8} {'MaxDD':<8} {'Win%':<7} {'PF':<6} {'Trades'}")
    print("-" * 100)

    for r in results:
        print(f"{r['segment']:<16} {r['return']:+7.2f}%  {r['sharpe']:+6.2f}  {r['max_dd']:+6.2f}%  "
              f"{r['win_rate']:5.1f}%  {r['pf']:.2f}  {r['trades']}")

    print("-" * 100)

    # Consistency check
    if results:
        returns = [r["return"] for r in results]
        positive = sum(1 for r in returns if r > 0)
        print(f"\n  Positive segments: {positive}/{len(results)}")
        print(f"  Return range: {min(returns):+.1f}% to {max(returns):+.1f}%")
        if positive == len(results):
            print("  VERDICT: Strategy profitable in ALL market regimes ✅")
        elif positive >= len(results) * 0.67:
            print("  VERDICT: Strategy profitable in most regimes — acceptable ✅")
        else:
            print("  VERDICT: Strategy inconsistent — needs improvement ⚠️")


if __name__ == "__main__":
    main()
