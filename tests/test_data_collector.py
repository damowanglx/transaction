"""
Tests for data collection pipeline (Phase 1).

Verifies:
- AkShare fetcher returns well-formed data
- Cleaner removes invalid records
- ClickHouse client connects and stores data
- PostgreSQL client stores business data

Run: pytest tests/test_data_collector.py -v
"""

import sys
from datetime import date, datetime, timedelta

import pytest

sys.path.insert(0, ".")


# ============================================================
# Test: DailyBar model
# ============================================================

class TestDailyBar:
    """DailyBar data model properties."""

    def test_create_bar(self):
        from data.models.bar import DailyBar

        bar = DailyBar(
            ts_code="600000.SH",
            trade_date=date(2024, 6, 15),
            open=10.0,
            high=10.5,
            low=9.8,
            close=10.3,
            pre_close=10.1,
            change=0.2,
            pct_chg=1.98,
            vol=5000000,
            amount=51000000,
            turnover_rate=2.5,
            pe=6.5,
            pb=0.8,
            is_st=0,
        )
        assert bar.ts_code == "600000.SH"
        assert bar.is_up is True
        assert bar.daily_range == pytest.approx(0.7)
        assert bar.body_pct == pytest.approx(0.3 / 0.7)
        assert bar.is_limit_up is False
        assert bar.is_limit_down is False

    def test_bar_immutable(self):
        from data.models.bar import DailyBar

        bar = DailyBar(
            ts_code="000001.SZ",
            trade_date=date(2024, 1, 1),
            open=5.0,
            high=5.0,
            low=5.0,
            close=5.0,
            pre_close=5.0,
            change=0.0,
            pct_chg=0.0,
            vol=100,
            amount=500,
        )
        with pytest.raises(Exception):
            bar.close = 10.0  # type: ignore

    def test_limit_up_detection(self):
        from data.models.bar import DailyBar

        bar = DailyBar(
            ts_code="000001.SZ",
            trade_date=date(2024, 1, 1),
            open=10.0,
            high=11.0,
            low=10.0,
            close=11.0,
            pre_close=10.0,
            change=1.0,
            pct_chg=10.0,
            vol=100000,
            amount=1100000,
        )
        assert bar.is_limit_up is True

    def test_body_pct_zero_range(self):
        from data.models.bar import DailyBar

        bar = DailyBar(
            ts_code="000001.SZ",
            trade_date=date(2024, 1, 1),
            open=5.0,
            high=5.0,
            low=5.0,
            close=5.0,
            pre_close=5.0,
            change=0.0,
            pct_chg=0.0,
            vol=100,
            amount=500,
        )
        assert bar.body_pct == 0.0


# ============================================================
# Test: AkShare Fetcher (integration — requires network)
# ============================================================

class TestAkShareFetcher:
    """Integration tests for AkShare data fetcher."""

    @pytest.mark.integration
    def test_fetch_stock_list(self):
        """AkShare should return a non-empty stock list."""
        from data.collector.akshare_fetcher import fetch_stock_list

        stocks = fetch_stock_list()
        assert len(stocks) > 1000, f"Expected >1000 stocks, got {len(stocks)}"
        sample = stocks[0]
        assert "ts_code" in sample
        assert "name" in sample
        assert len(sample["ts_code"]) == 6

    @pytest.mark.integration
    def test_fetch_single_stock_history(self):
        """Fetch known stock history — should return days of data."""
        from data.collector.akshare_fetcher import fetch_stock_daily_hist

        bars = fetch_stock_daily_hist(
            ts_code="000001",
            start_date="20240101",
            end_date="20240131",
            adjust="qfq",
        )
        assert len(bars) > 0, "Should have trading days in Jan 2024"
        bar = bars[0]
        assert bar["ts_code"] == "000001.SZ"
        assert bar["open"] > 0
        assert bar["high"] >= bar["low"]
        assert bar["close"] > 0
        assert bar["vol"] >= 0

    @pytest.mark.integration
    def test_fetch_stock_info(self):
        """Fetch individual stock info from East Money."""
        from data.collector.akshare_fetcher import fetch_stock_individual_info

        info = fetch_stock_individual_info("000001")
        if info is not None:
            assert "ts_code" in info
            assert "name" in info
            assert info["ts_code"] == "000001.SZ"


# ============================================================
# Test: Cleaner
# ============================================================

class TestCleaner:
    """Data cleaner logic tests."""

    def test_clean_valid_records_passthrough(self):
        from data.collector.cleaner import clean_daily_bars

        records = [
            {
                "ts_code": "000001.SZ",
                "trade_date": date(2024, 6, 15),
                "open": 10.0, "high": 11.0, "low": 9.5, "close": 10.5,
                "pre_close": 10.0, "change": 0.5, "pct_chg": 5.0,
                "vol": 1000000, "amount": 10500000,
            },
        ]
        cleaned = clean_daily_bars(records)
        assert len(cleaned) == 1

    def test_drop_zero_prices(self):
        from data.collector.cleaner import clean_daily_bars

        records = [
            {
                "ts_code": "000001.SZ",
                "trade_date": date(2024, 6, 15),
                "open": 0.0, "high": 0.0, "low": 0.0, "close": 0.0,
                "pre_close": 10.0, "change": 0.0, "pct_chg": 0.0,
                "vol": 1000, "amount": 0,
            },
        ]
        cleaned = clean_daily_bars(records)
        assert len(cleaned) == 0

    def test_drop_crossed_high_low(self):
        from data.collector.cleaner import clean_daily_bars

        records = [
            {
                "ts_code": "000001.SZ",
                "trade_date": date(2024, 6, 15),
                "open": 10.0, "high": 9.0, "low": 11.0, "close": 10.0,
                "pre_close": 10.0, "change": 0.0, "pct_chg": 0.0,
                "vol": 1000, "amount": 10000,
            },
        ]
        cleaned = clean_daily_bars(records)
        assert len(cleaned) == 0

    def test_drop_missing_fields(self):
        from data.collector.cleaner import clean_daily_bars

        records = [
            {"ts_code": "000001.SZ", "trade_date": date(2024, 6, 15)},
        ]
        cleaned = clean_daily_bars(records)
        assert len(cleaned) == 0

    def test_dedup_keeps_last(self):
        from data.collector.cleaner import clean_daily_bars

        records = [
            {
                "ts_code": "000001.SZ",
                "trade_date": date(2024, 6, 15),
                "open": 10.0, "high": 11.0, "low": 9.5, "close": 10.5,
                "pre_close": 10.0, "change": 0.5, "pct_chg": 5.0,
                "vol": 1000, "amount": 10500,
            },
            {
                "ts_code": "000001.SZ",
                "trade_date": date(2024, 6, 15),
                "open": 10.0, "high": 11.0, "low": 9.5, "close": 10.8,
                "pre_close": 10.0, "change": 0.8, "pct_chg": 8.0,
                "vol": 2000, "amount": 21600,
            },
        ]
        cleaned = clean_daily_bars(records)
        assert len(cleaned) == 1
        assert cleaned[0]["close"] == 10.8

    def test_drop_zero_volume(self):
        from data.collector.cleaner import clean_daily_bars

        records = [
            {
                "ts_code": "000001.SZ",
                "trade_date": date(2024, 6, 15),
                "open": 10.0, "high": 11.0, "low": 9.5, "close": 10.5,
                "pre_close": 10.0, "change": 0.5, "pct_chg": 5.0,
                "vol": 0, "amount": 0,
            },
        ]
        cleaned = clean_daily_bars(records)
        assert len(cleaned) == 0


# ============================================================
# Test: Risk Configuration
# ============================================================

class TestRiskConfig:
    """Risk parameter configuration tests."""

    def test_default_config(self):
        from config.risk_params import DEFAULT_RISK_CONFIG

        assert DEFAULT_RISK_CONFIG.max_single_position_pct == 0.20
        assert DEFAULT_RISK_CONFIG.max_daily_loss_pct == 0.02
        assert DEFAULT_RISK_CONFIG.exclude_st is True

    def test_config_immutable(self):
        from config.risk_params import DEFAULT_RISK_CONFIG

        with pytest.raises(Exception):
            DEFAULT_RISK_CONFIG.max_single_position_pct = 0.50  # type: ignore

    def test_conservative_config(self):
        from config.risk_params import CONSERVATIVE_RISK_CONFIG

        assert CONSERVATIVE_RISK_CONFIG.max_single_position_pct == 0.10
        assert CONSERVATIVE_RISK_CONFIG.max_total_position_pct == 0.50
        assert CONSERVATIVE_RISK_CONFIG.stop_loss_pct == 0.03

    def test_get_risk_config(self):
        from config.risk_params import get_risk_config

        default = get_risk_config("default")
        conservative = get_risk_config("conservative")
        assert default.max_single_position_pct == 0.20
        assert conservative.max_single_position_pct == 0.10

    def test_get_risk_config_invalid_mode(self):
        from config.risk_params import get_risk_config

        with pytest.raises(ValueError, match="Unknown risk mode"):
            get_risk_config("aggressive")


# ============================================================
# Test: ClickHouse Client (requires running ClickHouse)
# ============================================================

class TestClickHouseClient:
    """ClickHouse client tests (requires docker compose up)."""

    @pytest.mark.integration
    def test_ping(self):
        from data.storage.clickhouse_client import get_clickhouse_client

        ch = get_clickhouse_client()
        assert ch.ping() is True

    @pytest.mark.integration
    def test_insert_and_query_bars(self):
        from data.storage.clickhouse_client import get_clickhouse_client

        ch = get_clickhouse_client()
        records = [
            {
                "ts_code": "TEST01.SH",
                "trade_date": date(2024, 6, 15),
                "open": 10.0, "high": 11.0, "low": 9.5, "close": 10.5,
                "pre_close": 10.0, "change": 0.5, "pct_chg": 5.0,
                "vol": 1000000, "amount": 10500000, "turnover_rate": 3.0,
                "pe": 15.0, "pb": 1.5, "is_st": 0,
            },
        ]
        # Insert
        n = ch.insert_daily_bars(records)
        assert n == 1

        # Query back
        df = ch.get_bars("TEST01.SH", date(2024, 6, 1), date(2024, 6, 30))
        assert len(df) == 1
        assert df.iloc[0]["close"] == 10.5

        # Cleanup
        ch.client.command("ALTER TABLE daily_bars DELETE WHERE ts_code = 'TEST01.SH'")


# ============================================================
# Test: PostgreSQL Client (requires running PostgreSQL)
# ============================================================

class TestPostgresClient:
    """PostgreSQL client tests (requires docker compose up)."""

    @pytest.mark.integration
    def test_ping(self):
        from data.storage.postgres_client import get_postgres_client

        pg = get_postgres_client()
        assert pg.ping() is True

    @pytest.mark.integration
    def test_insert_and_query_trade(self):
        from data.storage.postgres_client import get_postgres_client

        pg = get_postgres_client()

        trade = {
            "ts_code": "000001.SZ",
            "direction": "BUY",
            "price": 10.50,
            "volume": 1000,
            "amount": 10500.00,
            "commission": 3.15,
            "stamp_tax": 0.0,
            "strategy_name": "test_strategy",
            "signal_reason": "Test trade",
        }
        trade_id = pg.insert_trade_record(trade)
        assert trade_id > 0

        # Query back
        trades = pg.get_trades_by_date(date.today())
        assert len(trades) >= 1

        # Cleanup
        from sqlalchemy import text
        with pg.engine.connect() as conn:
            conn.execute(text(f"DELETE FROM trade_records WHERE id = {trade_id}"))
            conn.commit()

    @pytest.mark.integration
    def test_log_risk_event(self):
        from data.storage.postgres_client import get_postgres_client

        pg = get_postgres_client()
        event_id = pg.log_risk_event(
            event_type="TEST_EVENT",
            severity="WARN",
            detail="Test risk event from pytest",
            ts_code="000001.SZ",
        )
        assert event_id > 0

        # Cleanup
        from sqlalchemy import text
        with pg.engine.connect() as conn:
            conn.execute(text(f"DELETE FROM risk_events WHERE id = {event_id}"))
            conn.commit()
