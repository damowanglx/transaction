#!/usr/bin/env python
"""Check existing positions for sell signals only."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json, random, logging
from datetime import date, timedelta
from data.storage.clickhouse_client import get_clickhouse_client
from strategy.timing.mean_revert import MeanRevertStrategy
from config.settings import setup_logging
setup_logging()
logger = logging.getLogger("check")

# Load positions
pos_file = Path(__file__).resolve().parent / "positions.json"
positions = {}
if pos_file.exists():
    positions = json.loads(pos_file.read_text())

if not positions:
    print("No positions found")
    sys.exit(0)

print(f"=== 持仓检查 ({len(positions)} 只) ===\n")

# Load data for held stocks
ch = get_clickhouse_client()
if not ch.ping():
    print("DB unreachable")
    sys.exit(1)

codes = list(positions.keys())
codes_tuple = tuple(codes)

# Get latest date
r = ch.client.query("SELECT max(trade_date) FROM daily_bars WHERE ts_code != '000300.SH'")
latest = r.first_row[0]
if isinstance(latest, str):
    from datetime import datetime
    latest = datetime.strptime(latest, "%Y-%m-%d").date()
start = latest - timedelta(days=120)

import pandas as pd
df = ch.client.query_df(
    "SELECT ts_code, trade_date, open, high, low, close, vol, amount, turnover_rate "
    "FROM daily_bars WHERE ts_code IN %(codes)s "
    "AND trade_date >= %(start)s AND trade_date <= %(end)s "
    "ORDER BY ts_code, trade_date",
    parameters={"codes": codes_tuple, "start": start.isoformat(), "end": latest.isoformat()},
)

if df.empty:
    print("No data for held stocks!")
    sys.exit(1)

print(f"数据: {df['ts_code'].nunique()}/{len(codes)} 只有数据, 最新={latest}\n")

# Run strategy to check for sell signals
strat = MeanRevertStrategy("check")
strat.init(
    bb_period=23, bb_std=3.0, rsi_oversold=26, rsi_overbought=65,
    stop_loss=0.05, take_profit=0.10, top_n=10,
    min_price=5.0, min_turnover=1.0,
    use_atr_stop=True, use_vol_target=True,
    current_holdings=positions,
)

signals = strat.on_data(df, latest)
sells = [s for s in signals if s.signal_type.value == "SELL"]

# Build price/indicator lookup
last_day = df[df["trade_date"] == latest]
prices = dict(zip(last_day["ts_code"], last_day["close"]))

print(f"  {'代码':<12} {'现价':<8} {'入场价':<8} {'盈亏%':<8} {'RSI':<6} {'BB位置':<8} {'状态'}")
print(f"  {'-'*70}")

sell_codes = {s.ts_code for s in sells}
total_pnl = 0
for code, pos in positions.items():
    if code not in prices:
        continue
    entry = pos.get("entry_price", 0) if isinstance(pos, dict) else pos
    price = prices[code]
    pnl = (price - entry) / entry * 100 if entry > 0 else 0
    total_pnl += pnl

    # Quick RSI/BB calc
    s = df[df["ts_code"] == code].sort_values("trade_date")
    close = s["close"]
    rsi = MeanRevertStrategy._calc_rsi(close, 14) if len(close) >= 15 else 0
    ma = close.rolling(23).mean().iloc[-1]
    std = close.rolling(23).std().iloc[-1]
    upper = ma + 3.0 * std
    lower = ma - 3.0 * std
    bb_pos = (price - lower) / (upper - lower) if (upper - lower) > 0 else 0.5

    status = "🔴 卖出!" if code in sell_codes else "🟢 持有"
    pnl_str = f"+{pnl:.1f}%" if pnl >= 0 else f"{pnl:.1f}%"
    print(f"  {code:<12} ¥{price:<7.2f} ¥{entry:<7.2f} {pnl_str:<8} {rsi:<6.0f} {bb_pos:<8.3f} {status}")

avg_pnl = total_pnl / len(positions) if positions else 0
print(f"\n  平均盈亏: {avg_pnl:+.1f}%")
print(f"  卖出信号: {len(sells)} 只")
print(f"  建议: {'减仓' if sells else '继续持有全部'}")

if sells:
    print(f"\n  ⚠️ 周一开盘优先卖出:")
    for s in sells:
        print(f"    {s.ts_code} — {s.reason}")
