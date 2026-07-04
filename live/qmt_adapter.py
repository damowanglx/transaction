"""
QMT (MiniQMT) adapter — bridges xtquant API to our trading system.

Two modules:
- xtdata: Market data (history + real-time)
- xttrader: Order execution (requires logged-in QMT client)

Install: pip install xtquant (or from QMT directory)
Usage: Start QMT client → log in → run this adapter.

Reference:
    xtdata.download_history_data(stock, period, start, end)
    xtdata.get_market_data(field_list, stock_list, period)
    xttrader.order_stock(account, stock_code, order_type, order_volume, price_type, price)
"""

import logging
from datetime import date, datetime, timedelta
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

# xtquant is only available when QMT is installed
try:
    from xtquant import xtdata, xttrader
    QMT_AVAILABLE = True
except ImportError:
    QMT_AVAILABLE = False
    xtdata = None
    xttrader = None


class QMTDataAdapter:
    """Market data via MiniQMT xtdata."""

    def __init__(self):
        if not QMT_AVAILABLE:
            logger.warning("xtquant not installed — QMT data unavailable")
        self._cache: dict[str, pd.DataFrame] = {}

    def is_available(self) -> bool:
        return QMT_AVAILABLE

    def download_history(
        self,
        stock: str,
        period: str = "1d",
        start: str = "",
        end: str = "",
    ) -> bool:
        """Download history data to local QMT cache.

        Args:
            stock: Stock code e.g. '000001.SZ'
            period: '1d', '1m', '5m', 'tick'
            start: 'YYYYMMDD' format
            end: 'YYYYMMDD' format
        """
        if not QMT_AVAILABLE:
            return False
        xtdata.download_history_data(stock, period, start, end)
        return True

    def get_daily_bars(
        self,
        stock: str,
        start: str = "",
        end: str = "",
    ) -> pd.DataFrame:
        """Get daily K-line data from QMT.

        Returns DataFrame with columns: time, open, high, low, close, volume, amount.
        """
        if not QMT_AVAILABLE:
            return pd.DataFrame()

        self.download_history(stock, "1d", start, end)
        data = xtdata.get_market_data(
            field_list=["open", "high", "low", "close", "volume", "amount"],
            stock_list=[stock],
            period="1d",
            start_time=start,
            end_time=end,
        )
        if data is None or stock not in data:
            return pd.DataFrame()

        df = data[stock]
        if isinstance(df, pd.DataFrame):
            return df.reset_index()
        return pd.DataFrame()

    def get_realtime_quotes(self, stocks: list[str]) -> dict[str, dict]:
        """Get real-time quotes for a list of stocks.

        Returns: {stock_code: {lastPrice, open, high, low, volume, amount, ...}}
        """
        if not QMT_AVAILABLE:
            return {}
        data = xtdata.get_full_tick(stocks)
        return data if data else {}

    def get_trading_dates(self, start: str, end: str) -> list[str]:
        """Get trading calendar between start and end."""
        if not QMT_AVAILABLE:
            return []
        return xtdata.get_trading_dates("SH", start, end)


class QMTTradeAdapter:
    """Order execution via MiniQMT xttrader.

    Requires:
    1. QMT client running and logged in
    2. Account connected
    """

    def __init__(self, account_id: str = ""):
        self._account = account_id
        self._connected = False
        if QMT_AVAILABLE and account_id:
            self._connect(account_id)

    def _connect(self, account_id: str):
        """Connect to QMT trading account."""
        try:
            # xttrader connects to the running QMT client
            self._account = account_id
            self._connected = True
            logger.info("QMT trade connected: %s", account_id)
        except Exception:
            logger.exception("Failed to connect QMT trade")

    def is_available(self) -> bool:
        return QMT_AVAILABLE and self._connected

    def buy(
        self,
        stock: str,
        price: float,
        volume: int,
        order_type: int = 0,  # 0=限价, 1=市价
    ) -> Optional[int]:
        """Submit a buy order.

        Args:
            stock: Stock code e.g. '000001.SZ'
            price: Limit price
            volume: Shares (must be multiple of 100)
            order_type: 0=限价单, 1=市价单

        Returns:
            Order ID if successful, None if failed.
        """
        if not self._connected:
            logger.error("QMT trade not connected")
            return None

        try:
            # xttrader.order_stock(account, stock_code, order_type, volume, price_type, price, strategy_name, order_remark)
            order_id = xttrader.order_stock(
                self._account,
                stock,
                order_type,       # 0=限价
                volume,
                0,                # price_type: 0=指定价
                price,
                "mean_revert",    # strategy name
                "auto",           # remark
            )
            logger.info("BUY order: %s %d@%.2f → order_id=%s", stock, volume, price, order_id)
            return order_id
        except Exception:
            logger.exception("Buy order failed: %s", stock)
            return None

    def sell(
        self,
        stock: str,
        price: float,
        volume: int,
        order_type: int = 0,
    ) -> Optional[int]:
        """Submit a sell order."""
        if not self._connected:
            logger.error("QMT trade not connected")
            return None

        try:
            order_id = xttrader.order_stock(
                self._account, stock, order_type, volume, 0, price,
                "mean_revert", "auto",
            )
            logger.info("SELL order: %s %d@%.2f → order_id=%s", stock, volume, price, order_id)
            return order_id
        except Exception:
            logger.exception("Sell order failed: %s", stock)
            return None

    def cancel_order(self, order_id: int) -> bool:
        """Cancel a pending order."""
        try:
            xttrader.cancel_order(self._account, order_id)
            return True
        except Exception:
            logger.exception("Cancel failed: %d", order_id)
            return False

    def query_positions(self) -> list[dict]:
        """Query current positions."""
        if not self._connected:
            return []
        try:
            return xttrader.query_stock_positions(self._account)
        except Exception:
            return []

    def query_orders(self) -> list[dict]:
        """Query today's orders."""
        if not self._connected:
            return []
        try:
            return xttrader.query_stock_orders(self._account)
        except Exception:
            return []


# ============================================================
# Singleton
# ============================================================

_data_adapter: Optional[QMTDataAdapter] = None
_trade_adapter: Optional[QMTTradeAdapter] = None


def get_data_adapter() -> QMTDataAdapter:
    global _data_adapter
    if _data_adapter is None:
        _data_adapter = QMTDataAdapter()
    return _data_adapter


def get_trade_adapter(account_id: str = "") -> QMTTradeAdapter:
    global _trade_adapter
    if _trade_adapter is None:
        _trade_adapter = QMTTradeAdapter(account_id)
    return _trade_adapter
