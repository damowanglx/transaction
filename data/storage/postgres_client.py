"""
PostgreSQL client for business data: trades, P&L, risk events, signals.

Uses SQLAlchemy 2.0-style API with connection pooling.
"""

import logging
from datetime import date, datetime
from typing import Optional

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from config.settings import get_postgres_url

logger = logging.getLogger(__name__)


class PostgresClient:
    """PostgreSQL client for business/operational data."""

    def __init__(self):
        self._engine: Optional[Engine] = None

    @property
    def engine(self) -> Engine:
        """Lazy-initialize SQLAlchemy engine."""
        if self._engine is None:
            self._engine = create_engine(
                get_postgres_url(),
                pool_size=5,
                max_overflow=10,
                pool_pre_ping=True,
            )
        return self._engine

    def ping(self) -> bool:
        """Health check."""
        try:
            with self.engine.connect() as conn:
                result = conn.execute(text("SELECT 1"))
                return result.scalar() == 1
        except Exception:
            logger.exception("PostgreSQL ping failed")
            return False

    # ============================================================
    # Trade Records
    # ============================================================

    def insert_trade_record(self, trade: dict) -> int:
        """Insert a single trade record. Returns the new row ID."""
        with self.engine.connect() as conn:
            result = conn.execute(
                text("""
                    INSERT INTO trade_records
                        (ts_code, direction, price, volume, amount,
                         commission, stamp_tax, trade_time, strategy_name,
                         signal_reason, order_id)
                    VALUES
                        (:ts_code, :direction, :price, :volume, :amount,
                         :commission, :stamp_tax, :trade_time, :strategy_name,
                         :signal_reason, :order_id)
                    RETURNING id
                """),
                {
                    "ts_code": trade["ts_code"],
                    "direction": trade["direction"],
                    "price": trade["price"],
                    "volume": trade["volume"],
                    "amount": trade["amount"],
                    "commission": trade.get("commission", 0),
                    "stamp_tax": trade.get("stamp_tax", 0),
                    "trade_time": trade.get("trade_time", datetime.now()),
                    "strategy_name": trade["strategy_name"],
                    "signal_reason": trade.get("signal_reason"),
                    "order_id": trade.get("order_id"),
                },
            )
            conn.commit()
            return result.scalar_one()

    def get_trades_by_date(
        self,
        start: date,
        end: Optional[date] = None,
    ) -> list[dict]:
        """Query trade records within a date range."""
        end = end or start
        with self.engine.connect() as conn:
            result = conn.execute(
                text("""
                    SELECT * FROM trade_records
                    WHERE trade_time::date >= :start
                      AND trade_time::date <= :end
                    ORDER BY trade_time ASC
                """),
                {"start": start.isoformat(), "end": end.isoformat()},
            )
            return [dict(row._mapping) for row in result]

    # ============================================================
    # Daily P&L
    # ============================================================

    def upsert_daily_pnl(self, pnl: dict):
        """Insert or update daily P&L snapshot."""
        import json
        with self.engine.connect() as conn:
            conn.execute(
                text("""
                    INSERT INTO daily_pnl
                        (trade_date, total_value, cash, market_value,
                         daily_pnl, daily_return, cumulative_pnl, positions_json)
                    VALUES
                        (:trade_date, :total_value, :cash, :market_value,
                         :daily_pnl, :daily_return, :cumulative_pnl, :positions_json)
                    ON CONFLICT (trade_date)
                    DO UPDATE SET
                        total_value = EXCLUDED.total_value,
                        cash = EXCLUDED.cash,
                        market_value = EXCLUDED.market_value,
                        daily_pnl = EXCLUDED.daily_pnl,
                        daily_return = EXCLUDED.daily_return,
                        cumulative_pnl = EXCLUDED.cumulative_pnl,
                        positions_json = EXCLUDED.positions_json
                """),
                {
                    "trade_date": pnl["trade_date"],
                    "total_value": pnl["total_value"],
                    "cash": pnl["cash"],
                    "market_value": pnl["market_value"],
                    "daily_pnl": pnl["daily_pnl"],
                    "daily_return": pnl["daily_return"],
                    "cumulative_pnl": pnl["cumulative_pnl"],
                    "positions_json": json.dumps(pnl.get("positions", [])),
                },
            )
            conn.commit()

    def get_pnl_history(self, start: date, end: date) -> list[dict]:
        """Query P&L history within a date range."""
        with self.engine.connect() as conn:
            result = conn.execute(
                text("""
                    SELECT * FROM daily_pnl
                    WHERE trade_date >= :start AND trade_date <= :end
                    ORDER BY trade_date ASC
                """),
                {"start": start.isoformat(), "end": end.isoformat()},
            )
            return [dict(row._mapping) for row in result]

    # ============================================================
    # Risk Events
    # ============================================================

    def log_risk_event(
        self,
        event_type: str,
        severity: str,
        detail: str,
        ts_code: Optional[str] = None,
    ) -> int:
        """Log a risk event. Returns the new row ID."""
        with self.engine.connect() as conn:
            result = conn.execute(
                text("""
                    INSERT INTO risk_events (event_type, severity, ts_code, detail)
                    VALUES (:event_type, :severity, :ts_code, :detail)
                    RETURNING id
                """),
                {
                    "event_type": event_type,
                    "severity": severity,
                    "ts_code": ts_code,
                    "detail": detail,
                },
            )
            conn.commit()
            return result.scalar_one()

    def get_unresolved_risk_events(self) -> list[dict]:
        """Get all unresolved risk events."""
        with self.engine.connect() as conn:
            result = conn.execute(
                text("""
                    SELECT * FROM risk_events
                    WHERE resolved = FALSE
                    ORDER BY event_time DESC
                """),
            )
            return [dict(row._mapping) for row in result]

    # ============================================================
    # Strategy Signals
    # ============================================================

    def insert_signal(self, signal: dict) -> int:
        """Insert a strategy signal."""
        import json
        with self.engine.connect() as conn:
            result = conn.execute(
                text("""
                    INSERT INTO strategy_signals
                        (strategy_name, ts_code, signal_type, confidence, factor_values)
                    VALUES
                        (:strategy_name, :ts_code, :signal_type, :confidence,
                         :factor_values)
                    RETURNING id
                """),
                {
                    "strategy_name": signal["strategy_name"],
                    "ts_code": signal["ts_code"],
                    "signal_type": signal["signal_type"],
                    "confidence": signal.get("confidence"),
                    "factor_values": json.dumps(signal.get("factor_values", {})),
                },
            )
            conn.commit()
            return result.scalar_one()

    def close(self):
        """Dispose the engine."""
        if self._engine:
            self._engine.dispose()
            self._engine = None


# Singleton
_client: Optional[PostgresClient] = None


def get_postgres_client() -> PostgresClient:
    """Get or create the singleton PostgresClient."""
    global _client
    if _client is None:
        _client = PostgresClient()
    return _client
