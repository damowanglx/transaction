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
from pathlib import Path
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


class _QMTTradeCallback:
    """Callback handler for QMT trading events."""
    def on_connected(self): logger.info("QMT trade: connected")
    def on_disconnected(self): logger.warning("QMT trade: disconnected")
    def on_account_status(self, status): logger.info("QMT account status: %s", status)
    def on_stock_order(self, order): logger.info("QMT order update: %s", order)
    def on_stock_trade(self, trade): logger.info("QMT trade fill: %s", trade)
    def on_order_error(self, error): logger.error("QMT order error: %s", error)
    def on_cancel_error(self, error): logger.error("QMT cancel error: %s", error)
    def on_order_stock_async_response(self, resp): logger.info("QMT order response: %s", resp)
    def on_cancel_order_stock_async_response(self, resp): logger.info("QMT cancel response: %s", resp)
    def on_smt_appointment_async_response(self, resp): pass


class QMTTradeAdapter:
    """Order execution via QMT xttrader.

    Requires:
    1. QMT client running and logged in (行情+交易 mode)
    2. Account connected in QMT
    """

    def __init__(self, account_id: str = ""):
        self._account_id = account_id
        self._account = None  # xttype.StockAccount object
        self._connected = False
        self._trader = None
        if QMT_AVAILABLE:
            self._connect(account_id)

    def _connect(self, account_id: str):
        """Connect to QMT xttrader via XtQuantTrader."""
        import time
        try:
            from xtquant import xttrader, xttype

            # Find QMT userdata path
            path_candidates = [
                r"D:\国金QMT\国金证券QMT交易端\userdata_mini",
                r"D:\国金证券QMT交易端\userdata_mini",
                r"D:\国金QMT\国金证券QMT交易端\userdata",
            ]
            path = next((p for p in path_candidates if Path(p).exists()), "")

            if not path:
                logger.error("QMT userdata directory not found")
                return

            self._callback = _QMTTradeCallback()
            self._trader = xttrader.XtQuantTrader(path, 0, self._callback)
            self._trader.start()
            time.sleep(1)
            result = self._trader.connect()

            if result == 0:
                self._connected = True
                logger.info("QMT trade session initialized: %s", path)

                # Query account info
                accounts = self._trader.query_account_infos()
                if accounts and len(accounts) > 0:
                    if account_id:
                        matching = [a for a in accounts if a.account_id == account_id]
                        self._account = matching[0] if matching else accounts[0]
                    else:
                        self._account = accounts[0]
                    logger.info("QMT account: %s (type=%s)", self._account.account_id, self._account.account_type)
                else:
                    logger.warning("No trading accounts found in QMT")
            else:
                logger.error("QMT xttrader connect() returned %s", result)

            # Release old event loop if in main thread
            if hasattr(self._trader, 'oldloop'):
                import asyncio
                from threading import current_thread
                if current_thread().name == "MainThread":
                    try:
                        asyncio.set_event_loop(self._trader.oldloop)
                    except Exception:
                        pass

        except Exception:
            logger.exception("Failed to initialize QMT xttrader")

    @property
    def is_connected(self) -> bool:
        return self._connected

    def is_available(self) -> bool:
        return QMT_AVAILABLE and self._connected

    def buy(
        self,
        stock: str,
        price: float,
        volume: int,
    ) -> Optional[int]:
        """Submit a buy order (limit order by default).

        Args:
            stock: Stock code e.g. '600000.SH'
            price: Limit price
            volume: Shares (must be multiple of 100)

        Returns:
            Order ID if successful, None if failed.
        """
        if not self._connected or self._trader is None:
            logger.error("QMT trade not connected")
            return None

        try:
            from xtquant import xtconstant as c
            # xttrader.order_stock: order_type 23=买, 24=卖
            seq = self._trader.order_stock(
                self._account, stock, 23, volume, 0, price, "mean_revert", "auto"
            )
            logger.info("BUY %s %d@%.2f → seq=%s", stock, volume, price, seq)
            return seq
        except Exception:
            logger.exception("Buy order failed: %s", stock)
            return None

    def sell(
        self,
        stock: str,
        price: float,
        volume: int,
    ) -> Optional[int]:
        """Submit a sell order (limit order by default)."""
        if not self._connected or self._trader is None:
            logger.error("QMT trade not connected")
            return None

        try:
            seq = self._trader.order_stock(
                self._account, stock, 24, volume, 0, price, "mean_revert", "auto"
            )
            logger.info("SELL %s %d@%.2f → seq=%s", stock, volume, price, seq)
            return seq
        except Exception:
            logger.exception("Sell order failed: %s", stock)
            return None

    def cancel_order(self, order_id: int) -> bool:
        """Cancel a pending order."""
        if not self._trader: return False
        try:
            result = self._trader.cancel_order_stock(self._account, order_id)
            logger.info("Cancel order %s: result=%s", order_id, result)
            return result == 0
        except Exception as e:
            logger.error("Cancel failed %s: %s", order_id, e)
            return False

    def query_positions(self) -> list[dict]:
        """Query current positions from broker."""
        if not self._trader: return []
        try:
            positions = self._trader.query_stock_positions(self._account)
            logger.info("Positions queried: %d holdings", len(positions) if positions else 0)
            return positions if positions else []
        except Exception as e:
            logger.error("Query positions failed: %s", e)
            return []

    def query_orders(self) -> list[dict]:
        """Query today's orders from broker."""
        if not self._trader: return []
        try:
            orders = self._trader.query_stock_orders(self._account)
            logger.info("Orders queried: %d orders", len(orders) if orders else 0)
            return orders if orders else []
        except Exception as e:
            logger.error("Query orders failed: %s", e)
            return []

    def query_asset(self) -> Optional[dict]:
        """Query account asset (cash, market value, total)."""
        if not self._trader: return None
        try:
            asset = self._trader.query_stock_asset(self._account)
            if asset:
                logger.info("Asset: total=%.0f cash=%.0f mv=%.0f",
                           getattr(asset, 'total_asset', 0),
                           getattr(asset, 'cash', 0),
                           getattr(asset, 'market_value', 0))
            return asset
        except Exception as e:
            logger.error("Query asset failed: %s", e)
            return None


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
