"""
config_v4.py — Configuration for Composite + DCA Strategy on BTC 4h (DegenClaw)

Strategy: Composite scoring + DCA + Regime filter
  LONG:  StochK cross up + composite score ≥ 3.5 + gate ≥2/5 + regime OK
  SHORT: StochK cross dn + composite score ≥ 3.5 + gate ≥2/5 + regime OK
  DCA:   Layer 1 = 50% margin on initial signal; Layer 2 = 50% margin on 0.8% adverse move
  Exit:  TP +2.5% / SL -1.5% from avg entry (native HL orders)

Backtested 90 days BTC 4h @ 25x: 30 trades, 53% WR, +124% ROI, PF 1.49, Sortino 0.33, Max DD 27%
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
SYMBOL: str = os.getenv("SYMBOL", "BTC")
CANDLE_INTERVAL: str = os.getenv("CANDLE_INTERVAL", "4h")
LEVERAGE: int = int(os.getenv("LEVERAGE", "25"))
POSITION_SIZE_USD: float = float(os.getenv("POSITION_SIZE_USD", "50"))

# ─────────────────────────────────────────────
# Stoch+MACD Strategy Parameters
# ─────────────────────────────────────────────
TP_PERCENT: float = float(os.getenv("TP_PERCENT", "2.5"))
SL_PERCENT: float = float(os.getenv("SL_PERCENT", "1.5"))

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
    if not DRY_RUN:
        if not os.getenv("HYPERLIQUID_PRIVATE_KEY", "").strip():
            errors.append("HYPERLIQUID_PRIVATE_KEY required when DRY_RUN=false")
        if not os.getenv("HYPERLIQUID_ACCOUNT_ADDRESS", "").strip():
            errors.append("HYPERLIQUID_ACCOUNT_ADDRESS required when DRY_RUN=false")
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
REGIME_BUY_PCT: float = float(os.getenv("REGIME_BUY_PCT", "10"))   # block buys if >10% below 30-bar high (wider for 4h)
REGIME_SELL_PCT: float = float(os.getenv("REGIME_SELL_PCT", "3"))  # block shorts if <3% above 30-bar low

# ─────────────────────────────────────────────
# Exhaustion Blocker (prevents trading at extremes)
# ─────────────────────────────────────────────
EXHAUST_RSI_LOW: float = float(os.getenv("EXHAUST_RSI_LOW", "25"))   # block longs if RSI < this AND StochK < low
EXHAUST_RSI_HIGH: float = float(os.getenv("EXHAUST_RSI_HIGH", "75")) # block shorts if RSI > this AND StochK > high
EXHAUST_STK_LOW: float = float(os.getenv("EXHAUST_STK_LOW", "10"))   # StochK extreme low threshold
EXHAUST_STK_HIGH: float = float(os.getenv("EXHAUST_STK_HIGH", "90")) # StochK extreme high threshold

# ─────────────────────────────────────────────
# DCA (Dollar Cost Average) Settings
# ─────────────────────────────────────────────
DCA_ENABLED: bool = os.getenv("DCA_ENABLED", "true").lower() in ("true", "1", "yes")
DCA_LAYERS: int = int(os.getenv("DCA_LAYERS", "2"))                # total layers (incl. initial)
DCA_TRIGGER_PCT: float = float(os.getenv("DCA_TRIGGER_PCT", "0.8"))  # % adverse move to trigger next layer (wider for 4h)
