"""
strategy_v3.py — Valley+ROC6 Long / Peak+Volume Short

Backtested on SOL 1h, 90 days, no fees:
  - 174 trades (13.5/week) — meets DegenClaw 10+/week requirement
  - 48% win rate with 1.5% TP / 1.2% SL (1.25:1 R:R)
  - +12.5% ROI, PF 1.14, positive Sortino
  
Entry logic:
  LONG:  Valley (low < prev AND next low) + ROC6 < -2% (momentum pullback)
  SHORT: Peak (high > prev AND next high) + Volume > 1.2x 20-bar SMA

Exit logic:
  TP: +1.5% from entry
  SL: -1.2% from entry
  No signal-flip exits (hold until TP or SL)
"""

import logging
import math
from typing import Literal, Optional, TypedDict

logger = logging.getLogger(__name__)

Signal = Literal["LONG", "SHORT", "NONE"]


class Candle(TypedDict):
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float


class SignalResult(TypedDict):
    signal: Signal
    price: float
    atr: float
    roc6: float
    vol_ratio: float
    is_valley: bool
    is_peak: bool


# ── Config defaults (overridden by env vars in config) ────────
TP_PERCENT = 1.5
SL_PERCENT = 1.2
ROC6_THRESHOLD = -2.0
VOLUME_MULTIPLIER = 1.2
VOLUME_SMA_PERIOD = 20


def compute_roc(candles: list[Candle], period: int = 6) -> float:
    """Rate of change over `period` bars."""
    if len(candles) < period + 1:
        return 0.0
    current = candles[-1]["close"]
    past = candles[-(period + 1)]["close"]
    if past == 0:
        return 0.0
    return ((current / past) - 1.0) * 100.0


def compute_volume_ratio(candles: list[Candle], period: int = VOLUME_SMA_PERIOD) -> float:
    """Current volume / SMA of volume."""
    if len(candles) < period:
        return 1.0
    vols = [c["volume"] for c in candles[-period:]]
    avg = sum(vols) / len(vols)
    if avg == 0:
        return 1.0
    return candles[-1]["volume"] / avg


def compute_atr(candles: list[Candle], period: int = 14) -> float:
    """ATR for position sizing reference."""
    if len(candles) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(candles)):
        h, l, pc = candles[i]["high"], candles[i]["low"], candles[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(trs) < period:
        return sum(trs) / len(trs) if trs else 0.0
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr


def is_valley(candles: list[Candle], idx: int) -> bool:
    if idx < 1 or idx >= len(candles) - 1:
        return False
    return (candles[idx]["low"] < candles[idx - 1]["low"] and
            candles[idx]["low"] < candles[idx + 1]["low"])


def is_peak(candles: list[Candle], idx: int) -> bool:
    if idx < 1 or idx >= len(candles) - 1:
        return False
    return (candles[idx]["high"] > candles[idx - 1]["high"] and
            candles[idx]["high"] > candles[idx + 1]["high"])


def check_exit(current_candle: Candle, entry_price: float, entry_side: str,
               tp_pct: float = TP_PERCENT, sl_pct: float = SL_PERCENT
               ) -> tuple[bool, Optional[str], Optional[float]]:
    """Check TP/SL exit only — no signal-flip exits."""
    tp = tp_pct / 100.0
    sl = sl_pct / 100.0

    if entry_side == "LONG":
        tp_price = entry_price * (1.0 + tp)
        sl_price = entry_price * (1.0 - sl)
        if current_candle["high"] >= tp_price:
            return True, "TP_HIT", tp_price
        if current_candle["low"] <= sl_price:
            return True, "SL_HIT", sl_price

    elif entry_side == "SHORT":
        tp_price = entry_price * (1.0 - tp)
        sl_price = entry_price * (1.0 + sl)
        if current_candle["low"] <= tp_price:
            return True, "TP_HIT", tp_price
        if current_candle["high"] >= sl_price:
            return True, "SL_HIT", sl_price

    return False, None, None


def compute_signal(candles: list[Candle],
                   roc_threshold: float = ROC6_THRESHOLD,
                   vol_multiplier: float = VOLUME_MULTIPLIER,
                   ) -> SignalResult:
    """
    Compute signal:
      LONG  if second-to-last candle is a valley AND ROC6 < threshold
      SHORT if second-to-last candle is a peak AND volume > multiplier * SMA
    """
    if len(candles) < 22:  # need enough for volume SMA + ROC
        return {"signal": "NONE", "price": candles[-1]["close"], "atr": 0.0,
                "roc6": 0.0, "vol_ratio": 1.0, "is_valley": False, "is_peak": False}

    atr = compute_atr(candles)
    roc6 = compute_roc(candles[:-1], period=6)  # ROC of the confirmed bar
    vol_ratio = compute_volume_ratio(candles[:-1])

    # Check second-to-last candle (confirmed by last candle)
    idx = len(candles) - 2
    valley = is_valley(candles, idx)
    peak = is_peak(candles, idx)

    signal: Signal = "NONE"
    price = candles[-1]["close"]

    if valley and roc6 < roc_threshold:
        signal = "LONG"
        logger.info("LONG signal: valley at $%.2f, ROC6=%.2f%%", candles[idx]["low"], roc6)

    elif peak and vol_ratio > vol_multiplier:
        signal = "SHORT"
        logger.info("SHORT signal: peak at $%.2f, vol=%.1fx", candles[idx]["high"], vol_ratio)

    return {
        "signal": signal,
        "price": price,
        "atr": atr,
        "roc6": roc6,
        "vol_ratio": vol_ratio,
        "is_valley": valley,
        "is_peak": peak,
    }


def calculate_size(price: float, size_usd: float, leverage: int) -> float:
    raw = (size_usd * leverage) / price
    return math.floor(raw * 10_000) / 10_000
