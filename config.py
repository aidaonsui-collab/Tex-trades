"""
config.py — Centralised configuration for the VWAP Cross trading bot.
All settings are loaded from environment variables with safe defaults.
"""

import os
from dotenv import load_dotenv

load_dotenv()


# ─────────────────────────────────────────────
# DegenClaw ACP / Exchange
# ─────────────────────────────────────────────
# ACP API key — from ~/openclaw-acp/config.json (LITE_AGENT_API_KEY)
LITE_AGENT_API_KEY: str = os.getenv("LITE_AGENT_API_KEY", "")
# Hyperliquid public endpoint (candles + price feed, no auth needed)
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
RSI_LOWER: float = 35.0            # RSI must be ABOVE this for a valid signal
RSI_UPPER: float = 65.0            # RSI must be BELOW this for a valid signal
LOOP_INTERVAL_SECONDS: int = 900   # 15 minutes — matches candle timeframe
HEALTH_LOG_INTERVAL: int = 2       # Log health status every N loops (~30 min)

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

# Path to persist position state across restarts (used when Upstash is not configured)
STATE_FILE: str = os.getenv("STATE_FILE", "position_state.json")

# ─────────────────────────────────────────────
# Upstash Redis (optional — for persistent state on Railway)
# ─────────────────────────────────────────────
# When both vars are set the bot stores position state in Upstash Redis so
# that state survives Railway restarts and redeploys.  If either var is
# absent the bot falls back to the local STATE_FILE JSON file.
#
# Get these values from your Upstash console at https://console.upstash.com
# after creating a Redis database (free tier is sufficient).
UPSTASH_REDIS_REST_URL: str = os.getenv("UPSTASH_REDIS_REST_URL", "")
UPSTASH_REDIS_REST_TOKEN: str = os.getenv("UPSTASH_REDIS_REST_TOKEN", "")

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
        if not LITE_AGENT_API_KEY:
            errors.append("LITE_AGENT_API_KEY is required when DRY_RUN=false")

    # Telegram is optional — bot runs without alerts if not configured
    if TELEGRAM_BOT_TOKEN and not TELEGRAM_CHAT_ID:
        errors.append("TELEGRAM_CHAT_ID is required when TELEGRAM_BOT_TOKEN is set")

    if LEVERAGE < 1 or LEVERAGE > 50:
        errors.append(f"LEVERAGE must be between 1 and 50, got {LEVERAGE}")

    if POSITION_SIZE_USD < 10:
        errors.append(f"POSITION_SIZE_USD seems dangerously low: {POSITION_SIZE_USD}")

    if errors:
        raise EnvironmentError(
            "Configuration errors found:\n" + "\n".join(f"  - {e}" for e in errors)
        )
