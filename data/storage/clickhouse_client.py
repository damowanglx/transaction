"""
ClickHouse client for K-line data storage and query.

Handles connection pooling, batch inserts, and common queries.
"""

import logging
from datetime import date
from typing import Optional

import clickhouse_connect
import pandas as pd

from config.settings import CLICKHOUSE_CONFIG

logger = logging.getLogger(__name__)


class ClickHouseClient:
    """ClickHouse client for time-series market data."""

    def __init__(self):
        self._client = None

    @property
    def client(self):
        """Lazy-initialize the ClickHouse connection."""
        if self._client is None:
            self._client = clickhouse_connect.get_client(
                host=CLICKHOUSE_CONFIG["host"],
                port=CLICKHOUSE_CONFIG["http_port"],
                username=CLICKHOUSE_CONFIG["user"],
                password=CLICKHOUSE_CONFIG["password"],
                database=CLICKHOUSE_CONFIG["database"],
            )
        return self._client

    def ping(self) -> bool:
        """Health check."""
        try:
            result = self.client.query("SELECT 1")
            return result.first_row[0] == 1
        except Exception:
            logger.exception("ClickHouse ping failed")
            return False

    # ============================================================
    # Daily Bars
    # ============================================================

    def insert_daily_bars(self, records: list[dict]) -> int:
        """Insert a batch of daily bar records into ClickHouse.

        Args:
            records: List of dicts matching daily_bars schema.

        Returns:
            Number of rows inserted.
        """
        if not records:
            return 0

        columns = [
            "ts_code", "trade_date", "open", "high", "low", "close",
            "pre_close", "change", "pct_chg", "vol", "amount", "turnover_rate",
            "pe", "pb", "is_st",
        ]
        data = []
        for r in records:
            row = []
            for col in columns:
                val = r.get(col)
                # Convert None to 0.0 for float columns (nullable floats in CH handle this)
                row.append(val)
            data.append(row)

        self.client.insert("daily_bars", data, column_names=columns)
        return len(data)

    def get_bars(
        self,
        ts_code: str,
        start_date: date,
        end_date: date,
    ) -> pd.DataFrame:
        """Query daily bars for a single stock within a date range."""
        query = """
            SELECT *
            FROM daily_bars
            WHERE ts_code = %(ts_code)s
              AND trade_date >= %(start_date)s
              AND trade_date <= %(end_date)s
            ORDER BY trade_date ASC
        """
        params = {
            "ts_code": ts_code,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        }
        return self.client.query_df(query, params)

    def get_multi_bars(
        self,
        ts_codes: list[str],
        trade_date: date,
    ) -> pd.DataFrame:
        """Query daily bars for multiple stocks on a single date."""
        query = """
            SELECT *
            FROM daily_bars
            WHERE ts_code IN (%(codes)s)
              AND trade_date = %(trade_date)s
        """
        params = {
            "codes": tuple(ts_codes),
            "trade_date": trade_date.isoformat(),
        }
        return self.client.query_df(query, params)

    def get_price_matrix(
        self,
        ts_codes: list[str],
        start_date: date,
        end_date: date,
    ) -> pd.DataFrame:
        """Return a pivot table: dates as rows, ts_codes as columns, close as values."""
        query = """
            SELECT ts_code, trade_date, close
            FROM daily_bars
            WHERE ts_code IN %(codes)s
              AND trade_date >= %(start_date)s
              AND trade_date <= %(end_date)s
            ORDER BY trade_date ASC
        """
        params = {
            "codes": tuple(ts_codes),
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        }
        df = self.client.query_df(query, params)
        if df.empty:
            return df
        return df.pivot(index="trade_date", columns="ts_code", values="close")

    def get_all_codes_on_date(self, trade_date: date) -> list[str]:
        """Get all stock codes that have data on a given date."""
        query = """
            SELECT DISTINCT ts_code
            FROM daily_bars
            WHERE trade_date = %(trade_date)s
              AND is_st = 0
        """
        result = self.client.query(query, parameters={"trade_date": trade_date.isoformat()})
        return [row[0] for row in result.result_rows]

    def get_dates_in_range(
        self,
        start_date: date,
        end_date: date,
    ) -> list[date]:
        """Get all distinct trading dates within a range."""
        query = """
            SELECT DISTINCT trade_date
            FROM daily_bars
            WHERE trade_date >= %(start_date)s
              AND trade_date <= %(end_date)s
            ORDER BY trade_date ASC
        """
        result = self.client.query(
            query,
            parameters={"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
        )
        return [row[0] for row in result.result_rows]

    # ============================================================
    # Minute Bars
    # ============================================================

    def insert_minute_bars(self, records: list[dict]) -> int:
        """Insert minute bars."""
        if not records:
            return 0
        columns = ["ts_code", "trade_time", "open", "high", "low", "close", "vol", "amount"]
        data = [[r.get(c) for c in columns] for r in records]
        self.client.insert("minute_bars", data, column_names=columns)
        return len(data)

    # ============================================================
    # Stock Info (dimension table)
    # ============================================================

    def upsert_stock_info(self, records: list[dict]) -> int:
        """Upsert stock basic info. ReplacingMergeTree handles dedup."""
        if not records:
            return 0
        columns = ["ts_code", "name", "area", "industry", "market", "list_date"]
        data = [[r.get(c) for c in columns] for r in records]
        self.client.insert("stock_info", data, column_names=columns)
        return len(data)

    def get_stock_info(self, ts_code: str) -> Optional[dict]:
        """Get basic info for a single stock."""
        query = """
            SELECT * FROM stock_info
            WHERE ts_code = %(ts_code)s
            ORDER BY updated_at DESC
            LIMIT 1
        """
        result = self.client.query(query, parameters={"ts_code": ts_code})
        if result.result_rows:
            return dict(zip(result.column_names, result.result_rows[0]))
        return None

    def close(self):
        """Close the client connection."""
        if self._client:
            self._client.close()
            self._client = None


# Singleton
_client: Optional[ClickHouseClient] = None


def get_clickhouse_client() -> ClickHouseClient:
    """Get or create the singleton ClickHouse client."""
    global _client
    if _client is None:
        _client = ClickHouseClient()
    return _client
