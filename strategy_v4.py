"""
strategy_v4.py — Stochastic K/D Cross + MACD Confirmation

Backtested 90d SOL 1h, no fees (DegenClaw):
  162 trades (12.6/week), 49% WR, +21.6% ROI, PF 1.22, Sortino 0.16

Entry:
  LONG:  StochK crosses above StochD + MACD line > 0 + MACD histogram > 0
  SHORT: StochK crosses below StochD + MACD line < 0 + RSI < 50

Exit:
  TP: +1.5% from entry
  SL: -1.2% from entry
  No signal-flip exits (hold until TP or SL)
"""

import logging
import math
import numpy as np
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
    stoch_k: float
    stoch_d: float
    macd: float
    macd_signal: float
    macd_hist: float
    rsi: float


# ── Indicators ────────────────────────────────────────────────

def compute_ema(values: list[float], period: int) -> list[float]:
    """Exponential Moving Average."""
    if not values:
        return []
    ema = [values[0]]
    mult = 2.0 / (period + 1)
    for i in range(1, len(values)):
        ema.append((values[i] - ema[-1]) * mult + ema[-1])
    return ema


def compute_stochastic(candles: list[Candle], k_period: int = 14, d_period: int = 3) -> tuple[list[float], list[float]]:
    """Stochastic K and D."""
    n = len(candles)
    k_values = []
    for i in range(n):
        start = max(0, i - k_period + 1)
        window = candles[start:i + 1]
        lowest = min(c["low"] for c in window)
        highest = max(c["high"] for c in window)
        if highest == lowest:
            k_values.append(50.0)
        else:
            k_values.append(((candles[i]["close"] - lowest) / (highest - lowest)) * 100.0)

    # D = SMA of K
    d_values = []
    for i in range(n):
        start = max(0, i - d_period + 1)
        window = k_values[start:i + 1]
        d_values.append(sum(window) / len(window))

    return k_values, d_values


def compute_macd(candles: list[Candle], fast: int = 12, slow: int = 26, signal_period: int = 9
                 ) -> tuple[list[float], list[float], list[float]]:
    """MACD line, signal line, histogram."""
    closes = [c["close"] for c in candles]
    ema_fast = compute_ema(closes, fast)
    ema_slow = compute_ema(closes, slow)
    macd_line = [f - s for f, s in zip(ema_fast, ema_slow)]
    signal_line = compute_ema(macd_line, signal_period)
    histogram = [m - s for m, s in zip(macd_line, signal_line)]
    return macd_line, signal_line, histogram


def compute_rsi(candles: list[Candle], period: int = 14) -> list[float]:
    """RSI using Wilder smoothing."""
    closes = [c["close"] for c in candles]
    n = len(closes)
    if n < 2:
        return [50.0] * n

    rsi_values = [50.0]  # first value
    gains = []
    losses = []

    for i in range(1, n):
        delta = closes[i] - closes[i - 1]
        gains.append(max(0, delta))
        losses.append(max(0, -delta))

    if len(gains) < period:
        return [50.0] * n

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    rsi_values = [50.0] * (period + 1)
    if avg_loss == 0:
        rsi_values[-1] = 100.0
    else:
        rs = avg_gain / avg_loss
        rsi_values[-1] = 100.0 - (100.0 / (1.0 + rs))

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            rsi_values.append(100.0)
        else:
            rs = avg_gain / avg_loss
            rsi_values.append(100.0 - (100.0 / (1.0 + rs)))

    return rsi_values


# ── Signal Detection ──────────────────────────────────────────

def compute_signal(candles: list[Candle],
                   k_period: int = 14, d_period: int = 3,
                   macd_fast: int = 12, macd_slow: int = 26, macd_sig_period: int = 9,
                   rsi_period: int = 14, rsi_threshold: float = 50.0,
                   ) -> SignalResult:
    """
    Compute signal:
      LONG:  StochK crosses above D (K[i]>D[i] AND K[i-1]<=D[i-1]) + MACD>0 + hist>0
      SHORT: StochK crosses below D (K[i]<D[i] AND K[i-1]>=D[i-1]) + MACD<0 + RSI<threshold
    """
    if len(candles) < max(macd_slow, k_period, rsi_period) + 5:
        return {"signal": "NONE", "price": candles[-1]["close"],
                "stoch_k": 50, "stoch_d": 50, "macd": 0, "macd_signal": 0,
                "macd_hist": 0, "rsi": 50}

    k_vals, d_vals = compute_stochastic(candles, k_period, d_period)
    macd_line, signal_line, histogram = compute_macd(candles, macd_fast, macd_slow, macd_sig_period)
    rsi_vals = compute_rsi(candles, rsi_period)

    # Current and previous values
    k_now = k_vals[-1]
    k_prev = k_vals[-2]
    d_now = d_vals[-1]
    d_prev = d_vals[-2]
    macd_now = macd_line[-1]
    hist_now = histogram[-1]
    rsi_now = rsi_vals[-1]
    price = candles[-1]["close"]

    # Cross detection
    k_crossed_up = k_now > d_now and k_prev <= d_prev
    k_crossed_dn = k_now < d_now and k_prev >= d_prev

    signal: Signal = "NONE"

    if k_crossed_up and macd_now > 0 and hist_now > 0:
        signal = "LONG"
        logger.info("LONG: StochK cross up (K=%.1f>D=%.1f), MACD=%.3f, hist=%.3f",
                     k_now, d_now, macd_now, hist_now)

    elif k_crossed_dn and macd_now < 0 and rsi_now < rsi_threshold:
        signal = "SHORT"
        logger.info("SHORT: StochK cross down (K=%.1f<D=%.1f), MACD=%.3f, RSI=%.1f",
                     k_now, d_now, macd_now, rsi_now)

    return {
        "signal": signal,
        "price": price,
        "stoch_k": k_now,
        "stoch_d": d_now,
        "macd": macd_now,
        "macd_signal": signal_line[-1],
        "macd_hist": hist_now,
        "rsi": rsi_now,
    }


def check_exit(current_candle: Candle, entry_side: str, entry_price: float,
               tp_pct: float = 1.5, sl_pct: float = 1.2
               ) -> tuple[bool, Optional[str], Optional[float]]:
    """Check TP/SL. No signal-flip exits."""
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


def calculate_size(price: float, size_usd: float, leverage: int) -> float:
    raw = (size_usd * leverage) / price
    return math.floor(raw * 10_000) / 10_000
