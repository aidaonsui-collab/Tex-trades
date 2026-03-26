"""
config.py — Centralised configuration for the VWAP Cross trading bot.
All settings are loaded from environment variables with safe defaults.
"""

import os
from dotenv import load_dotenv

load_dotenv()


# ─────────────────────────────────────────────
# Hyperliquid / Exchange
# ─────────────────────────────────────────────
HYPERLIQUID_PRIVATE_KEY: str = os.getenv("HYPERLIQUID_PRIVATE_KEY", "")
HYPERLIQUID_API_URL: str = "https://api.hyperliquid.xyz"

# Trading symbol (coin name as Hyperliquid expects it, e.g. "BTC")
SYMBOL: str = os.getenv("SYMBOL", "BTC")

# Leverage to use when opening positions (integer)
LEVERAGE: int = int(os.getenv("LEVERAGE", "10"))

# Notional size of each trade in USD
POSITION_SIZE_USD: float = float(os.getenv("POSITION_SIZE_USD", "50"))

# ─────────────────────────────────────────────
# Strategy
# ─────────────────────────────────────────────
CANDLE_INTERVAL: str = "15m"       # Hyperliquid candle interval string
CANDLE_LOOKBACK: int = 100         # Number of candles to fetch each cycle
RSI_PERIOD: int = 14               # RSI calculation period
RSI_LOWER: float = 40.0            # RSI must be ABOVE this for a valid signal
RSI_UPPER: float = 60.0            # RSI must be BELOW this for a valid signal
LOOP_INTERVAL_SECONDS: int = 900   # 15 minutes — matches candle timeframe
HEALTH_LOG_INTERVAL: int = 4       # Log health status every N loops (~1 hr)

# ─────────────────────────────────────────────
# Telegram
# ─────────────────────────────────────────────
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

# ─────────────────────────────────────────────
# Bot behaviour
# ─────────────────────────────────────────────
# DRY_RUN=true  → simulate signals, log everything, NO real orders sent
# DRY_RUN=false → live trading, real orders executed on Hyperliquid
DRY_RUN: bool = os.getenv("DRY_RUN", "true").lower() in ("true", "1", "yes")

# Path to persist position state across restarts
STATE_FILE: str = os.getenv("STATE_FILE", "position_state.json")

# Exponential backoff settings for API retries
RETRY_MAX_ATTEMPTS: int = 5
RETRY_BASE_DELAY: float = 2.0      # seconds
RETRY_MAX_DELAY: float = 60.0     # seconds


def validate():
    """
    Validate critical config at startup and raise early if anything is missing.
    Called once from bot.py before the main loop starts.
    """
    errors = []

    if not DRY_RUN:
        if not HYPERLIQUID_PRIVATE_KEY:
            errors.append("HYPERLIQUID_PRIVATE_KEY is required when DRY_RUN=false")

    if not TELEGRAM_BOT_TOKEN:
        errors.append("TELEGRAM_BOT_TOKEN is required")

    if not TELEGRAM_CHAT_ID:
        errors.append("TELEGRAM_CHAT_ID is required")

    if LEVERAGE < 1 or LEVERAGE > 50:
        errors.append(f"LEVERAGE must be between 1 and 50, got {LEVERAGE}")

    if POSITION_SIZE_USD < 10:
        errors.append(f"POSITION_SIZE_USD seems dangerously low: {POSITION_SIZE_USD}")

    if errors:
        raise EnvironmentError(
            "Configuration errors found:\n" + "\n".join(f"  - {e}" for e in errors)
        )
