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
import pandas as pd
from strategy.base.strategy_template import Signal, SignalType

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
    if latest_db and latest_db < today - timedelta(days=3):
        logger.warning("DB data is stale (latest=%s, today=%s). Run backfill_baostock first.", latest_db, today)

    # -- Market health check: skip buys if market in downtrend --
    market_ok = True
    market_status = ""
    try:
        csi = ch.client.query_df(
            "SELECT trade_date, close FROM daily_bars "
            "WHERE ts_code = '000300.SH' AND trade_date >= %(start)s "
            "ORDER BY trade_date",
            parameters={"start": (today - timedelta(days=120)).isoformat()},
        )
        if not csi.empty and len(csi) >= 20:
            csi_close = csi["close"]
            ma20 = csi_close.rolling(20).mean().iloc[-1]
            ma50 = csi_close.rolling(50).mean().iloc[-1] if len(csi) >= 50 else ma20
            current = csi_close.iloc[-1]
            pct_from_ma20 = (current - ma20) / ma20 * 100
            pct_from_ma50 = (current - ma50) / ma50 * 100
            chg_5d = (current - csi_close.iloc[-5]) / csi_close.iloc[-5] * 100 if len(csi) >= 5 else 0

            market_status = (
                f"沪深300 {csi['trade_date'].iloc[-1]}: {current:.0f} | "
                f"MA20: {ma20:.0f} ({pct_from_ma20:+.1f}%) | "
                f"5日: {chg_5d:+.1f}%"
            )

            if pct_from_ma20 < -5:
                market_ok = False
                market_status += " | ⛔ 大盘深跌 -5%以下，暂停买入"
            elif pct_from_ma20 < -3:
                market_ok = False
                market_status += " | ⚠️ 大盘跌破MA20 -3%，暂停买入"
            elif chg_5d < -5:
                market_ok = False
                market_status += " | ⚠️ 近5日跌超5%，暂停买入"

    except Exception as e:
        logger.warning("Market health check failed: %s", e)

    logger.info("Market: %s", market_status or "data unavailable")

    # Set end_date for signal generation
    end_date = latest_db

    import random
    import time as _time
    random.seed(int(_time.time() * 1000) % (2**31))
    codes = ch.get_all_codes_on_date(end_date)
    codes = [c for c in codes if c != '000300.SH']
    sample = random.sample(codes, min(len(codes), 2000))
    codes_tuple = tuple(sample)
    start_date = end_date - timedelta(days=120)  # 120d enough for all indicators

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

    # 2. Run strategy — default multi-strategy voting
    if strategy not in ("trend_follow", "mean_revert", "multi"):
        # Default to multi-strategy mode
        strategy = "multi"

    all_buys: dict[str, dict] = {}  # code → {score, signals, reasons}
    all_sells: set[str] = set()

    if strategy in ("mean_revert", "multi"):
        mr = MeanRevertStrategy("daily_mr")
        mr.init(bb_period=23, bb_std=3.0, rsi_oversold=26, rsi_overbought=65,
                stop_loss=0.05, take_profit=0.10, top_n=10,
                min_price=5.0, min_turnover=1.0,
                use_atr_stop=True, use_vol_target=True,
                current_holdings=current_positions)
        mr_signals = mr.on_data(df, end_date)
        for s in mr_signals:
            if s.signal_type.value == "BUY":
                entry = all_buys.setdefault(s.ts_code, {"score": 0, "reasons": [], "weight": 0})
                entry["score"] += 1
                entry["reasons"].append(f"MR: {s.reason[:30]}")
                entry["weight"] = s.target_weight
            else:
                all_sells.add(s.ts_code)
        logger.info("MeanRevert: %d buys, %d sells",
                     sum(1 for s in mr_signals if s.signal_type.value == "BUY"),
                     sum(1 for s in mr_signals if s.signal_type.value == "SELL"))

    if strategy in ("trend_follow", "multi"):
        tf = TrendFollowStrategy("daily_tf")
        tf.init(ma_fast=5, ma_slow=20, ma_trend=60, top_n=10)
        tf.sync_positions(current_positions)
        tf_signals = tf.on_data(df, end_date)
        for s in tf_signals:
            if s.signal_type.value == "BUY":
                entry = all_buys.setdefault(s.ts_code, {"score": 0, "reasons": [], "weight": 0})
                entry["score"] += 1
                entry["reasons"].append(f"TF: {s.reason[:30]}")
                if entry["weight"] == 0:
                    entry["weight"] = s.target_weight
            else:
                all_sells.add(s.ts_code)
        logger.info("TrendFollow: %d buys, %d sells",
                     sum(1 for s in tf_signals if s.signal_type.value == "BUY"),
                     sum(1 for s in tf_signals if s.signal_type.value == "SELL"))

    # Build consensus signals
    signals = []
    for code, info in all_buys.items():
        if code in current_positions or code in all_sells:
            continue
        votes = info["score"]
        conf = 0.5 + votes * 0.25  # 2 votes = 100%, 1 vote = 75%
        weight = info["weight"] * (0.5 if votes == 1 else 1.0)  # Half-size for single-vote
        signals.append(Signal(
            ts_code=code, signal_type=SignalType.BUY,
            confidence=conf,
            reason=f"[{votes}/2策略] {' | '.join(info['reasons'])}",
            target_weight=weight,
            timestamp=end_date,
        ))

    for code in all_sells:
        signals.append(Signal(
            ts_code=code, signal_type=SignalType.SELL,
            confidence=0.8 if code in all_sells else 0.5,
            reason="Multi-strategy consensus: sell",
            target_weight=0.0, timestamp=end_date,
        ))

    logger.info("Consensus: %d buys, %d sells (from %d buy candidates)",
                 sum(1 for s in signals if s.signal_type.value == "BUY"),
                 sum(1 for s in signals if s.signal_type.value == "SELL"),
                 len(all_buys))

    # Market gate: filter out buys when market is in downtrend
    if not market_ok:
        buys_before = [s for s in signals if s.signal_type.value == "BUY"]
        signals = [s for s in signals if s.signal_type.value != "BUY"]
        logger.warning("MARKET DOWNTREND — blocked %d buy signals. Only sells allowed.", len(buys_before))

    print_signals(signals, dry_run, price_lookup, name_lookup, stop_lookup,
                  total_capital=200_000, top_n=10, market_info=market_status)

    # Save positions for tomorrow
    new_positions = {}
    for s in signals:
        if s.signal_type.value == "BUY":
            entry_price = price_lookup.get(s.ts_code, 0.0)
            stop_loss = stop_lookup.get(s.ts_code, entry_price * 0.95)
            # Calculate volume for new positions
            per_stock_budget = 200_000 * 0.80 / top_n
            shares = int(per_stock_budget / entry_price / 100) * 100 if entry_price > 0 else 0
            new_positions[s.ts_code] = {
                "entry_price": entry_price,
                "volume": shares,
                "amount": shares * entry_price,
                "buy_date": str(end_date),
                "stop_loss": stop_loss,
                "take_profit": entry_price * 1.15,
                "commission": 5.00,
                "cost_basis": entry_price + 5.00 / shares if shares > 0 else entry_price,
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
                  total_capital: float = 200_000, top_n: int = 10,
                  market_info: str = ""):
    """Print signals with actionable details: price, shares, amount."""
    if market_info:
        print(f"\n  📊 {market_info}")
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
