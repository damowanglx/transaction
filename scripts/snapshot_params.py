#!/usr/bin/env python
"""Strategy parameter snapshot — save and compare parameter sets over time.

Usage:
    python scripts/snapshot_params.py save     # Save current params
    python scripts/snapshot_params.py list     # List all snapshots
    python scripts/snapshot_params.py diff A B # Compare two snapshots
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json
from datetime import datetime
from config.settings import atomic_write_json

SNAPSHOT_DIR = Path(__file__).resolve().parent.parent / "params_history"
DEFAULT_PARAMS = {
    "mean_revert": {
        "bb_period": 23, "bb_std": 3.0, "rsi_oversold": 26, "rsi_overbought": 65,
        "stop_loss": 0.05, "take_profit": 0.10, "top_n": 10,
        "min_price": 5.0, "min_turnover": 1.0, "atr_mult": 2.0,
        "use_atr_stop": True, "use_vol_target": True, "green_candle": True,
    },
    "risk": {
        "max_single_position_pct": 0.20, "max_total_position_pct": 0.80,
        "max_holdings_count": 10, "stop_loss_pct": 0.05,
        "max_daily_loss_pct": 0.02, "max_consecutive_loss_days": 3,
    },
}


def save_snapshot(name: str = ""):
    """Save current parameter snapshot with timestamp."""
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    label = f"{ts}_{name}" if name else ts
    filepath = SNAPSHOT_DIR / f"{label}.json"
    atomic_write_json(str(filepath), DEFAULT_PARAMS, indent=2, ensure_ascii=False)
    print(f"Snapshot saved: {filepath}")


def list_snapshots():
    """List all saved parameter snapshots."""
    if not SNAPSHOT_DIR.exists():
        print("No snapshots found")
        return []
    files = sorted(SNAPSHOT_DIR.glob("*.json"))
    for f in files:
        print(f"  {f.stem}")
    return files


def diff_snapshots(a: str, b: str):
    """Compare two snapshots and show differences."""
    def load(fp):
        return json.loads(Path(fp).read_text())
    d1 = load(SNAPSHOT_DIR / f"{a}.json") if not "/" in a else load(a)
    d2 = load(SNAPSHOT_DIR / f"{b}.json") if not "/" in b else load(b)

    for section in d1:
        if section not in d2:
            print(f"  Section '{section}': only in first")
            continue
        for key, v1 in d1[section].items():
            v2 = d2[section].get(key)
            if v1 != v2:
                print(f"  {section}.{key}: {v1} → {v2}")


if __name__ == "__main__":
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    cmd = sys.argv[1] if len(sys.argv) > 1 else "save"
    if cmd == "save":
        save_snapshot(sys.argv[2] if len(sys.argv) > 2 else "")
    elif cmd == "list":
        list_snapshots()
    elif cmd == "diff":
        diff_snapshots(sys.argv[2], sys.argv[3])
    else:
        print(f"Usage: {__file__} save|list|diff [args]")
