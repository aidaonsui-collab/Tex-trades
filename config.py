"""
config.py — Centralised configuration for the Momentum Breakout trading bot.

Strategy: Momentum Breakout + EMA Trend Filter + ATR Stops
Optimised for Degen Claw weekly competition (Sortino + Return% + PF).

All settings are loaded from environment variables with safe defaults.
"""

import os
from dotenv import load_dotenv

load_dotenv()


# ─────────────────────────────────────────────
# DegenClaw ACP / Exchange
# ─────────────────────────────────────────────
LITE_AGENT_API_KEY: str = os.getenv("LITE_AGENT_API_KEY", "")
HYPERLIQUID_API_URL: str = "https://api.hyperliquid.xyz"

# Trading symbol — SOL outperforms BTC/ETH for breakout strategies
SYMBOL: str = os.getenv("SYMBOL", "SOL")

# Leverage to use when opening positions
LEVERAGE: int = int(os.getenv("LEVERAGE", "15"))

# Notional size of each trade in USD
POSITION_SIZE_USD: float = float(os.getenv("POSITION_SIZE_USD", "50"))

# ─────────────────────────────────────────────
# Strategy — Momentum Breakout
# ─────────────────────────────────────────────
CANDLE_INTERVAL: str = os.getenv("CANDLE_INTERVAL", "1h")
CANDLE_LOOKBACK: int = 100         # candles to fetch each cycle

# Breakout detection
BREAKOUT_LOOKBACK: int = int(os.getenv("BREAKOUT_LOOKBACK", "12"))
    # Break above/below this many bars' high/low

ROC_THRESHOLD: float = float(os.getenv("ROC_THRESHOLD", "1.0"))
    # Rate of change (%) must exceed this for momentum confirmation

VOLUME_MULTIPLIER: float = float(os.getenv("VOLUME_MULTIPLIER", "1.0"))
    # Volume must be above SMA(20) * this multiplier

# Trend filter
TREND_EMA_PERIOD: int = int(os.getenv("TREND_EMA_PERIOD", "50"))
    # Only long above EMA, only short below EMA

# Risk management — ATR-based stops
ATR_PERIOD: int = 14
ATR_MULTIPLIER: float = float(os.getenv("ATR_MULTIPLIER", "2.0"))
    # Stop loss distance = ATR * this value
REWARD_RISK_RATIO: float = float(os.getenv("REWARD_RISK_RATIO", "2.5"))
    # Take profit = stop distance * this ratio (2.5 = 2.5:1 R:R)

# Loop timing
LOOP_INTERVAL_SECONDS: int = 3600  # 1 hour — matches 1h candle
HEALTH_LOG_INTERVAL: int = 1       # heartbeat every loop (~1 hour)

# ─────────────────────────────────────────────
# Telegram
# ─────────────────────────────────────────────
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

# ─────────────────────────────────────────────
# Bot behaviour
# ─────────────────────────────────────────────
DRY_RUN: bool = os.getenv("DRY_RUN", "true").lower() in ("true", "1", "yes")
STATE_FILE: str = os.getenv("STATE_FILE", "position_state.json")

# ─────────────────────────────────────────────
# Upstash Redis (optional — for Railway persistence)
# ─────────────────────────────────────────────
UPSTASH_REDIS_REST_URL: str = os.getenv("UPSTASH_REDIS_REST_URL", "")
UPSTASH_REDIS_REST_TOKEN: str = os.getenv("UPSTASH_REDIS_REST_TOKEN", "")

# Exponential backoff settings
RETRY_MAX_ATTEMPTS: int = 5
RETRY_BASE_DELAY: float = 2.0
RETRY_MAX_DELAY: float = 60.0


def validate():
    """Validate critical config at startup."""
    errors = []

    if not DRY_RUN:
        if not LITE_AGENT_API_KEY:
            errors.append("LITE_AGENT_API_KEY is required when DRY_RUN=false")

    if TELEGRAM_BOT_TOKEN and not TELEGRAM_CHAT_ID:
        errors.append("TELEGRAM_CHAT_ID is required when TELEGRAM_BOT_TOKEN is set")

    if LEVERAGE < 1 or LEVERAGE > 50:
        errors.append(f"LEVERAGE must be between 1 and 50, got {LEVERAGE}")

    if POSITION_SIZE_USD < 10:
        errors.append(f"POSITION_SIZE_USD seems dangerously low: {POSITION_SIZE_USD}")

    if BREAKOUT_LOOKBACK < 4 or BREAKOUT_LOOKBACK > 100:
        errors.append(f"BREAKOUT_LOOKBACK should be 4-100, got {BREAKOUT_LOOKBACK}")

    if REWARD_RISK_RATIO < 1.0:
        errors.append(f"REWARD_RISK_RATIO must be >= 1.0, got {REWARD_RISK_RATIO}")

    if errors:
        raise EnvironmentError(
            "Configuration errors found:\n" + "\n".join(f"  - {e}" for e in errors)
        )
