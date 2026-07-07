#!/usr/bin/env python
"""Quick Monday signals — minimal download, 2000 stock sample."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json, time
from datetime import date, timedelta
import numpy as np
import pandas as pd

from xtquant import xtdata

from strategy.timing.mean_revert import MeanRevertStrategy

today = date.today()
print(f"=== 周一交易信号 ({today}) ===")

# 1. Fast sample from QMT
codes = xtdata.get_stock_list_in_sector("沪深A股")
print(f"全市场: {len(codes)} 只")

# Sample 2000 by liquidity (skip ST, use all for speed)
import random
import time as _time
random.seed(int(_time.time() * 1000) % (2**31))
sample = random.sample(codes, min(len(codes), 2000))
print(f"采样: {len(sample)} 只")

# 2. Download only 60 days
end = today.strftime("%Y%m%d")
start = (today - timedelta(days=60)).strftime("%Y%m%d")
print(f"下载 {start}-{end} ...")

done = [0]
def cb(d):
    done[0] += 1
    if done[0] % 500 == 0:
        print(f"  {done[0]}/{len(sample)} ...")

xtdata.download_history_data2(sample, "1d", start, end, callback=cb)
print(f"下载完成: {done[0]} 只")

# 3. Build DataFrame
rows = []
for code in sample:
    data = xtdata.get_market_data_ex(
        field_list=["open", "high", "low", "close", "volume", "amount"],
        stock_list=[code], period="1d",
        start_time=start, end_time=end, count=-1,
    )
    if data and code in data:
        df_s = data[code]
        if not df_s.empty:
            for idx, row in df_s.iterrows():
                rows.append({
                    "ts_code": code, "trade_date": pd.Timestamp(idx).date(),
                    "open": row["open"], "high": row["high"],
                    "low": row["low"], "close": row["close"],
                    "vol": float(row.get("volume", 0) or 0),
                    "amount": float(row.get("amount", 0) or 0),
                    "turnover_rate": 0.0,
                })

df = pd.DataFrame(rows)
df = df.drop_duplicates(subset=["ts_code", "trade_date"])
max_dt = df["trade_date"].max()
print(f"数据: {len(df)} 行, {df['ts_code'].nunique()} 只, 最新={max_dt}")

# 4. Load positions
pos_file = Path(__file__).resolve().parent / "positions.json"
current_positions = {}
try:
    if pos_file.exists():
        current_positions = json.loads(pos_file.read_text())
        print(f"当前持仓: {len(current_positions)} 只")
except Exception:
    pass

# 5. Build lookups
last_day = df[df["trade_date"] == max_dt]
price_lookup = dict(zip(last_day["ts_code"], last_day["close"]))

# ATR stop lookup
stop_lookup = {}
for code in last_day["ts_code"].unique():
    s = df[df["ts_code"] == code].sort_values("trade_date")
    if len(s) < 20:
        continue
    h, l, c = s["high"], s["low"], s["close"]
    tr = pd.concat([h-l, (h-c.shift(1)).abs(), (l-c.shift(1)).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean().iloc[-1]
    price = c.iloc[-1]
    if price > 0 and atr > 0:
        stop_lookup[code] = max(price - 2.0 * atr, price * 0.92)

# Name lookup from xtdata
name_lookup = {}
try:
    # QMT doesn't provide Chinese names easily, use a quick query
    pass
except Exception:
    pass

# 6. Run strategy
strat = MeanRevertStrategy("mon_mr")
strat.init(
    bb_period=23, bb_std=3.0, rsi_oversold=26, rsi_overbought=65,
    stop_loss=0.05, take_profit=0.10, top_n=10,
    min_price=5.0, min_turnover=1.0,
    use_atr_stop=True, use_vol_target=True,
    current_holdings=current_positions,
    green_candle=True,
)
signals = strat.on_data(df, max_dt)

buys = sorted([s for s in signals if s.signal_type.value == "BUY"],
              key=lambda s: s.confidence, reverse=True)
sells = [s for s in signals if s.signal_type.value == "SELL"]

# 7. Print
total_cap = 200_000
top_n = 10
per_stock = total_cap * 0.80 / top_n

print("\n" + "=" * 80)
print(f"  📊 周一交易信号 (7月6日) — {len(buys)}买 {len(sells)}卖")
print("=" * 80)

# Sell signals first (important!)
if sells:
    print(f"\n  🔴 卖出 (周一开盘清仓):")
    print(f"  {'代码':<12} {'现价':<8} {'入场价':<8} {'盈亏':<8} {'理由'}")
    print(f"  {'-'*60}")
    for s in sells:
        p = price_lookup.get(s.ts_code, 0)
        pos = current_positions.get(s.ts_code, {})
        entry = pos.get("entry_price", 0) if isinstance(pos, dict) else 0
        pnl = f"+{(p-entry)/entry*100:.1f}%" if entry > 0 and p >= entry else f"{(p-entry)/entry*100:.1f}%" if entry > 0 else "?"
        print(f"  {s.ts_code:<12} ¥{p:<7.2f} ¥{entry:<7.2f} {pnl:<8} {s.reason[:30]}")

if buys:
    print(f"\n  🟢 买入 (每只约 ¥{per_stock:,.0f}):")
    print(f"  {'排名':<5} {'代码':<12} {'现价':<8} {'股数':<6} {'金额':<10} {'止损':<8} {'置信度':<8} {'信号'}")
    print(f"  {'-'*80}")
    for i, s in enumerate(buys[:top_n], 1):
        p = price_lookup.get(s.ts_code, 0)
        if p <= 0:
            continue
        shares = int(per_stock / p / 100) * 100
        amount = shares * p
        stop = stop_lookup.get(s.ts_code, p * 0.95)
        conf = s.confidence * 100
        print(f"  {i:<5} {s.ts_code:<12} ¥{p:<7.2f} {shares:<6,} ¥{amount:<9,.0f} ¥{stop:<7.2f} {conf:>4.0f}%    {s.reason[:25]}")

if not buys and not sells:
    print("\n  ⚠️ 无信号 — 继续持有现有仓位")

print(f"\n  ⚠️ 操作时间: 周一 9:30 开盘")
print(f"  📋 总仓位80% 单票止损-5% 日亏损2%熔断")
print("=" * 80)

# 8. Save positions
new_positions = {}
for s in buys[:top_n]:
    p = price_lookup.get(s.ts_code, 0)
    if p > 0:
        new_positions[s.ts_code] = {"entry_price": p, "buy_date": str(today)}
sell_codes = {s.ts_code for s in sells}
for code, pos in current_positions.items():
    if code not in sell_codes and code not in new_positions:
        new_positions[code] = pos
from config.settings import atomic_write_json
atomic_write_json(str(pos_file), new_positions, indent=2, ensure_ascii=False)
print(f"\n持仓已更新: {len(new_positions)} 只 → scripts/positions.json")
