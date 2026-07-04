"""
Global configuration for the A-Share quantitative trading system.

All environment-specific values should come from environment variables.
Defaults are for local development (docker-compose).
"""

import os


# ============================================================
# Database
# ============================================================

CLICKHOUSE_CONFIG = {
    "host": os.getenv("CLICKHOUSE_HOST", "localhost"),
    "http_port": int(os.getenv("CLICKHOUSE_HTTP_PORT", "8123")),
    "native_port": int(os.getenv("CLICKHOUSE_NATIVE_PORT", "9000")),
    "user": os.getenv("CLICKHOUSE_USER", "quant"),
    "password": os.getenv("CLICKHOUSE_PASSWORD", "quant123"),
    "database": os.getenv("CLICKHOUSE_DB", "quant"),
}

POSTGRES_CONFIG = {
    "host": os.getenv("POSTGRES_HOST", "localhost"),
    "port": int(os.getenv("POSTGRES_PORT", "5432")),
    "user": os.getenv("POSTGRES_USER", "quant"),
    "password": os.getenv("POSTGRES_PASSWORD", "quant123"),
    "database": os.getenv("POSTGRES_DB", "quant"),
}

SQLALCHEMY_DATABASE_URL = (
    f"postgresql+psycopg2://{POSTGRES_CONFIG['user']}:{POSTGRES_CONFIG['password']}"
    f"@{POSTGRES_CONFIG['host']}:{POSTGRES_CONFIG['port']}/{POSTGRES_CONFIG['database']}"
)

# ============================================================
# Data Collection
# ============================================================

# Delay between API requests to avoid rate limiting (seconds)
AKSHARE_REQUEST_DELAY = 2.0

# History data lookback (years)
HISTORY_LOOKBACK_YEARS = 3

# Trading calendar
TRADING_DAYS_PER_YEAR = 244

# ============================================================
# Strategy Defaults
# ============================================================

DEFAULT_UNIVERSE = "hs300"  # 沪深300
ALT_UNIVERSES = ["zz500", "zz1000"]  # 中证500, 中证1000

# ============================================================
# Logging
# ============================================================

LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {
            "format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "standard",
            "level": "INFO",
        },
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": "logs/quant.log",
            "maxBytes": 10 * 1024 * 1024,  # 10MB
            "backupCount": 5,
            "formatter": "standard",
            "level": "DEBUG",
        },
    },
    "root": {
        "handlers": ["console", "file"],
        "level": "DEBUG",
    },
}


def get_postgres_url() -> str:
    """Return SQLAlchemy-compatible PostgreSQL connection URL."""
    return SQLALCHEMY_DATABASE_URL


def setup_logging(level: str = "INFO", log_file: str | None = None):
    """Configure logging from the centralized config.

    Args:
        level: Override console log level (e.g. 'DEBUG', 'INFO').
        log_file: Override log file path. None = default from config.
    """
    import io
    import logging.config
    import sys
    from pathlib import Path

    # Fix Windows GBK encoding for Unicode characters (¥ etc.)
    # Only wrap if stdout is a real terminal, not a pipe/file
    if hasattr(sys.stdout, 'buffer') and sys.stdout.isatty():
        sys.stdout = io.TextIOWrapper(
            sys.stdout.buffer, encoding='utf-8', errors='replace'
        )

    config = LOGGING_CONFIG.copy()
    config["handlers"] = {
        k: v.copy() for k, v in LOGGING_CONFIG["handlers"].items()
    }
    config["handlers"]["console"]["level"] = level
    config["handlers"]["console"]["stream"] = "ext://sys.stdout"

    if log_file:
        config["handlers"]["file"]["filename"] = log_file
    else:
        Path("logs").mkdir(exist_ok=True)

    logging.config.dictConfig(config)


def atomic_write_json(filepath, data, **kwargs):
    """Write JSON atomically: temp file → rename. Prevents corruption on crash.

    Args:
        filepath: Path or str — target JSON file.
        data: JSON-serializable object to write.
        **kwargs: Passed to json.dumps (e.g., indent, ensure_ascii).
    """
    import json
    import os
    from pathlib import Path

    path = Path(filepath)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp_path.write_text(
            json.dumps(data, **kwargs) if kwargs else json.dumps(data),
            encoding="utf-8",
        )
        os.replace(tmp_path, path)  # Atomic on Windows & Unix
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        raise
