"""
AkShare-based market data fetcher.

Fetches daily K-line data, stock basic info, and trading calendar
from free public APIs via akshare.

Rate-limited to avoid IP bans. All functions return lists of dicts
suitable for direct ClickHouse insertion.
"""

import logging
import time
from datetime import date, timedelta
from typing import Optional

import akshare as ak
import pandas as pd

from config.settings import AKSHARE_REQUEST_DELAY

logger = logging.getLogger(__name__)


def _sleep():
    """Sleep to avoid rate limiting."""
    time.sleep(AKSHARE_REQUEST_DELAY)


def _safe_date(d) -> Optional[date]:
    """Convert various date formats to date, return None on failure."""
    if d is None:
        return None
    if isinstance(d, date):
        return d
    if isinstance(d, pd.Timestamp):
        return d.date()
    try:
        return pd.Timestamp(d).date()
    except Exception:
        return None


def _safe_float(val, default=0.0) -> float:
    """Convert val to float, returning default for NaN/inf/non-numeric."""
    try:
        result = float(val)
        if pd.isna(result) or result == float("inf") or result == float("-inf"):
            return default
        return result
    except (ValueError, TypeError):
        return default


def fetch_stock_list() -> list[dict]:
    """
    Fetch full A-share stock list with basic info.

    Returns:
        List of dicts with keys: ts_code, name, area, industry, market, list_date
    """
    logger.info("Fetching A-share stock list...")
    try:
        # akshare stock_info_a_code_name returns basic stock info
        df = ak.stock_info_a_code_name()
        _sleep()

        records = []
        for _, row in df.iterrows():
            record = {
                "ts_code": str(row.get("code", "")).strip(),
                "name": str(row.get("name", "")).strip(),
            }
            # stock_info_a_code_name doesn't return industry/area/market
            # We'll fill those later with stock_individual_info_em
            records.append(record)

        logger.info("Fetched %d stock codes", len(records))
        return records
    except Exception:
        logger.exception("Failed to fetch stock list")
        return []


def fetch_stock_daily_hist(
    ts_code: str,
    start_date: str,
    end_date: str,
    adjust: str = "qfq",
) -> list[dict]:
    """
    Fetch daily K-line history for a single stock.

    Uses akshare stock_zh_a_hist which returns:
    - 日期, 开盘, 最高, 最低, 收盘, 前收盘, 涨跌额, 涨跌幅
    - 成交量, 成交额, 换手率, 市盈率-动态, 市净率

    Args:
        ts_code: Stock code like '600000' (no suffix)
        start_date: 'YYYYMMDD' format
        end_date: 'YYYYMMDD' format
        adjust: 'qfq' (前复权), 'hfq' (后复权), '' (不复权)

    Returns:
        List of dicts matching daily_bars schema
    """
    logger.debug("Fetching %s from %s to %s", ts_code, start_date, end_date)
    try:
        df = ak.stock_zh_a_hist(
            symbol=ts_code,
            period="daily",
            start_date=start_date,
            end_date=end_date,
            adjust=adjust,
        )
        _sleep()

        if df.empty:
            return []

        records = []
        for _, row in df.iterrows():
            trade_date = _safe_date(row.get("日期"))
            if trade_date is None:
                continue
            record = {
                "ts_code": f"{ts_code}.{'SH' if ts_code.startswith(('6', '9')) else 'SZ'}",
                "trade_date": trade_date,
                "open": _safe_float(row.get("开盘")),
                "high": _safe_float(row.get("最高")),
                "low": _safe_float(row.get("最低")),
                "close": _safe_float(row.get("收盘")),
                "pre_close": _safe_float(row.get(("前收盘" if adjust else "昨收"))),
                "change": _safe_float(row.get("涨跌额")),
                "pct_chg": _safe_float(row.get("涨跌幅")),
                "vol": _safe_float(row.get("成交量")),
                "amount": _safe_float(row.get("成交额")),
                "turnover_rate": _safe_float(row.get("换手率")),
                "pe": _safe_float(row.get("市盈率-动态"), -1) if _safe_float(row.get("市盈率-动态"), -1) != -1 else None,
                "pb": _safe_float(row.get("市净率"), -1) if _safe_float(row.get("市净率"), -1) != -1 else None,
                "is_st": 1 if "ST" in str(row.get("股票名称", "")) or "*ST" in str(row.get("股票名称", "")) else 0,
            }
            records.append(record)

        logger.debug("Fetched %d daily bars for %s", len(records), ts_code)
        return records
    except Exception:
        logger.warning("Failed to fetch %s: continuing", ts_code, exc_info=True)
        return []


def fetch_all_stocks_daily(
    stock_list: list[str],
    start_date: date,
    end_date: date,
    adjust: str = "qfq",
    max_stocks: int = 0,
) -> tuple[int, int]:
    """
    Fetch daily history for all stocks in the list.

    Args:
        stock_list: List of stock codes (6 digits, no suffix)
        start_date: Start date
        end_date: End date
        adjust: 'qfq' | 'hfq' | ''
        max_stocks: 0 = all, >0 = limit (for testing)

    Returns:
        (total_stocks_processed, total_bars_collected)
    """
    from data.storage.clickhouse_client import get_clickhouse_client

    ch = get_clickhouse_client()
    total_bars = 0
    processed = 0

    codes = stock_list[:max_stocks] if max_stocks > 0 else stock_list
    start_str = start_date.strftime("%Y%m%d")
    end_str = end_date.strftime("%Y%m%d")

    logger.info(
        "Starting bulk fetch: %d stocks, %s to %s",
        len(codes), start_str, end_str,
    )

    for i, code in enumerate(codes):
        try:
            bars = fetch_stock_daily_hist(code, start_str, end_str, adjust)
            n = ch.insert_daily_bars(bars)
            total_bars += n
            processed += 1

            if (i + 1) % 100 == 0:
                logger.info("Progress: %d/%d stocks, %d bars inserted", i + 1, len(codes), total_bars)
        except Exception:
            logger.warning("Error processing %s, skipped", code)
            _sleep()
    return processed, total_bars


def fetch_stock_individual_info(ts_code: str) -> Optional[dict]:
    """
    Fetch detailed info for a single stock (industry, area, listing date, etc.).

    Uses akshare stock_individual_info_em (东方财富).
    """
    logger.debug("Fetching info for %s", ts_code)
    try:
        df = ak.stock_individual_info_em(symbol=ts_code)
        _sleep()

        info: dict = {}
        for _, row in df.iterrows():
            key = str(row.get("item", ""))
            val = str(row.get("value", ""))
            info[key] = val

        return {
            "ts_code": f"{ts_code}.{'SH' if ts_code.startswith(('6', '9')) else 'SZ'}",
            "name": info.get("股票简称", ""),
            "area": info.get("所属地域", ""),
            "industry": info.get("所属行业", ""),
            "market": "SH" if ts_code.startswith(("6", "9")) else "SZ",
            "list_date": _safe_date(info.get("上市时间")),
        }
    except Exception:
        logger.warning("Failed to fetch info for %s", ts_code, exc_info=True)
        return None


def get_trade_date_range(
    start_date: date,
    end_date: date,
) -> list[date]:
    """
    Get list of trading dates in a range using akshare trade calendar.
    Falls back to all weekdays if API fails.
    """
    try:
        df = ak.tool_trade_date_hist_sina()
        trade_dates = pd.to_datetime(df["trade_date"])
        result = []
        for td in trade_dates:
            d = td.date()
            if start_date <= d <= end_date:
                result.append(d)
        return result
    except Exception:
        logger.warning("Trade calendar fetch failed, using weekday fallback")
        # Fallback: all weekdays in range
        result = []
        current = start_date
        while current <= end_date:
            if current.weekday() < 5:  # Mon-Fri
                result.append(current)
            current += timedelta(days=1)
        return result
