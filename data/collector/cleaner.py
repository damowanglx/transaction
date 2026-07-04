"""
Data cleaning pipeline for A-Share market data.

Handles:
- Missing value imputation
- Price/volume sanity checks
- ST stock flagging
- Duplicate row detection
- Suspicious value filtering
"""

import logging
from collections import defaultdict

logger = logging.getLogger(__name__)


def clean_daily_bars(records: list[dict]) -> list[dict]:
    """
    Clean a batch of daily bar records.

    Filters out invalid data points and enriches records with computed fields.

    Args:
        records: Raw records from AkShare fetcher.

    Returns:
        Cleaned, validated records ready for storage.
    """
    clean = []

    for r in records:
        # ---- Filter: Missing essential fields ----
        if not all(k in r for k in ("open", "high", "low", "close", "vol")):
            logger.debug("Dropping record for %s: missing essential fields", r.get("ts_code", "?"))
            continue

        # ---- Filter: Zero or negative prices ----
        if r["open"] <= 0 or r["high"] <= 0 or r["low"] <= 0 or r["close"] <= 0:
            logger.debug("Dropping record for %s on %s: zero/negative price", r.get("ts_code"), r.get("trade_date"))
            continue

        # ---- Filter: Low > High (crossed) ----
        if r["low"] > r["high"]:
            logger.debug("Dropping record for %s on %s: low > high", r.get("ts_code"), r.get("trade_date"))
            continue

        # ---- Filter: Suspect huge single-day moves (>30% without ST prefix) ----
        if abs(r.get("pct_chg", 0)) > 30 and r.get("is_st", 0) == 0:
            logger.debug("Dropping record for %s on %s: suspicious pct_chg=%.2f", r.get("ts_code"), r.get("trade_date"), r["pct_chg"])
            continue

        # ---- Filter: Zero volume (suspended or illiquid) ----
        if r["vol"] <= 0:
            continue

        # ---- Enrich: Fill missing PE/PB with None ----
        r.setdefault("pe", None)
        r.setdefault("pb", None)
        r.setdefault("turnover_rate", 0.0)
        r.setdefault("is_st", 0)

        clean.append(r)

    # ---- Dedup: Keep last occurrence per (ts_code, trade_date) ----
    seen: dict[tuple, int] = {}
    for i, r in enumerate(clean):
        key = (r["ts_code"], r["trade_date"])
        seen[key] = i

    deduped = [r for i, r in enumerate(clean) if seen[(r["ts_code"], r["trade_date"])] == i]

    dropped = len(records) - len(deduped)
    if dropped > 0:
        logger.info("Cleaned %d records: dropped %d invalid/duplicate", len(clean), dropped)

    return deduped
