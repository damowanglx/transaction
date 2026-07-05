"""
APScheduler-based data collection scheduler.

Runs daily after market close (15:30 CST) to fetch the latest data.
Handles retries and error logging.
"""

import logging
from datetime import date, datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from data.collector.akshare_fetcher import (
    fetch_all_stocks_daily,
    fetch_stock_list,
)

logger = logging.getLogger(__name__)


class DataScheduler:
    """Manages scheduled data collection jobs."""

    def __init__(self):
        self._scheduler = BackgroundScheduler(
            timezone="Asia/Shanghai",
            job_defaults={
                "coalesce": True,        # Skip missed runs
                "max_instances": 1,       # One instance at a time
                "misfire_grace_time": 300,  # 5 min grace
            },
        )
        self._stock_cache: list[str] = []

    # ============================================================
    # Job definitions
    # ============================================================

    def _refresh_stock_list(self):
        """Periodically refresh the stock code list."""
        logger.info("Refreshing stock list...")
        try:
            stocks = fetch_stock_list()
            self._stock_cache = [s["ts_code"] for s in stocks]
            logger.info("Stock list refreshed: %d codes", len(self._stock_cache))
        except Exception:
            logger.exception("Failed to refresh stock list")

    def _collect_daily_after_close(self):
        """Collect today's daily bar data after market close."""
        logger.info("Starting daily collection after market close...")
        if not self._stock_cache:
            self._refresh_stock_list()
        if not self._stock_cache:
            logger.error("No stock codes available — cannot collect")
            return

        today = date.today()
        start = today - timedelta(days=3)  # Cover weekend gaps

        try:
            processed, bars = fetch_all_stocks_daily(
                stock_list=self._stock_cache,
                start_date=start,
                end_date=today,
            )
            logger.info(
                "Daily collection complete: %d stocks processed, %d bars inserted", processed, bars,
            )
        except Exception:
            logger.exception("Daily collection failed")

    # ============================================================
    # Lifecycle
    # ============================================================

    def start(self):
        """Start the scheduler and all jobs."""
        # Refresh stock list once at startup
        logger.info("Starting data scheduler...")
        self._refresh_stock_list()

        # Daily collection: 15:30 CST (Mon-Fri)
        self._scheduler.add_job(
            self._collect_daily_after_close,
            trigger=CronTrigger(day_of_week="mon-fri", hour=15, minute=30),
            id="collect_daily_bars",
            name="Collect daily bars after close",
            replace_existing=True,
        )

        # Weekly stock list refresh: Sunday 08:00
        self._scheduler.add_job(
            self._refresh_stock_list,
            trigger=CronTrigger(day_of_week="sun", hour=8, minute=0),
            id="refresh_stock_list",
            name="Refresh stock list weekly",
            replace_existing=True,
        )

        self._scheduler.start()
        logger.info("Data scheduler started with %d jobs", len(self._scheduler.get_jobs()))

    def stop(self):
        """Gracefully stop the scheduler."""
        logger.info("Stopping data scheduler...")
        self._scheduler.shutdown(wait=False)
        logger.info("Data scheduler stopped")

    @property
    def is_running(self) -> bool:
        """Check if scheduler is active."""
        return self._scheduler.running

    def run_now(self):
        """Trigger daily collection immediately (for testing/manual use)."""
        self._collect_daily_after_close()


# Global singleton
_scheduler: DataScheduler | None = None


def get_scheduler() -> DataScheduler:
    """Get or create the singleton data scheduler."""
    global _scheduler
    if _scheduler is None:
        _scheduler = DataScheduler()
    return _scheduler
