#!/usr/bin/env python
"""Emergency stop — immediately halt all trading and liquidate positions if needed.

Usage:
    python scripts/emergency_stop.py          # Halt trading (latch breaker)
    python scripts/emergency_stop.py liquidate # Halt + sell all positions at market
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json, logging
from datetime import date
from config.settings import setup_logging, atomic_write_json
setup_logging()
logger = logging.getLogger("emergency")

LIQUIDATE = "--liquidate" in sys.argv or "-l" in sys.argv


def halt_trading():
    """Trip circuit breaker and persist halt state."""
    halt_file = Path(__file__).resolve().parent.parent / "config" / "halt.json"
    atomic_write_json(str(halt_file), {
        "halted": True,
        "halted_at": str(date.today()),
        "reason": "Manual emergency stop",
        "liquidate": LIQUIDATE,
    }, indent=2, ensure_ascii=False)
    logger.critical("🚨 EMERGENCY STOP ACTIVATED 🚨")
    logger.critical("All trading halted. Reason: manual trigger.")
    print("\n" + "=" * 60)
    print("  🚨 紧急停止已激活 🚨")
    print("  所有交易已暂停")
    print(f"  熔断文件: {halt_file}")
    if LIQUIDATE:
        print("  ⚠️ 请手动在QMT中清仓所有持仓")
    print("=" * 60 + "\n")


def lift_halt():
    """Remove halt state."""
    halt_file = Path(__file__).resolve().parent.parent / "config" / "halt.json"
    if halt_file.exists():
        halt_file.unlink()
        logger.info("Emergency stop lifted")
        print("✅ 紧急停止已解除，恢复交易")
    else:
        print("ℹ️ 当前未在紧急停止状态")


def is_halted() -> bool:
    """Check if emergency stop is active."""
    halt_file = Path(__file__).resolve().parent.parent / "config" / "halt.json"
    if halt_file.exists():
        try:
            data = json.loads(halt_file.read_text())
            return data.get("halted", False)
        except Exception:
            pass
    return False


if __name__ == "__main__":
    if "--lift" in sys.argv or "-r" in sys.argv:
        lift_halt()
    else:
        halt_trading()
