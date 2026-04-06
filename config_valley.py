"""
config_valley.py — Configuration for Valley/Peak 30m Strategy (10x Leverage)

Strategy: Bidirectional valley/peak detection on SOL 30m candles
Leverage: 10x
Expected: 96.6% win rate, +50-60% weekly P&L
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ─────────────────────────────────────────────────────────────────
# Strategy Selection
# ─────────────────────────────────────────────────────────────────
STRATEGY_TYPE = "VALLEY_PEAK"  # "VALLEY_PEAK", "MOMENTUM_BREAKOUT", "RSI_MEAN_REVERSION"

# ─────────────────────────────────────────────────────────────────
# Hyperliquid / Exchange
# ─────────────────────────────────────────────────────────────────
LITE_AGENT_API_KEY: str = os.getenv("LITE_AGENT_API_KEY", "")
HYPERLIQUID_API_URL: str = "https://api.hyperliquid.xyz"

# ─────────────────────────────────────────────────────────────────
# Trading Parameters
# ─────────────────────────────────────────────────────────────────
SYMBOL: str = os.getenv("SYMBOL", "SOL")
CANDLE_INTERVAL: str = os.getenv("CANDLE_INTERVAL", "30m")
LEVERAGE: int = int(os.getenv("LEVERAGE", "10"))
POSITION_SIZE_USD: float = float(os.getenv("POSITION_SIZE_USD", "100"))

# ─────────────────────────────────────────────────────────────────
# Valley/Peak Strategy Parameters
# ─────────────────────────────────────────────────────────────────
TP_PERCENT: float = float(os.getenv("TP_PERCENT", "3.0"))  # Take profit 3%
SL_PERCENT: float = float(os.getenv("SL_PERCENT", "1.5"))  # Stop loss 1.5%

# ─────────────────────────────────────────────────────────────────
# Risk Management
# ─────────────────────────────────────────────────────────────────
MAX_CONSECUTIVE_SL: int = int(os.getenv("MAX_CONSECUTIVE_SL", "2"))
DAILY_LOSS_LIMIT_USD: float = float(os.getenv("DAILY_LOSS_LIMIT", "-500"))
EQUITY_STOP_PERCENT: float = float(os.getenv("EQUITY_STOP", "0.50"))  # 50%

# ─────────────────────────────────────────────────────────────────
# Candle Fetching
# ─────────────────────────────────────────────────────────────────
CANDLE_LOOKBACK: int = 100  # Fetch 100 candles for valley/peak detection

# ─────────────────────────────────────────────────────────────────
# Loop Timing
# ─────────────────────────────────────────────────────────────────
LOOP_INTERVAL_SECONDS: int = 1800  # 30 minutes (matches 30m candle)
HEALTH_LOG_INTERVAL: int = 1       # Heartbeat every loop

# ─────────────────────────────────────────────────────────────────
# Telegram Alerts
# ─────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

# ─────────────────────────────────────────────────────────────────
# Bot Behavior
# ─────────────────────────────────────────────────────────────────
DRY_RUN: bool = os.getenv("DRY_RUN", "true").lower() in ("true", "1", "yes")
STATE_FILE: str = os.getenv("STATE_FILE", "position_state_valley.json")
WEEKDAY_ONLY: bool = os.getenv("WEEKDAY_ONLY", "true").lower() in ("true", "1", "yes")

# ─────────────────────────────────────────────────────────────────
# Upstash Redis (optional)
# ─────────────────────────────────────────────────────────────────
UPSTASH_REDIS_REST_URL: str = os.getenv("UPSTASH_REDIS_REST_URL", "")
UPSTASH_REDIS_REST_TOKEN: str = os.getenv("UPSTASH_REDIS_REST_TOKEN", "")

# Retry settings
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

    if LEVERAGE < 1 or LEVERAGE > 20:
        errors.append(f"LEVERAGE must be between 1 and 20, got {LEVERAGE}")

    if POSITION_SIZE_USD < 10:
        errors.append(f"POSITION_SIZE_USD seems low: {POSITION_SIZE_USD}")

    if TP_PERCENT <= 0 or TP_PERCENT > 10:
        errors.append(f"TP_PERCENT must be > 0 and <= 10, got {TP_PERCENT}")

    if SL_PERCENT <= 0 or SL_PERCENT > 5:
        errors.append(f"SL_PERCENT must be > 0 and <= 5, got {SL_PERCENT}")

    if errors:
        raise EnvironmentError(
            "Configuration errors found:\n" + "\n".join(f"  - {e}" for e in errors)
        )
