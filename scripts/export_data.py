#!/usr/bin/env python
"""Export trading data for external apps (复盘app, analysis tools, etc.)

Outputs JSON files to export/ directory:
- positions.json     — Current holdings with stop/take-profit
- signals.json       — Latest trading signals
- trades.json        — Full trade history with P&L
- watchlist.json     — Stop-loss watch list (prices to monitor)
- portfolio.json     — Portfolio summary for dashboard apps

Usage:
    python scripts/export_data.py              # Export all
    python scripts/export_data.py --watch      # Stop-loss monitor format
    python scripts/export_data.py --csv        # CSV format for Excel
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json, logging
from datetime import date, datetime

logger = logging.getLogger("export")

EXPORT_DIR = Path(__file__).resolve().parent.parent / "export"
POSITIONS_FILE = Path(__file__).resolve().parent / "positions.json"
TRADE_LOG = Path(__file__).resolve().parent / "trade_log.json"


def ensure_dir():
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)


def get_latest_prices():
    try:
        from data.storage.clickhouse_client import get_clickhouse_client
        ch = get_clickhouse_client()
        if ch.ping():
            r = ch.client.query("SELECT max(trade_date) FROM daily_bars WHERE ts_code != '000300.SH'")
            latest = r.first_row[0]
            if isinstance(latest, str):
                from datetime import datetime as dt
                latest = dt.strptime(latest, "%Y-%m-%d").date()
            positions = json.loads(POSITIONS_FILE.read_text()) if POSITIONS_FILE.exists() else {}
            codes = list(positions.keys())
            if codes:
                df = ch.client.query_df(
                    "SELECT ts_code, close FROM daily_bars "
                    "WHERE ts_code IN %(codes)s AND trade_date = %(d)s",
                    parameters={"codes": tuple(codes), "d": latest.isoformat()},
                )
                return latest, dict(zip(df["ts_code"], df["close"]))
    except Exception:
        pass
    return None, {}


def export_all():
    """Export all data in standard JSON format."""
    ensure_dir()
    trade_date, prices = get_latest_prices()

    # 1. Positions with live metrics
    positions = json.loads(POSITIONS_FILE.read_text()) if POSITIONS_FILE.exists() else {}
    portfolio = []
    total_mv = 0
    total_cost = 0
    for code, pos in positions.items():
        entry = pos["entry_price"]
        vol = pos["volume"]
        price = prices.get(code, entry)
        mv = price * vol
        cost = entry * vol
        total_mv += mv
        total_cost += cost
        portfolio.append({
            "code": code,
            "name": pos.get("name", ""),
            "entry_price": entry,
            "volume": vol,
            "current_price": price,
            "market_value": round(mv, 2),
            "pnl_pct": round((price - entry) / entry * 100, 2),
            "pnl_amount": round((price - entry) * vol, 2),
            "stop_loss": pos.get("stop_loss", round(entry * 0.95, 2)),
            "take_profit": pos.get("take_profit", round(entry * 1.15, 2)),
            "buy_date": pos.get("buy_date", ""),
            "cost_basis": pos.get("cost_basis", entry),
        })

    # 2. Portfolio summary
    summary = {
        "date": str(trade_date or date.today()),
        "exported_at": datetime.now().isoformat(),
        "total_capital": 200_000,
        "holdings": len(portfolio),
        "market_value": round(total_mv, 2),
        "cost_basis": round(total_cost, 2),
        "total_pnl": round(total_mv - total_cost, 2),
        "total_pnl_pct": round((total_mv - total_cost) / total_cost * 100, 2) if total_cost > 0 else 0,
        "position_pct": round(total_mv / 200_000 * 100, 1),
        "positions": portfolio,
    }

    with open(EXPORT_DIR / "portfolio.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    # 3. Stop-loss watchlist (for monitoring apps)
    watchlist = []
    for code, pos in positions.items():
        entry = pos["entry_price"]
        stop = pos.get("stop_loss", entry * 0.95)
        tp = pos.get("take_profit", entry * 1.15)
        price = prices.get(code, entry)
        gap_to_stop = round((price - stop) / stop * 100, 2)
        watchlist.append({
            "code": code,
            "name": pos.get("name", ""),
            "current": price,
            "stop": stop,
            "gap_pct": gap_to_stop,
            "alert": "SELL" if gap_to_stop <= 0 else "WARN" if gap_to_stop < 3 else "OK",
            "take_profit": tp,
        })
    watchlist.sort(key=lambda x: x["gap_pct"])

    with open(EXPORT_DIR / "watchlist.json", "w", encoding="utf-8") as f:
        json.dump({
            "updated": datetime.now().isoformat(),
            "data_date": str(trade_date),
            "items": watchlist,
        }, f, indent=2, ensure_ascii=False)

    # 4. Trade history
    trades = json.loads(TRADE_LOG.read_text()) if TRADE_LOG.exists() else []
    with open(EXPORT_DIR / "trades.json", "w", encoding="utf-8") as f:
        json.dump({
            "count": len(trades),
            "trades": trades,
        }, f, indent=2, ensure_ascii=False)

    # 5. Latest signals (try to read from signal output)
    signals_file = EXPORT_DIR.parent / "params_history"
    try:
        latest_signal = sorted(signals_file.glob("*.json"))[-1] if signals_file.exists() else None
    except Exception:
        latest_signal = None

    print(f"导出完成 → {EXPORT_DIR}/")
    print(f"  portfolio.json  — 持仓+盈亏 {len(portfolio)}只")
    print(f"  watchlist.json  — 止损监控列表")
    print(f"  trades.json     — 交易历史 {len(trades)}笔")
    print(f"\n复盘app直接读取 {EXPORT_DIR}/portfolio.json 即可")


def export_csv():
    """Export positions as CSV for Excel."""
    ensure_dir()
    import csv
    _, prices = get_latest_prices()
    positions = json.loads(POSITIONS_FILE.read_text()) if POSITIONS_FILE.exists() else {}

    with open(EXPORT_DIR / "positions.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["代码", "名称", "入场价", "股数", "现价", "市值", "盈亏%", "止损", "止盈", "买入日"])
        for code, pos in positions.items():
            entry = pos["entry_price"]
            vol = pos["volume"]
            price = prices.get(code, entry)
            w.writerow([
                code, pos.get("name", ""), entry, vol, price,
                round(price * vol, 2),
                round((price - entry) / entry * 100, 2),
                pos.get("stop_loss", round(entry * 0.95, 2)),
                pos.get("take_profit", round(entry * 1.15, 2)),
                pos.get("buy_date", ""),
            ])
    print(f"CSV导出 → {EXPORT_DIR}/positions.csv")


if __name__ == "__main__":
    if "--csv" in sys.argv:
        export_csv()
    else:
        export_all()
