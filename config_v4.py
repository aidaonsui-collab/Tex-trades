"""
config_v4.py — Configuration for Stoch+MACD Strategy (DegenClaw)

Strategy: Stochastic K/D cross + MACD trend confirmation
  LONG:  StochK crosses above D + MACD > 0 + MACD histogram > 0
  SHORT: StochK crosses below D + MACD < 0 + RSI < 50
  Exit:  TP +1.5% / SL -1.2% (no leverage, no fees on DegenClaw)

Backtested 90 days SOL 1h: 162 trades (12.6/week), 49% WR, +21.6% ROI, PF 1.22
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ─────────────────────────────────────────────
# Strategy Selection
# ─────────────────────────────────────────────
STRATEGY_TYPE = "STOCH_MACD"

# ─────────────────────────────────────────────
# Exchange / DegenClaw ACP
# ─────────────────────────────────────────────
LITE_AGENT_API_KEY: str = os.getenv("LITE_AGENT_API_KEY", "")
HYPERLIQUID_API_URL: str = "https://api.hyperliquid.xyz"

# ─────────────────────────────────────────────
# Trading Parameters
# ─────────────────────────────────────────────
SYMBOL: str = os.getenv("SYMBOL", "SOL")
CANDLE_INTERVAL: str = os.getenv("CANDLE_INTERVAL", "1h")
LEVERAGE: int = int(os.getenv("LEVERAGE", "10"))
POSITION_SIZE_USD: float = float(os.getenv("POSITION_SIZE_USD", "100"))

# ─────────────────────────────────────────────
# Stoch+MACD Strategy Parameters
# ─────────────────────────────────────────────
TP_PERCENT: float = float(os.getenv("TP_PERCENT", "2.0"))
SL_PERCENT: float = float(os.getenv("SL_PERCENT", "1.0"))

# Stochastic
STOCH_K_PERIOD: int = int(os.getenv("STOCH_K_PERIOD", "14"))
STOCH_D_PERIOD: int = int(os.getenv("STOCH_D_PERIOD", "3"))

# MACD
MACD_FAST: int = int(os.getenv("MACD_FAST", "12"))
MACD_SLOW: int = int(os.getenv("MACD_SLOW", "26"))
MACD_SIGNAL: int = int(os.getenv("MACD_SIGNAL", "9"))

# RSI (for short filter)
RSI_PERIOD: int = int(os.getenv("RSI_PERIOD", "14"))
RSI_SHORT_THRESHOLD: float = float(os.getenv("RSI_SHORT_THRESHOLD", "50"))

# Cooldown
COOLDOWN_BARS: int = int(os.getenv("COOLDOWN_BARS", "2"))

# ─────────────────────────────────────────────
# Candle Fetching
# ─────────────────────────────────────────────
CANDLE_LOOKBACK: int = 100

# ─────────────────────────────────────────────
# Loop Timing
# ─────────────────────────────────────────────
LOOP_INTERVAL_SECONDS: int = int(os.getenv("LOOP_INTERVAL", "3600"))
HEALTH_LOG_INTERVAL: int = 1

# ─────────────────────────────────────────────
# Telegram
# ─────────────────────────────────────────────
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

# ─────────────────────────────────────────────
# Bot Behavior
# ─────────────────────────────────────────────
DRY_RUN: bool = os.getenv("DRY_RUN", "true").lower() in ("true", "1", "yes")
STATE_FILE: str = os.getenv("STATE_FILE", "position_state_v4.json")

# ─────────────────────────────────────────────
# Upstash Redis
# ─────────────────────────────────────────────
UPSTASH_REDIS_REST_URL: str = os.getenv("UPSTASH_REDIS_REST_URL", "")
UPSTASH_REDIS_REST_TOKEN: str = os.getenv("UPSTASH_REDIS_REST_TOKEN", "")

RETRY_MAX_ATTEMPTS: int = 5
RETRY_BASE_DELAY: float = 2.0
RETRY_MAX_DELAY: float = 60.0


def validate():
    errors = []
    if not DRY_RUN and not LITE_AGENT_API_KEY:
        errors.append("LITE_AGENT_API_KEY required when DRY_RUN=false")
    if TELEGRAM_BOT_TOKEN and not TELEGRAM_CHAT_ID:
        errors.append("TELEGRAM_CHAT_ID required with TELEGRAM_BOT_TOKEN")
    if TP_PERCENT <= 0 or TP_PERCENT > 10:
        errors.append(f"TP_PERCENT must be 0-10, got {TP_PERCENT}")
    if SL_PERCENT <= 0 or SL_PERCENT > 5:
        errors.append(f"SL_PERCENT must be 0-5, got {SL_PERCENT}")
    if errors:
        raise EnvironmentError("Config errors:\n" + "\n".join(f"  - {e}" for e in errors))

# ─────────────────────────────────────────────
# Composite Signal Parameters
# ─────────────────────────────────────────────
MIN_SCORE: float = float(os.getenv("MIN_SCORE", "3.5"))
HTF_BONUS: float = float(os.getenv("HTF_BONUS", "0.5"))
HTF_INTERVAL: str = os.getenv("HTF_INTERVAL", "4h")

# ─────────────────────────────────────────────
# Regime Filter (prevents catching falling knives / shorting capitulation)
# ─────────────────────────────────────────────
REGIME_BUY_PCT: float = float(os.getenv("REGIME_BUY_PCT", "6"))    # block buys if >6% below 30-bar high
REGIME_SELL_PCT: float = float(os.getenv("REGIME_SELL_PCT", "3"))   # block shorts if <3% above 30-bar low

# ─────────────────────────────────────────────
# Exhaustion Blocker (prevents trading at extremes)
# ─────────────────────────────────────────────
EXHAUST_RSI_LOW: float = float(os.getenv("EXHAUST_RSI_LOW", "30"))   # block longs if RSI < this AND StochK < low
EXHAUST_RSI_HIGH: float = float(os.getenv("EXHAUST_RSI_HIGH", "70")) # block shorts if RSI > this AND StochK > high
EXHAUST_STK_LOW: float = float(os.getenv("EXHAUST_STK_LOW", "15"))   # StochK extreme low threshold
EXHAUST_STK_HIGH: float = float(os.getenv("EXHAUST_STK_HIGH", "85")) # StochK extreme high threshold
