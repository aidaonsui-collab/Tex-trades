"""
strategy_weekend.py — MACD Cross strategy for SOL weekends.

Backtested on weekends (Jan 1 - Mar 28, 2026):
  - SOL MACD Cross: 55.6% win rate, +$139.23 over ~25 weekend days
  - Best performing weekend strategy across 8 strategies tested
  - 18 trades over 25 weekend days (~0.7/day)

Signal rules:
  LONG  : MACD histogram crosses ABOVE zero (12,26,9)
  SHORT : MACD histogram crosses BELOW zero
  NONE  : MACD histogram stays on same side of zero

Exit rules (handled in bot.py):
  - ATR-based stop loss:   entry ± ATR * atr_multiplier
  - ATR-based take profit: entry ± ATR * atr_multiplier * reward_risk_ratio
  - Signal flip:           opposite signal fires (MACD crosses back)
"""

import logging
from typing import Literal, TypedDict

import config

logger = logging.getLogger(__name__)

Signal = Literal["LONG", "SHORT", "NONE"]


class Candle(TypedDict):
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float


# ─────────────────────────────────────────────
# Indicator calculations
# ─────────────────────────────────────────────

def compute_ema(values: list[float], period: int) -> list[float]:
    """Compute Exponential Moving Average."""
    if len(values) < period:
        return [float("nan")] * len(values)

    ema_values: list[float] = [float("nan")] * (period - 1)
    sma = sum(values[:period]) / period
    ema_values.append(sma)

    mult = 2.0 / (period + 1)
    for i in range(period, len(values)):
        ema_values.append((values[i] - ema_values[-1]) * mult + ema_values[-1])

    return ema_values


def compute_macd(closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9):
    """
    Compute MACD line, signal line, and histogram.
    Returns: (macd_line, signal_line, histogram)
    """
    ema_fast = compute_ema(closes, fast)
    ema_slow = compute_ema(closes, slow)
    
    macd_line = []
    for i in range(len(closes)):
        f = ema_fast[i]
        s = ema_slow[i]
        if f == f and s == s:  # both valid
            macd_line.append(f - s)
        else:
            macd_line.append(float("nan"))
    
    signal_line = compute_ema(macd_line, signal)
    
    histogram = []
    for i in range(len(closes)):
        m = macd_line[i]
        s = signal_line[i]
        if m == m and s == s:
            histogram.append(m - s)
        else:
            histogram.append(float("nan"))
    
    return macd_line, signal_line, histogram


def compute_atr(candles: list[Candle], period: int = 14) -> list[float]:
    """Compute Average True Range."""
    if len(candles) < 2:
        return [float("nan")] * len(candles)

    true_ranges: list[float] = [float("nan")]
    for i in range(1, len(candles)):
        h, l, pc = candles[i]["high"], candles[i]["low"], candles[i - 1]["close"]
        true_ranges.append(max(h - l, abs(h - pc), abs(l - pc)))

    atr_values: list[float] = [float("nan")] * period
    if len(true_ranges) > period:
        valid = [tr for tr in true_ranges[1:period + 1] if tr == tr]
        if valid:
            atr_val = sum(valid) / len(valid)
            atr_values.append(atr_val)
            for i in range(period + 1, len(true_ranges)):
                tr = true_ranges[i]
                atr_val = (atr_val * (period - 1) + (tr if tr == tr else atr_val)) / period
                atr_values.append(atr_val)

    while len(atr_values) < len(candles):
        atr_values.append(float("nan"))
    return atr_values


# ─────────────────────────────────────────────
# Signal result
# ─────────────────────────────────────────────

class SignalResult:
    """Holds the computed signal and supporting indicator values."""

    def __init__(self, signal: Signal, price: float, atr: float,
                 macd_hist: float, macd_line: float, signal_line: float):
        self.signal = signal
        self.price = price
        self.atr = atr
        self.macd_hist = macd_hist
        self.macd_line = macd_line
        self.signal_line = signal_line

    @property
    def trend_direction(self) -> str:
        if self.macd_hist > 0: return "BULLISH"
        if self.macd_hist < 0: return "BEARISH"
        return "NEUTRAL"

    @property
    def stop_loss(self) -> float | None:
        if self.atr != self.atr or self.atr <= 0: return None
        d = self.atr * config.ATR_MULTIPLIER
        if self.signal == "LONG": return self.price - d
        if self.signal == "SHORT": return self.price + d
        return None

    @property
    def take_profit(self) -> float | None:
        if self.atr != self.atr or self.atr <= 0: return None
        d = self.atr * config.ATR_MULTIPLIER * config.REWARD_RISK_RATIO
        if self.signal == "LONG": return self.price + d
        if self.signal == "SHORT": return self.price - d
        return None

    def __repr__(self) -> str:
        return (f"SignalResult(signal={self.signal}, price={self.price:.2f}, "
                f"atr={self.atr:.2f}, macd_hist={self.macd_hist:.4f}, trend={self.trend_direction})")


# ─────────────────────────────────────────────
# Main signal function
# ─────────────────────────────────────────────

def compute_signal(candles: list[Candle]) -> SignalResult:
    """
    Evaluate MACD Cross strategy on candle list.
    
    MACD(12,26,9) histogram crossing zero = signal.
    - Histogram crosses from negative to positive = LONG
    - Histogram crosses from positive to negative = SHORT
    """
    min_required = 35  # Need enough for MACD(26) + signal(9)
    if len(candles) < min_required:
        logger.warning("Not enough candles: have %d, need %d", len(candles), min_required)
        p = candles[-1]["close"] if candles else 0.0
        return SignalResult("NONE", p, 0, 0, 0, 0)

    closes = [c["close"] for c in candles]
    atr_vals = compute_atr(candles, config.ATR_PERIOD)
    
    macd_line, signal_line, histogram = compute_macd(closes)
    
    current_hist = histogram[-1]
    prev_hist = histogram[-2]
    
    price = closes[-1]
    atr_val = atr_vals[-1]

    logger.debug("close=%.2f macd=%.4f signal=%.4f hist=%.4f atr=%.2f",
                 price, macd_line[-1] if macd_line[-1] == macd_line[-1] else 0,
                 signal_line[-1] if signal_line[-1] == signal_line[-1] else 0,
                 current_hist if current_hist == current_hist else 0, atr_val)

    # Check for valid values
    if current_hist != current_hist or prev_hist != prev_hist:
        return SignalResult("NONE", price, atr_val, 0, 0, 0)

    signal: Signal = "NONE"

    # MACD histogram crosses zero
    if prev_hist <= 0 and current_hist > 0:
        signal = "LONG"
        logger.info("LONG: MACD histogram crossed above zero (%.4f -> %.4f)", prev_hist, current_hist)
    elif prev_hist >= 0 and current_hist < 0:
        signal = "SHORT"
        logger.info("SHORT: MACD histogram crossed below zero (%.4f -> %.4f)", prev_hist, current_hist)

    return SignalResult(
        signal=signal,
        price=price,
        atr=atr_val,
        macd_hist=current_hist,
        macd_line=macd_line[-1] if macd_line[-1] == macd_line[-1] else 0,
        signal_line=signal_line[-1] if signal_line[-1] == signal_line[-1] else 0
    )


def check_exit(candle: Candle, open_side: str, entry_price: float,
               entry_atr: float) -> tuple[bool, str, float]:
    """
    Check if candle triggers ATR stop/TP exit.
    Returns: (should_exit, reason, exit_price)
    """
    if entry_atr <= 0 or entry_atr != entry_atr:
        return False, "none", candle["close"]

    sd = entry_atr * config.ATR_MULTIPLIER
    td = sd * config.REWARD_RISK_RATIO

    if open_side == "LONG":
        if candle["low"] <= entry_price - sd:
            return True, "stop", entry_price - sd
        if candle["high"] >= entry_price + td:
            return True, "tp", entry_price + td
    elif open_side == "SHORT":
        if candle["high"] >= entry_price + sd:
            return True, "stop", entry_price + sd
        if candle["low"] <= entry_price - td:
            return True, "tp", entry_price - td

    return False, "none", candle["close"]


def is_exit_signal(current_signal: Signal, open_side: str) -> bool:
    """Check if signal flips against open position (MACD crosses back)."""
    return ((open_side == "LONG" and current_signal == "SHORT") or
            (open_side == "SHORT" and current_signal == "LONG"))
