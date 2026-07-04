#!/usr/bin/env python
"""Quick parameter sweep for mean reversion strategy.

Uses small sample (100 stocks, 1 year) for fast iteration.
Ranks by Sharpe ratio.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging
import itertools
from datetime import date, timedelta

from config.settings import setup_logging
setup_logging(level="WARNING")
logger = logging.getLogger("optimize")

from backtest.engine import BacktestEngine
from strategy.timing.mean_revert import MeanRevertStrategy
from data.storage.clickhouse_client import get_clickhouse_client


def load_data():
    ch = get_clickhouse_client()
    r = ch.client.query("SELECT max(trade_date) FROM daily_bars")
    db_latest = r.first_row[0]
    if isinstance(db_latest, str):
        from datetime import datetime
        db_latest = datetime.strptime(db_latest, "%Y-%m-%d").date()
    end = db_latest
    start = end - timedelta(days=365)  # 1 year for meaningful results

    codes = ch.get_all_codes_on_date(end)
    import random
    random.seed(42)
    codes = random.sample(codes, min(len(codes), 100))

    codes_tuple = tuple(codes)
    df = ch.client.query_df(
        "SELECT ts_code, trade_date, open, high, low, close, vol, amount "
        "FROM daily_bars WHERE ts_code IN %(codes)s "
        "AND trade_date >= %(start)s AND trade_date <= %(end)s "
        "ORDER BY ts_code, trade_date",
        parameters={"codes": codes_tuple, "start": start.isoformat(), "end": end.isoformat()},
    )
    return df, start, end


def run_sweep():
    data, start, end = load_data()
    logger.info("Loaded %d rows, %d stocks", len(data), data["ts_code"].nunique())

    # Parameter grid — small set for fast validation
    param_grid = [
        # (bb_period, bb_std, rsi_oversold, rsi_overbought, stop_loss, top_n, take_profit)
        (20, 2.0, 30, 65, 0.05, 10, 0.15),  # baseline
        (25, 2.5, 25, 70, 0.07, 8, 0.10),   # wider BB, tight RSI, tight SL
        (15, 1.5, 35, 60, 0.03, 12, 0.20),   # narrow BB, loose RSI, loose SL
        (20, 2.0, 30, 70, 0.05, 15, 0.12),   # baseline + more holdings
        (25, 2.0, 25, 65, 0.05, 5, 0.15),    # fewer holdings
    ]

    results = []
    for i, params in enumerate(param_grid):
        bb_p, bb_s, rsi_lo, rsi_hi, sl, tn, tp = params
        logger.info("Test %d/%d: bb=%d/%.1f rsi=%d/%d sl=%.0f%% top=%d tp=%.0f%%",
                     i + 1, len(param_grid), bb_p, bb_s, rsi_lo, rsi_hi, sl * 100, tn, tp * 100)

        strat = MeanRevertStrategy(f"mr_{i}")
        strat.init(
            bb_period=bb_p, bb_std=bb_s,
            rsi_oversold=rsi_lo, rsi_overbought=rsi_hi,
            stop_loss=sl, take_profit=tp, top_n=tn,
        )

        engine = BacktestEngine(initial_cash=200_000)
        try:
            result = engine.run(strat, data, start, end)
        except Exception as e:
            logger.error("Test %d failed: %s", i + 1, e)
            continue

        results.append({
            "bb_period": bb_p, "bb_std": bb_s,
            "rsi_oversold": rsi_lo, "rsi_overbought": rsi_hi,
            "stop_loss": sl, "take_profit": tp, "top_n": tn,
            "total_return": result.total_return,
            "annual_return": result.annual_return,
            "sharpe": result.sharpe_ratio,
            "max_dd": result.max_drawdown,
            "win_rate": result.win_rate,
            "trades": result.total_trades,
            "profit_factor": result.profit_factor,
        })

    # Sort by Sharpe
    results.sort(key=lambda x: x["sharpe"], reverse=True)

    print("\n" + "=" * 120)
    print("  PARAMETER OPTIMIZATION RESULTS (100 stocks, 1 year) — ranked by Sharpe")
    print("=" * 120)
    print(f"{'Rank':<5} {'BB':<8} {'RSI':<12} {'SL':<6} {'TopN':<5} {'Return':<10} {'Sharpe':<8} {'MaxDD':<8} {'Win%':<7} {'PF':<6} {'Trades':<7}")
    header = f"{'Rank':<5} {'BB':<8} {'RSI':<12} {'SL':<6} {'TopN':<5} {'Return':<10} {'Sharpe':<8} {'MaxDD':<8} {'Win%':<7} {'PF':<6} {'Trades':<7}"
    try:
        print(header)
    except UnicodeEncodeError:
        print("Rank  BB       RSI          SL    TopN  Return     Sharpe   MaxDD    Win%    PF     Trades")
    print("-" * 120)

    for rank, r in enumerate(results, 1):
        bb = f"{r['bb_period']}/{r['bb_std']}"
        rsi = f"{r['rsi_oversold']}/{r['rsi_overbought']}"
        print(f"{rank:<5} {bb:<8} {rsi:<12} {r['stop_loss']*100:.0f}%{'':<3} {r['top_n']:<5} "
              f"{r['total_return']*100:+7.2f}%  {r['sharpe']:+6.2f}  {r['max_dd']*100:+6.2f}%  "
              f"{r['win_rate']*100:5.1f}%  {r['profit_factor']:.2f}  {r['trades']:<7}")

    print("-" * 120)
    best = results[0]
    print(f"\n  BEST: bb={best['bb_period']}/{best['bb_std']} rsi={best['rsi_oversold']}/{best['rsi_overbought']} "
          f"sl={best['stop_loss']*100:.0f}% top_n={best['top_n']} "
          f"→ Sharpe={best['sharpe']:.2f} Return={best['total_return']*100:+.1f}%")

    return results


if __name__ == "__main__":
    run_sweep()
