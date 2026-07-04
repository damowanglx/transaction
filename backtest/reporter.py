"""
Backtest report generator.

Produces human-readable reports and summary statistics
from BacktestResult objects. Supports terminal output,
JSON export, and CSV trade log export.
"""

import json
import logging
from datetime import date

from backtest.engine import BacktestResult

logger = logging.getLogger(__name__)


def format_report(result: BacktestResult, strategy_name: str = "") -> str:
    """Format a backtest result as a readable terminal report.

    Args:
        result: BacktestResult from engine.run().
        strategy_name: Optional strategy name for the header.

    Returns:
        Multi-line string suitable for printing.
    """
    lines = []
    width = 60

    if strategy_name:
        lines.append("=" * width)
        lines.append(f"  BACKTEST REPORT — {strategy_name}")
        lines.append("=" * width)
    else:
        lines.append("=" * width)
        lines.append("  BACKTEST REPORT")
        lines.append("=" * width)

    lines.append("")

    # Period
    if result.daily_records:
        start = result.daily_records[0].trade_date
        end = result.daily_records[-1].trade_date
        n_days = len(result.daily_records)
        lines.append(f"  Period:          {start} → {end} ({n_days} trading days)")
    lines.append("")

    # Returns
    lines.append("  —— Returns ——")
    lines.append(f"  Initial Capital:  ¥{result.initial_cash:,.0f}")
    lines.append(f"  Final Value:      ¥{result.final_value:,.0f}")
    lines.append(f"  Total Return:     {result.return_pct:+.2f}%")
    lines.append(f"  Annual Return:    {result.annual_return_pct:+.2f}%")
    lines.append("")

    # Risk
    lines.append("  —— Risk ——")
    lines.append(f"  Sharpe Ratio:     {result.sharpe_ratio:.2f}")
    lines.append(f"  Max Drawdown:     {result.max_drawdown_pct:.2f}%")
    lines.append(f"  Max DD Duration:  {result.max_drawdown_duration} days")

    # Benchmark
    if result.benchmark_return != 0:
        lines.append("")
        lines.append("  —— Benchmark ——")
        lines.append(f"  Benchmark Return: {result.benchmark_return_pct:+.2f}%")
        lines.append(f"  Alpha:            {result.alpha_pct:+.2f}%")
    lines.append("")

    # Trades
    lines.append("  —— Trades ——")
    lines.append(f"  Total Trades:     {result.total_trades}")
    lines.append(f"  Win Rate:         {result.win_rate*100:.1f}%")
    lines.append(f"  Avg Win:          {result.avg_win_pct*100:+.2f}%")
    lines.append(f"  Avg Loss:         {result.avg_loss_pct*100:+.2f}%")
    lines.append(f"  Profit Factor:    {result.profit_factor:.2f}")
    lines.append("")

    # Signal quality
    if result.signals_generated > 0:
        lines.append("  —— Signal Quality ——")
        lines.append(f"  Signals Generated: {result.signals_generated}")
        lines.append(f"  Signals Rejected:  {result.signals_rejected} ({result.signals_rejected/max(result.signals_generated,1)*100:.0f}%)")
        lines.append(f"  Signals Executed:  {result.signals_executed} ({result.signals_executed/max(result.signals_generated,1)*100:.0f}%)")
    lines.append("")

    # Verdict
    verdict, color = _verdict(result)
    lines.append(f"  Verdict: {verdict}")
    lines.append("")
    lines.append("=" * width)

    return "\n".join(lines)


def _verdict(result: BacktestResult) -> tuple[str, str]:
    """Generate a qualitative verdict."""
    score = 0

    if result.sharpe_ratio > 1.5:
        score += 3
    elif result.sharpe_ratio > 1.0:
        score += 2
    elif result.sharpe_ratio > 0.5:
        score += 1
    else:
        score -= 1

    if result.max_drawdown > -0.15:
        score += 2
    elif result.max_drawdown > -0.25:
        score += 1
    else:
        score -= 1

    if result.win_rate > 0.5:
        score += 1
    if result.profit_factor > 1.5:
        score += 2
    elif result.profit_factor > 1.0:
        score += 1

    if result.total_trades < 30:
        score -= 1  # Too few trades — probably overfitted

    if score >= 6:
        return "STRONG — Ready for paper trading", "green"
    elif score >= 3:
        return "PASS — Needs more validation", "yellow"
    elif score >= 1:
        return "WEAK — Keep iterating", "orange"
    else:
        return "FAIL — Do not trade", "red"


def export_json(result: BacktestResult, filepath: str) -> None:
    """Export backtest result metrics as JSON (no daily records for file size)."""
    output = {
        "initial_cash": result.initial_cash,
        "final_value": result.final_value,
        "total_return": result.total_return,
        "annual_return": result.annual_return,
        "sharpe_ratio": result.sharpe_ratio,
        "max_drawdown": result.max_drawdown,
        "max_drawdown_duration": result.max_drawdown_duration,
        "total_trades": result.total_trades,
        "win_rate": result.win_rate,
        "avg_win_pct": result.avg_win_pct,
        "avg_loss_pct": result.avg_loss_pct,
        "profit_factor": result.profit_factor,
        "n_days": len(result.daily_records),
        "start_date": str(result.daily_records[0].trade_date) if result.daily_records else None,
        "end_date": str(result.daily_records[-1].trade_date) if result.daily_records else None,
    }

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    logger.info("Exported backtest report to %s", filepath)


def export_trade_csv(result: BacktestResult, filepath: str) -> None:
    """Export trade history as CSV for further analysis."""
    import csv

    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["order_id", "ts_code", "direction", "price", "volume",
                          "amount", "commission", "stamp_tax", "trade_date"])

        for trade in result.trade_history:
            writer.writerow([
                trade.order_id,
                trade.ts_code,
                trade.direction,
                trade.price,
                trade.volume,
                trade.amount,
                trade.commission,
                trade.stamp_tax,
                trade.trade_date.isoformat(),
            ])

    logger.info("Exported %d trades to %s", len(result.trade_history), filepath)
