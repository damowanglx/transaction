"""
Risk management parameters for the A-Share quantitative trading system.

CRITICAL: These parameters are the LAST LINE OF DEFENSE.
Do not relax them lightly.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RiskConfig:
    """Immutable risk configuration. Create new instance to change values."""

    # ---- Position Limits ----
    max_single_position_pct: float = 0.20   # Single stock ≤ 20% of capital
    max_total_position_pct: float = 0.80    # Total position ≤ 80% of capital
    max_holdings_count: int = 10            # Max number of concurrent holdings

    # ---- Stop Loss ----
    stop_loss_pct: float = 0.05             # Hard stop at -5% per position
    take_profit_pct: float = 0.15           # Take profit signal at +15%

    # ---- Circuit Breaker ----
    max_daily_loss_pct: float = 0.02        # Meltdown if daily loss > 2% of capital
    max_consecutive_loss_days: int = 3      # Pause after N consecutive loss days
    pause_duration_days: int = 7           # Trading pause after meltdown (~5 trading days)

    # ---- Liquidity Filters ----
    min_daily_volume_yuan: float = 500_000  # Min daily turnover (avoid illiquid stocks)
    max_position_volume_ratio: float = 0.02 # Position ≤ 2% of stock's daily volume

    # ---- Stock Filters ----
    exclude_st: bool = True                 # Exclude ST stocks
    exclude_new_listing_days: int = 60      # Exclude newly listed (< 60 trading days)
    min_stock_price: float = 3.0            # Min stock price (avoid penny stocks)
    max_stock_price: float = 200.0          # Max stock price (liquidity filter)

    # ---- Commission (realistic defaults) ----
    commission_rate: float = 0.0003         # Broker commission (万三 default)
    stamp_tax_rate: float = 0.001           # Stamp tax on sell (千一)
    min_commission: float = 5.0             # Minimum commission per trade (¥5)


# Default risk config instance
DEFAULT_RISK_CONFIG = RiskConfig()

# Conservative variant for initial live trading
CONSERVATIVE_RISK_CONFIG = RiskConfig(
    max_single_position_pct=0.10,
    max_total_position_pct=0.50,
    max_holdings_count=5,
    stop_loss_pct=0.03,
    max_daily_loss_pct=0.01,
)


def get_risk_config(mode: str = "default") -> RiskConfig:
    """Get risk configuration by mode, merged with user overrides.

    Reads config/user_config.json if present and overlays user settings.
    """
    from dataclasses import asdict
    import json
    from pathlib import Path

    modes = {
        "default": DEFAULT_RISK_CONFIG,
        "conservative": CONSERVATIVE_RISK_CONFIG,
    }
    if mode not in modes:
        raise ValueError(f"Unknown risk mode: {mode}. Valid modes: {list(modes.keys())}")

    config = modes[mode]

    # Overlay user config if present
    user_file = Path(__file__).resolve().parent.parent / "config" / "user_config.json"
    if user_file.exists():
        try:
            user_data = json.loads(user_file.read_text())
            risk_overrides = user_data.get("risk", {})
            if risk_overrides:
                merged = {**asdict(config), **risk_overrides}
                config = RiskConfig(**merged)
                logger.debug("Loaded user risk config from %s", user_file)
        except Exception:
            logger.warning("Failed to load user_config.json, using defaults", exc_info=True)

    return config
