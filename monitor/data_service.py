"""
Data service for Streamlit monitor — bridges ClickHouse/PostgreSQL to dashboard.

All functions return pandas DataFrames ready for charting.
Gracefully fall back to empty/sample data when DB unavailable.
"""

import logging
from datetime import date, timedelta

import pandas as pd

logger = logging.getLogger(__name__)


def _get_ch():
    """Lazy ClickHouse client."""
    try:
        from data.storage.clickhouse_client import get_clickhouse_client
        return get_clickhouse_client()
    except Exception:
        return None


def _get_pg():
    """Lazy PostgreSQL client."""
    try:
        from data.storage.postgres_client import get_postgres_client
        return get_postgres_client()
    except Exception:
        return None


def get_equity_curve(days: int = 365) -> pd.DataFrame:
    """Get portfolio equity curve from ClickHouse daily_bars + PostgreSQL daily_pnl.

    If no trade data exists, return empty DataFrame (dashboard shows "no data").
    """
    pg = _get_pg()
    if pg is None or not pg.ping():
        return pd.DataFrame(columns=["trade_date", "equity"])

    try:
        end = date.today()
        start = end - timedelta(days=days)
        rows = pg.get_pnl_history(start, end)
        if rows:
            return pd.DataFrame(rows)[["trade_date", "total_value"]].rename(
                columns={"total_value": "equity"}
            )
    except Exception:
        logger.debug("No P&L data yet", exc_info=True)

    return pd.DataFrame(columns=["trade_date", "equity"])


def get_positions() -> pd.DataFrame:
    """Get current positions from ClickHouse (latest day's close data for held stocks).

    Falls back to empty if no trade records or DB unavailable.
    """
    pg = _get_pg()
    if pg is None or not pg.ping():
        return pd.DataFrame(columns=["ts_code", "name", "volume", "avg_cost", "current_price", "market_value", "pnl_pct"])

    try:
        # Try PostgreSQL first, fall back to ClickHouse
        from sqlalchemy import text
        with pg.engine.connect() as conn:
            result = conn.execute(text("""
                SELECT ts_code, direction, price, volume, trade_time
                FROM trade_records
                ORDER BY trade_time DESC
                LIMIT 500
            """))
            rows = [dict(r._mapping) for r in result]
    except Exception:
        return pd.DataFrame(columns=["ts_code", "volume", "avg_cost", "current_price", "market_value", "pnl_pct"])

    if not rows:
        return pd.DataFrame(columns=["ts_code", "volume", "avg_cost", "current_price", "market_value", "pnl_pct"])

    df = pd.DataFrame(rows)
    # Net position per stock: buy volume - sell volume
    buys = df[df["direction"] == "BUY"].groupby("ts_code").agg(
        buy_volume=("volume", "sum"),
        buy_amount=("price", lambda x: (x * df.loc[x.index, "volume"]).sum()),
    )
    sells = df[df["direction"] == "SELL"].groupby("ts_code").agg(
        sell_volume=("volume", "sum"),
    )

    positions = buys.join(sells, how="left").fillna(0)
    positions["net_volume"] = positions["buy_volume"] - positions["sell_volume"]
    positions["avg_cost"] = positions["buy_amount"] / positions["buy_volume"].replace(0, 1)
    positions = positions[positions["net_volume"] > 0].copy()
    # Try to get latest close prices from ClickHouse
    try:
        from data.storage.clickhouse_client import get_clickhouse_client
        ch = get_clickhouse_client()
        if ch and ch.ping():
            r = ch.client.query("SELECT max(trade_date) FROM daily_bars")
            latest = r.first_row[0]
            codes = list(positions.index)
            if codes and latest:
                prices_df = ch.client.query_df(
                    "SELECT ts_code, close FROM daily_bars "
                    "WHERE ts_code IN %(codes)s AND trade_date = %(d)s",
                    parameters={"codes": tuple(codes), "d": str(latest)},
                )
                price_map = dict(zip(prices_df["ts_code"], prices_df["close"]))
                positions["current_price"] = [price_map.get(c, avg) for c, avg in zip(positions.index, positions["avg_cost"])]
    except Exception:
        positions["current_price"] = positions["avg_cost"]

    positions["market_value"] = positions["net_volume"] * positions["current_price"]
    positions["pnl_pct"] = (positions["current_price"] - positions["avg_cost"]) / positions["avg_cost"].replace(0, 1)

    return positions.reset_index()[["ts_code", "net_volume", "avg_cost", "current_price", "market_value", "pnl_pct"]]


def get_recent_signals(days: int = 30) -> pd.DataFrame:
    """Get recent strategy signals from PostgreSQL."""
    pg = _get_pg()
    if pg is None or not pg.ping():
        return pd.DataFrame(columns=["signal_time", "ts_code", "strategy_name", "signal_type", "confidence", "executed"])

    try:
        from sqlalchemy import text
        with pg.engine.connect() as conn:
            result = conn.execute(text("""
                SELECT signal_time, ts_code, strategy_name, signal_type, confidence, executed
                FROM strategy_signals
                WHERE signal_time >= CURRENT_DATE - :days
                ORDER BY signal_time DESC
                LIMIT 100
            """), {"days": days})
            rows = [dict(r._mapping) for r in result]
        if rows:
            return pd.DataFrame(rows)
    except Exception:
        logger.debug("No signal data", exc_info=True)

    return pd.DataFrame(columns=["signal_time", "ts_code", "strategy_name", "signal_type", "confidence", "executed"])


def get_risk_events(days: int = 30) -> pd.DataFrame:
    """Get recent risk events from PostgreSQL."""
    pg = _get_pg()
    if pg is None or not pg.ping():
        return pd.DataFrame(columns=["event_time", "event_type", "severity", "detail"])

    try:
        from sqlalchemy import text
        with pg.engine.connect() as conn:
            result = conn.execute(text("""
                SELECT event_time, event_type, severity, detail
                FROM risk_events
                WHERE event_time >= CURRENT_DATE - :days
                ORDER BY event_time DESC
                LIMIT 50
            """), {"days": days})
            rows = [dict(r._mapping) for r in result]
        if rows:
            return pd.DataFrame(rows)
    except Exception:
        logger.debug("No risk event data", exc_info=True)

    return pd.DataFrame(columns=["event_time", "event_type", "severity", "detail"])


def get_db_status() -> dict:
    """Check database connectivity."""
    status = {"clickhouse": False, "postgres": False}

    ch = _get_ch()
    if ch:
        try:
            status["clickhouse"] = ch.ping()
        except Exception:
            pass

    pg = _get_pg()
    if pg:
        try:
            status["postgres"] = pg.ping()
        except Exception:
            pass

    return status
