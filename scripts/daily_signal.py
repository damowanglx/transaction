#!/usr/bin/env python
"""
Daily signal generation script.

Run after market close (e.g., 15:30 CST) to:
1. Fetch latest data
2. Run factor calculation
3. Generate stock selection signals
4. Send notifications via DingTalk/WeChat

Usage:
    python scripts/daily_signal.py
    python scripts/daily_signal.py --strategy trend_follow
    python scripts/daily_signal.py --dry-run  # Don't send notifications
"""

import json
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))

from config.risk_params import get_risk_config
from data.storage.clickhouse_client import get_clickhouse_client
from notify.dingtalk import DingTalkNotifier
from risk.risk_engine import RiskEngine
from strategy.selector.stock_selector import StockSelector
from strategy.timing.trend_follow import TrendFollowStrategy
from strategy.timing.mean_revert import MeanRevertStrategy

from config.settings import setup_logging
setup_logging()
logger = logging.getLogger("daily_signal")


def main(strategy: str = "trend_follow", dry_run: bool = False):
    logger.info("=" * 50)
    logger.info("Daily Signal Generation — %s", date.today())
    logger.info("=" * 50)

    # 1. Check if today is a trading day
    ch = get_clickhouse_client()
    if not ch.ping():
        logger.error("ClickHouse not reachable — abort")
        return

    r = ch.client.query("SELECT max(trade_date) FROM daily_bars")
    latest_db = r.first_row[0]
    if isinstance(latest_db, str):
        from datetime import datetime
        latest_db = datetime.strptime(latest_db, "%Y-%m-%d").date()
    today = date.today()
    if today.weekday() >= 5:
        logger.info("Today is weekend, skipping signal generation. Latest DB: %s", latest_db)
        return
    if latest_db and latest_db < today - timedelta(days=1):
        logger.warning("DB data is stale (latest=%s, today=%s). Run download_incremental first.", latest_db, today)

    # Filter to dates with actual data
    import random
    random.seed(42)
    r = ch.client.query("SELECT max(trade_date) FROM daily_bars WHERE ts_code != '000300.SH'")
    last_trade_date = r.first_row[0]
    if isinstance(last_trade_date, str):
        from datetime import datetime
        last_trade_date = datetime.strptime(last_trade_date, "%Y-%m-%d").date()
    codes = ch.get_all_codes_on_date(last_trade_date)
    codes = [c for c in codes if c != '000300.SH']
    sample = random.sample(codes, min(len(codes), 2000))
    codes_tuple = tuple(sample)
    end_date = last_trade_date
    start_date = end_date - timedelta(days=365)

    df = ch.client.query_df(
        "SELECT ts_code, trade_date, open, high, low, close, vol, amount, turnover_rate "
        "FROM daily_bars "
        "WHERE ts_code IN %(codes)s "
        "  AND trade_date >= %(start)s "
        "  AND trade_date <= %(end)s "
        "ORDER BY ts_code, trade_date",
        parameters={"codes": codes_tuple, "start": start_date.isoformat(), "end": end_date.isoformat()},
    )

    if df.empty:
        logger.error("No OHLCV data loaded from ClickHouse. Data pipeline may be down.")
        logger.error("Run: docker compose up -d && python scripts/download_history.py")
        return

    # Build price lookup from latest data
    last_day = df[df["trade_date"] == df["trade_date"].max()]
    price_lookup = dict(zip(last_day["ts_code"], last_day["close"])) if not last_day.empty else {}

    # Build ATR stop lookup
    import numpy as np
    import pandas as pd
    stop_lookup = {}
    for code in last_day["ts_code"].unique():
        s = df[df["ts_code"] == code].sort_values("trade_date")
        if len(s) < 20:
            continue
        h = s["high"]; l = s["low"]; c = s["close"]
        tr = pd.concat([h-l, (h-c.shift(1)).abs(), (l-c.shift(1)).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14).mean().iloc[-1]
        price = c.iloc[-1]
        if price > 0 and atr > 0:
            stop_lookup[code] = max(price - 2.0 * atr, price * 0.92)  # ATR*2 or 8% max

    # Build name lookup from stock_info table
    name_lookup = {}
    try:
        names = ch.client.query("SELECT ts_code, name FROM stock_info")
        for row in names.result_rows:
            name_lookup[row[0]] = row[1]
    except Exception:
        pass

    # Check emergency stop before generating signals
    from scripts.emergency_stop import is_halted
    if is_halted():
        logger.critical("EMERGENCY STOP ACTIVE — no signals generated")
        print("\n🚨 紧急停止已激活，不生成交易信号 🚨\n")
        return

    # Load current positions from file (yesterday's buys)
    pos_file = Path(__file__).resolve().parent / "positions.json"
    current_positions = {}
    try:
        if pos_file.exists():
            current_positions = json.loads(pos_file.read_text())
            logger.info("Loaded %d current positions", len(current_positions))
    except Exception:
        pass

    # 2. Run strategy
    if strategy == "trend_follow":
        strat = TrendFollowStrategy("daily_tf")
        strat.init(ma_fast=5, ma_slow=20, ma_trend=60, top_n=10)
    elif strategy == "mean_revert":
        strat = MeanRevertStrategy("daily_mr")
        strat.init(bb_period=23, bb_std=3.0, rsi_oversold=26, rsi_overbought=65,
                   stop_loss=0.05, take_profit=0.10, top_n=10,
                   min_price=5.0, min_turnover=1.0,
                   use_atr_stop=True, use_vol_target=True,
                   current_holdings=current_positions)
    else:
        selector = StockSelector("daily_selector")
        selector.init(
            factors=["mom_60", "vol_20", "avg_turn_20"],
            top_n=10,
        )
        signals = selector.on_data(df, end_date)
        print_signals(signals, dry_run, price_lookup)
        return

    signals = strat.on_data(df, end_date)
    print_signals(signals, dry_run, price_lookup, name_lookup, stop_lookup, total_capital=200_000, top_n=10)

    # Save positions for tomorrow
    new_positions = {}
    for s in signals:
        if s.signal_type.value == "BUY":
            new_positions[s.ts_code] = {
                "entry_price": price_lookup.get(s.ts_code, 0.0),
                "buy_date": str(end_date),
            }
    # Keep existing positions that weren't sold
    sell_codes = {s.ts_code for s in signals if s.signal_type.value == "SELL"}
    for code, pos in current_positions.items():
        if code not in sell_codes and code not in new_positions:
            new_positions[code] = pos
    from config.settings import atomic_write_json
    atomic_write_json(str(pos_file), new_positions, indent=2, ensure_ascii=False)
    logger.info("Saved %d positions for tomorrow", len(new_positions))


def print_signals(signals, dry_run: bool, price_lookup: dict[str, float] | None = None,
                  name_lookup: dict[str, str] | None = None,
                  stop_lookup: dict[str, float] | None = None,
                  total_capital: float = 200_000, top_n: int = 10):
    """Print signals with actionable details: price, shares, amount."""
    if not signals:
        logger.info("No signals generated today")
        return

    buys = sorted([s for s in signals if s.signal_type.value == "BUY"],
                  key=lambda s: s.confidence, reverse=True)  # Most oversold first
    sells = [s for s in signals if s.signal_type.value == "SELL"]
    prices = price_lookup or {}

    # Calculate position size per stock
    per_stock_budget = total_capital * 0.80 / top_n  # 80% total / N holdings

    print("\n" + "=" * 80)
    print(f"  📊 明日交易信号 ({len(buys)}买 {len(sells)}卖)")
    print("=" * 80)

    if buys:
        names = name_lookup or {}
        print(f"\n  🟢 买入 (每只约 ¥{per_stock_budget:,.0f}):")
        print(f"  {'代码':<12} {'名称':<8} {'买入价':<8} {'股数':<6} {'金额':<10} {'止损':<8} {'置信度'}")
        print(f"  {'-'*68}")
        for s in buys:
            p = prices.get(s.ts_code, 0.0)
            if p <= 0:
                continue
            shares = int(per_stock_budget / p / 100) * 100
            amount = shares * p
            stop = (stop_lookup or {}).get(s.ts_code, p * 0.95)
            sname = names.get(s.ts_code, "")[:6]
            print(f"  {s.ts_code:<12} {sname:<8} ¥{p:<7.2f} {shares:<6,} ¥{amount:<9,.0f} ¥{stop:<7.2f} {s.confidence*100:>4.0f}%")

    if sells:
        print(f"\n  🔴 卖出信号 (清仓):")
        print(f"  {'代码':<12} {'卖出价':<10} {'全部卖出':<10} {'理由'}")
        print(f"  {'-'*50}")
        for s in sells:
            p = prices.get(s.ts_code, 0.0)
            print(f"  {s.ts_code:<12} ¥{p:<9.2f} {'全部':<10} {s.reason[:30]}")

    print(f"\n  ⚠️  操作时间: 明天 9:30 开盘后")
    print(f"  📋 风控: 单票≤20% 止损-5% 日亏损2%熔断")
    print("=" * 80 + "\n")

    if not dry_run and (buys or sells):
        # DingTalk primary notification
        dt_notifier = DingTalkNotifier()
        dt_notifier.send_batch_signals(buys, sells, prices, stop_lookup or {}, name_lookup or {}, per_stock_budget)
        # WeChat backup notification
        try:
            from notify.wechat import WeChatNotifier
            wx = WeChatNotifier()
            wx.send_markdown(
                f"## 📊 明日交易信号 ({len(buys)}买 {len(sells)}卖)\n"
                + "\n".join(
                    f"- 🟢 {s.ts_code} @ ¥{prices.get(s.ts_code, 0):.2f} (置信度{s.confidence:.0%})"
                    for s in buys[:5]
                )
                + ("\n" + "\n".join(
                    f"- 🔴 {s.ts_code} @ ¥{prices.get(s.ts_code, 0):.2f}"
                    for s in sells[:5]
                ) if sells else "")
            )
        except Exception:
            pass


if __name__ == "__main__":
    strategy_arg = sys.argv[1] if len(sys.argv) > 1 else "trend_follow"
    dry = "--dry-run" in sys.argv
    main(strategy_arg, dry)
