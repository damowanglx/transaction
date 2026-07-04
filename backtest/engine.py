"""
Event-driven backtest engine for A-share quantitative strategies.

Iterates over historical trading dates and runs:
1. T+1 settlement
2. Market price update
3. Strategy signal generation
4. Order execution
5. Daily P&L recording

Returns a BacktestReport with full trade history and metrics.
"""

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable, Optional

import numpy as np
import pandas as pd

from backtest.broker_sim import BrokerSim, Trade
from config.risk_params import RiskConfig, get_risk_config
from risk.risk_engine import RiskEngine
from strategy.base.strategy_template import BaseStrategy, Signal, SignalType

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DailyRecord:
    """Immutable daily snapshot during backtest."""
    trade_date: date
    cash: float
    market_value: float
    total_value: float
    daily_pnl: float
    daily_return: float
    cumulative_return: float
    trades: list[Trade]


@dataclass(frozen=True)
class BacktestResult:
    """Immutable result from a backtest run."""
    initial_cash: float
    final_value: float
    total_return: float
    annual_return: float
    sharpe_ratio: float
    max_drawdown: float
    max_drawdown_duration: int  # days
    total_trades: int
    win_rate: float
    avg_win_pct: float
    avg_loss_pct: float
    profit_factor: float
    benchmark_return: float = 0.0      # Benchmark total return
    alpha: float = 0.0                 # Excess return over benchmark
    signals_generated: int = 0         # Total signals from strategy
    signals_rejected: int = 0          # Signals blocked by risk
    signals_executed: int = 0          # Signals that became trades
    daily_records: list[DailyRecord] = field(default_factory=list)
    trade_history: list[Trade] = field(default_factory=list)

    @property
    def return_pct(self) -> float:
        return self.total_return * 100.0

    @property
    def annual_return_pct(self) -> float:
        return self.annual_return * 100.0

    @property
    def max_drawdown_pct(self) -> float:
        return self.max_drawdown * 100.0

    @property
    def benchmark_return_pct(self) -> float:
        return self.benchmark_return * 100.0

    @property
    def alpha_pct(self) -> float:
        return self.alpha * 100.0


class BacktestEngine:
    """Event-driven backtest engine.

    Usage:
        engine = BacktestEngine(
            initial_cash=200_000,
            risk_config=get_risk_config("default"),
        )
        result = engine.run(strategy, data, start_date, end_date)
    """

    def __init__(
        self,
        initial_cash: float = 200_000.0,
        risk_config: Optional[RiskConfig] = None,
        commission_rate: float = 0.0003,
        slippage_rate: float = 0.001,
    ):
        self._initial_cash = initial_cash
        self._risk_config = risk_config or get_risk_config("default")
        self._commission_rate = commission_rate
        self._slippage_rate = slippage_rate

    def run(
        self,
        strategy: BaseStrategy,
        data: pd.DataFrame,
        start_date: date,
        end_date: date,
        benchmark_data: pd.DataFrame | None = None,
        progress_callback: Optional[Callable] = None,
    ) -> BacktestResult:
        """Run backtest over a date range.

        Args:
            strategy: Initialized strategy with on_data() method.
            data: DataFrame with columns [ts_code, trade_date, open, high, low,
                  close, vol, amount, ...]. Must be sorted by trade_date.
            start_date: First trading date (inclusive).
            end_date: Last trading date (inclusive).
            benchmark_data: Optional DataFrame with [trade_date, close] for benchmark.
            progress_callback: Optional fn(date, progress_pct) for progress reporting.

        Returns:
            BacktestResult with all metrics and records.
        """
        # Benchmark tracking
        bench_start = 1.0
        bench_end = 1.0
        if benchmark_data is not None and not benchmark_data.empty:
            bench_prices = benchmark_data.set_index("trade_date")["close"]
            bench_start_val = bench_prices.iloc[0] if len(bench_prices) > 0 else 1.0
            bench_end_val = bench_prices.iloc[-1] if len(bench_prices) > 0 else 1.0
            bench_start = bench_start_val
            bench_end = bench_end_val

        risk_engine = RiskEngine(self._risk_config)
        risk_engine.set_initial_capital(self._initial_cash)

        broker = BrokerSim(
            initial_cash=self._initial_cash,
            commission_rate=self._commission_rate,
            slippage_rate=self._slippage_rate,
        )
        daily_records: list[DailyRecord] = []
        daily_pnl_total = 0.0
        prev_total = self._initial_cash
        prev_close: dict[str, float] = {}  # Yesterday's close for limit check
        sig_generated = 0
        sig_rejected = 0
        sig_executed = 0

        if data.empty or "trade_date" not in data.columns:
            logger.warning("No data for backtest — returning empty result")
            return self._compute_metrics([], [], 0.0, 0.0, 0, 0, 0)

        dates = sorted(data["trade_date"].unique())
        dates = [d for d in dates if start_date <= (d.date() if hasattr(d, 'date') else d) <= end_date]

        total_dates = len(dates)
        for i, dt in enumerate(dates):
            if hasattr(dt, 'date'):
                dt = dt.date()

            # 1. T+1 settlement
            broker = broker.settle_t_plus_1(dt)

            # 2. Get data up to and including today (strategy needs history for indicators)
            data_up_to_today = data[data["trade_date"] <= pd.Timestamp(dt)]
            today_data = data[data["trade_date"] == pd.Timestamp(dt)]

            # 3. Update market prices (from today's data only)
            prices = {}
            for _, row in today_data.iterrows():
                prices[row["ts_code"]] = row["close"]
            broker = broker.update_market_prices(prices)

            # 4. Sync positions into strategy
            holdings_dict = {
                code: {
                    "volume": pos.volume,
                    "available_volume": pos.available_volume,
                    "avg_cost": pos.avg_cost,
                    "market_value": pos.market_value,
                    "current_price": pos.current_price,
                }
                for code, pos in broker.positions.items()
            }
            strategy.sync_positions(holdings_dict)

            # 5. Run strategy with full historical data
            signals = strategy.on_data(data_up_to_today, dt)

            # 6. Global risk checks (once per day, before any order)
            all_trades_today: list[Trade] = []
            all_stop_loss_codes: set[str] = set()
            daily_pnl_total = broker.total_value - prev_total
            daily_pnl_pct = daily_pnl_total / prev_total if prev_total > 0 else 0.0

            # Build current positions snapshot for risk checks
            positions_snapshot = {
                code: {
                    "volume": p.volume,
                    "market_value": p.market_value,
                    "avg_cost": p.avg_cost,
                    "current_price": p.current_price,
                }
                for code, p in broker.positions.items()
            }

            # Check if circuit breaker allows trading today
            from risk.circuit_breaker import BreakerState
            breaker_status = risk_engine._circuit_breaker.check(
                capital=broker.total_value,
                pnl=daily_pnl_total,
                pnl_pct=daily_pnl_pct,
                history=[],
                today=dt,
            )

            if breaker_status.state in (BreakerState.TRIPPED, BreakerState.COOLING):
                logger.warning("Breaker tripped on %s: %s", dt, breaker_status.reason)
                risk_engine._circuit_breaker.record_day(daily_pnl_total)
                total = broker.total_value
                daily_pnl = daily_pnl_total
                daily_return = daily_pnl_pct
                record = DailyRecord(
                    trade_date=dt,
                    cash=broker.cash,
                    market_value=total - broker.cash,
                    total_value=total,
                    daily_pnl=daily_pnl,
                    daily_return=daily_return,
                    cumulative_return=(total - self._initial_cash) / self._initial_cash,
                    trades=[],
                )
                daily_records.append(record)
                prev_total = total
                prev_close = prices
                if progress_callback:
                    progress_callback(dt, (i + 1) / total_dates * 100)
                continue

            # Track signal quality
            sig_generated += len(signals)

            # Execute signals
            for signal in signals:
                sig_price = prices.get(signal.ts_code, 0.0)
                if signal.signal_type == SignalType.BUY and sig_price <= 0:
                    continue  # No price data for this stock

                budget = 0.0
                if signal.signal_type == SignalType.BUY:
                    budget = broker.total_value * signal.target_weight

                # Per-order position check (breaker already passed)
                risk_result = risk_engine.check_order(
                    direction=signal.signal_type.value,
                    ts_code=signal.ts_code,
                    price=prices.get(signal.ts_code, 0.0),
                    budget=budget,
                    total_value=broker.total_value,
                    current_positions=positions_snapshot,
                    daily_pnl=daily_pnl_total,
                    daily_pnl_pct=daily_pnl_pct,
                    today=dt,
                    daily_volume=today_data[today_data["ts_code"] == signal.ts_code]["amount"].sum() if signal.ts_code in today_data["ts_code"].values else None,
                )

                all_stop_loss_codes.update(risk_result.stop_loss_hit)

                if not risk_result.allowed:
                    sig_rejected += 1
                    continue
                sig_executed += 1

                if signal.signal_type == SignalType.BUY:
                    price = prices.get(signal.ts_code, 0)
                    if price <= 0:
                        continue
                    max_shares = risk_result.position_check.max_shares if risk_result.position_check else 100
                    volume = min(int(budget / price), max_shares) if budget > 0 else max_shares
                    result = broker.place_order("BUY", signal.ts_code, price, volume, dt, pre_close=prev_close.get(signal.ts_code, 0.0))
                    broker._state = result.remaining_state
                    all_trades_today.extend(result.trades)

                elif signal.signal_type == SignalType.SELL:
                    pos = broker.positions.get(signal.ts_code)
                    if pos:
                        price = prices.get(signal.ts_code, pos.current_price)
                        result = broker.place_order("SELL", signal.ts_code, price, pos.available_volume, dt, pre_close=prev_close.get(signal.ts_code, 0.0))
                        broker._state = result.remaining_state
                        all_trades_today.extend(result.trades)

            # 7. Execute stop-loss orders (auto-sell triggered positions)
            for ts_code in all_stop_loss_codes:
                pos = broker.positions.get(ts_code)
                if pos and pos.available_volume > 0:
                    price = prices.get(ts_code, pos.current_price)
                    result = broker.place_order("SELL", ts_code, price, pos.available_volume, dt, pre_close=prev_close.get(ts_code, 0.0))
                    broker._state = result.remaining_state
                    all_trades_today.extend(result.trades)

            # 8. Sync positions back into strategy after execution
            holdings_dict = {
                code: {
                    "volume": pos.volume,
                    "available_volume": pos.available_volume,
                    "avg_cost": pos.avg_cost,
                    "market_value": pos.market_value,
                    "current_price": pos.current_price,
                }
                for code, pos in broker.positions.items()
            }
            strategy.sync_positions(holdings_dict)

            # 9. Record daily snapshot
            total = broker.total_value
            daily_pnl = total - prev_total
            daily_return = daily_pnl / prev_total if prev_total > 0 else 0.0
            record = DailyRecord(
                trade_date=dt,
                cash=broker.cash,
                market_value=total - broker.cash,
                total_value=total,
                daily_pnl=daily_pnl,
                daily_return=daily_return,
                cumulative_return=(total - self._initial_cash) / self._initial_cash,
                trades=all_trades_today,
            )
            daily_records.append(record)
            prev_total = total
            risk_engine.record_day(dt, daily_pnl, daily_return)
            prev_close = prices

            if progress_callback:
                progress_callback(dt, (i + 1) / total_dates * 100)

        # 7. Compute metrics
        return self._compute_metrics(daily_records, broker.state.trade_history, bench_start, bench_end,
                                     sig_generated, sig_rejected, sig_executed)

    def _compute_metrics(
        self,
        daily_records: list[DailyRecord],
        trades: list[Trade],
        bench_start: float = 0.0,
        bench_end: float = 0.0,
        sig_gen: int = 0,
        sig_rej: int = 0,
        sig_exe: int = 0,
    ) -> BacktestResult:
        """Compute all performance metrics."""
        if not daily_records:
            return BacktestResult(
                initial_cash=self._initial_cash, final_value=self._initial_cash,
                total_return=0, annual_return=0, sharpe_ratio=0,
                max_drawdown=0, max_drawdown_duration=0,
                total_trades=0, win_rate=0, avg_win_pct=0, avg_loss_pct=0,
                profit_factor=0, benchmark_return=0, alpha=0,
                daily_records=[], trade_history=[],
            )

        returns = pd.Series([r.daily_return for r in daily_records])
        final_value = daily_records[-1].total_value
        total_return = (final_value - self._initial_cash) / self._initial_cash

        n_days = len(daily_records)
        annual_return = (1 + total_return) ** (244 / n_days) - 1 if n_days > 0 else 0

        # Sharpe ratio (annualized)
        excess = returns - 0.03 / 244  # Risk-free ~3%
        sharpe = (excess.mean() / excess.std() * np.sqrt(244)) if excess.std() > 0 else 0.0

        # Max drawdown
        cumulative = pd.Series([r.cumulative_return for r in daily_records])
        rolling_max = cumulative.expanding().max()
        drawdowns = cumulative - rolling_max
        max_drawdown = drawdowns.min()

        # Max drawdown duration
        in_dd = drawdowns < 0
        dd_durations = []
        count = 0
        for v in in_dd:
            if v:
                count += 1
            else:
                if count > 0:
                    dd_durations.append(count)
                count = 0
        if count > 0:
            dd_durations.append(count)
        max_dd_duration = max(dd_durations) if dd_durations else 0

        # Trade analysis
        buy_trades = [t for t in trades if t.direction == "BUY"]
        sell_trades = [t for t in trades if t.direction == "SELL"]

        # Match buy/sell pairs to compute win rate
        trade_pairs = self._match_trades(trades)
        wins = [tp for tp in trade_pairs if tp["pnl"] > 0]
        losses = [tp for tp in trade_pairs if tp["pnl"] <= 0]

        win_rate = len(wins) / len(trade_pairs) if trade_pairs else 0.0
        avg_win = np.mean([tp["pnl_pct"] for tp in wins]) if wins else 0.0
        avg_loss = np.mean([tp["pnl_pct"] for tp in losses]) if losses else 0.0

        total_profit = sum(tp["pnl"] for tp in wins)
        total_loss = abs(sum(tp["pnl"] for tp in losses))
        profit_factor = total_profit / total_loss if total_loss > 0 else 0.0

        bench_return = (bench_end / bench_start - 1.0) if bench_start > 0 else 0.0
        alpha = total_return - bench_return

        return BacktestResult(
            initial_cash=self._initial_cash,
            final_value=final_value,
            total_return=total_return,
            annual_return=annual_return,
            sharpe_ratio=sharpe,
            max_drawdown=max_drawdown,
            max_drawdown_duration=max_dd_duration,
            total_trades=len(sell_trades),
            win_rate=win_rate,
            avg_win_pct=avg_win,
            avg_loss_pct=avg_loss,
            profit_factor=profit_factor,
            benchmark_return=bench_return,
            alpha=alpha,
            signals_generated=sig_gen,
            signals_rejected=sig_rej,
            signals_executed=sig_exe,
            daily_records=daily_records,
            trade_history=trades,
        )

    @staticmethod
    def _match_trades(trades: list[Trade]) -> list[dict]:
        """Match buy/sell pairs using FIFO and compute P&L per trade."""
        pairs = []
        position: dict[str, list[dict]] = {}  # ts_code → [{"volume": v, "cost": c}, ...]

        for trade in trades:
            if trade.direction == "BUY":
                if trade.ts_code not in position:
                    position[trade.ts_code] = []
                position[trade.ts_code].append({
                    "volume": trade.volume,
                    "cost": trade.price,
                    "commission": trade.commission,
                })
            elif trade.direction == "SELL":
                code = trade.ts_code
                remaining = trade.volume
                sell_proceeds = trade.amount - trade.commission - trade.stamp_tax
                buy_cost = 0.0
                buy_commission = 0.0
                total_matched = 0

                while remaining > 0 and code in position and position[code]:
                    lot = position[code][0]
                    matched = min(remaining, lot["volume"])
                    ratio = matched / lot["volume"]

                    buy_cost += lot["cost"] * matched
                    buy_commission += lot["commission"] * ratio
                    remaining -= matched
                    total_matched += matched

                    lot["volume"] -= matched
                    if lot["volume"] <= 0:
                        position[code].pop(0)

                if total_matched > 0:
                    pnl_amount = sell_proceeds * (total_matched / trade.volume) - buy_cost - buy_commission
                    pnl_pct = pnl_amount / (buy_cost + buy_commission) if (buy_cost + buy_commission) > 0 else 0
                    pairs.append({
                        "ts_code": trade.ts_code,
                        "buy_date": "-",
                        "sell_date": str(trade.trade_date),
                        "volume": total_matched,
                        "pnl": pnl_amount,
                        "pnl_pct": pnl_pct,
                    })

        return pairs
