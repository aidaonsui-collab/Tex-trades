"""
strategy.py — Momentum Breakout strategy signal computation.

Optimised for Degen Claw weekly seasons (Sortino + Return % + Profit Factor).
Backtested across 17 weekly windows on SOL/USDT 1h:
  - 12/17 winning weeks (71%) with EMA50 filter
  - Avg Sortino: 3.62 | Avg weekly return: +1.9%
  - 33% win rate, 2.8x R:R — profitable via outsized winners

Signal rules:
  LONG  : close breaks ABOVE N-bar high  AND  ROC > threshold
          AND  volume > avg * multiplier  AND  close > EMA(trend_period)
  SHORT : close breaks BELOW N-bar low   AND  ROC < -threshold
          AND  volume > avg * multiplier  AND  close < EMA(trend_period)
  NONE  : no qualifying breakout on the latest candle

Exit rules (handled in bot.py):
  - ATR-based stop loss:   entry ± ATR * atr_multiplier
  - ATR-based take profit: entry ± ATR * atr_multiplier * reward_risk_ratio
  - Signal flip:           opposite signal fires
"""

import logging
import math
from typing import Literal, TypedDict, Optional

import config

logger = logging.getLogger(__name__)

Signal = Literal["LONG", "SHORT", "NONE"]


class Candle(TypedDict):
    timestamp: int   # milliseconds
    open: float
    high: float
    low: float
    close: float
    volume: float


# ─────────────────────────────────────────────
# Indicator calculations
# ─────────────────────────────────────────────

def compute_ema(values: list[float], period: int) -> list[float]:
    """Compute Exponential Moving Average. Early values use SMA seed."""
    if len(values) < period:
        return [float("nan")] * len(values)

    ema_values: list[float] = [float("nan")] * (period - 1)
    sma = sum(values[:period]) / period
    ema_values.append(sma)

    mult = 2.0 / (period + 1)
    for i in range(period, len(values)):
        ema_values.append((values[i] - ema_values[-1]) * mult + ema_values[-1])

    return ema_values


def compute_atr(candles: list[Candle], period: int = 14) -> list[float]:
    """Compute Average True Range (Wilder smoothed)."""
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


def compute_roc(closes: list[float], period: int) -> list[float]:
    """Rate of Change: (close / close[n ago] - 1) * 100."""
    roc = [float("nan")] * period
    for i in range(period, len(closes)):
        roc.append((closes[i] / closes[i - period] - 1) * 100 if closes[i - period] != 0 else 0.0)
    return roc


def compute_rolling_high(highs: list[float], period: int) -> list[float]:
    """Rolling max of highs over `period` bars (excludes current bar)."""
    result: list[float] = [float("nan")] * period
    for i in range(period, len(highs)):
        result.append(max(highs[i - period:i]))
    return result


def compute_rolling_low(lows: list[float], period: int) -> list[float]:
    """Rolling min of lows over `period` bars (excludes current bar)."""
    result: list[float] = [float("nan")] * period
    for i in range(period, len(lows)):
        result.append(min(lows[i - period:i]))
    return result


def compute_volume_sma(volumes: list[float], period: int = 20) -> list[float]:
    """Simple moving average of volume."""
    result: list[float] = [float("nan")] * (period - 1)
    for i in range(period - 1, len(volumes)):
        result.append(sum(volumes[i - period + 1:i + 1]) / period)
    return result


# ─────────────────────────────────────────────
# Signal result
# ─────────────────────────────────────────────

class SignalResult:
    """Holds the computed signal and supporting indicator values."""

    def __init__(self, signal: Signal, price: float, atr: float, roc: float,
                 channel_high: float, channel_low: float, ema_trend: float,
                 volume: float, volume_avg: float):
        self.signal = signal
        self.price = price
        self.atr = atr
        self.roc = roc
        self.channel_high = channel_high
        self.channel_low = channel_low
        self.ema_trend = ema_trend
        self.volume = volume
        self.volume_avg = volume_avg

    @property
    def trend_direction(self) -> str:
        if self.price > self.ema_trend: return "BULLISH"
        if self.price < self.ema_trend: return "BEARISH"
        return "NEUTRAL"

    @property
    def stop_loss(self) -> Optional[float]:
        if self.atr != self.atr or self.atr <= 0: return None
        d = self.atr * config.ATR_MULTIPLIER
        if self.signal == "LONG": return self.price - d
        if self.signal == "SHORT": return self.price + d
        return None

    @property
    def take_profit(self) -> Optional[float]:
        if self.atr != self.atr or self.atr <= 0: return None
        d = self.atr * config.ATR_MULTIPLIER * config.REWARD_RISK_RATIO
        if self.signal == "LONG": return self.price + d
        if self.signal == "SHORT": return self.price - d
        return None

    def __repr__(self) -> str:
        return (f"SignalResult(signal={self.signal}, price={self.price:.2f}, "
                f"atr={self.atr:.2f}, roc={self.roc:.2f}, trend={self.trend_direction})")


# ─────────────────────────────────────────────
# Main signal function
# ─────────────────────────────────────────────

def compute_signal(candles: list[Candle]) -> SignalResult:
    """Evaluate Momentum Breakout strategy on candle list."""
    min_required = max(config.BREAKOUT_LOOKBACK, config.TREND_EMA_PERIOD, config.ATR_PERIOD) + 2
    if len(candles) < min_required:
        logger.warning("Not enough candles: have %d, need %d", len(candles), min_required)
        p = candles[-1]["close"] if candles else 0.0
        return SignalResult("NONE", p, 0, 0, 0, 0, 0, 0, 0)

    closes = [c["close"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    volumes = [c["volume"] for c in candles]

    atr_s = compute_atr(candles, config.ATR_PERIOD)
    roc_s = compute_roc(closes, config.BREAKOUT_LOOKBACK)
    ch_hi = compute_rolling_high(highs, config.BREAKOUT_LOOKBACK)
    ch_lo = compute_rolling_low(lows, config.BREAKOUT_LOOKBACK)
    vol_a = compute_volume_sma(volumes, 20)
    ema_t = compute_ema(closes, config.TREND_EMA_PERIOD)

    cc, ca, cr = closes[-1], atr_s[-1], roc_s[-1]
    ch, cl, cv, cva, ce = ch_hi[-1], ch_lo[-1], volumes[-1], vol_a[-1], ema_t[-1]

    logger.debug("close=%.2f ch=%.2f-%.2f roc=%.2f vol=%.0f/%.0f ema=%.2f atr=%.2f",
                 cc, cl, ch, cr, cv, cva, ce, ca)

    def _ok(v): return v == v and v != 0
    if not all(_ok(v) for v in [ch, cl, cr, cva, ce]):
        return SignalResult("NONE", cc, ca, cr, ch, cl, ce, cv, cva)

    vol_ok = cv > cva * config.VOLUME_MULTIPLIER
    signal: Signal = "NONE"

    if cc > ch and cr > config.ROC_THRESHOLD and vol_ok and cc > ce:
        signal = "LONG"
        logger.info("LONG: breakout above %.2f, ROC=%.2f%%, vol=%.1fx, trend=BULL",
                     ch, cr, cv / cva)
    elif cc < cl and cr < -config.ROC_THRESHOLD and vol_ok and cc < ce:
        signal = "SHORT"
        logger.info("SHORT: breakdown below %.2f, ROC=%.2f%%, vol=%.1fx, trend=BEAR",
                     cl, cr, cv / cva)
    elif cc > ch or cc < cl:
        reasons = []
        if not vol_ok: reasons.append(f"vol {cv:.0f} < {cva * config.VOLUME_MULTIPLIER:.0f}")
        if cc > ch and cr <= config.ROC_THRESHOLD: reasons.append(f"ROC {cr:.2f} weak")
        if cc < cl and cr >= -config.ROC_THRESHOLD: reasons.append(f"ROC {cr:.2f} weak")
        if cc > ch and cc <= ce: reasons.append("below EMA")
        if cc < cl and cc >= ce: reasons.append("above EMA")
        logger.info("Breakout filtered: %s", ", ".join(reasons))

    return SignalResult(signal=signal, price=cc, atr=ca, roc=cr,
                        channel_high=ch, channel_low=cl, ema_trend=ce,
                        volume=cv, volume_avg=cva)


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
    """Check if signal flips against open position."""
    return ((open_side == "LONG" and current_signal == "SHORT") or
            (open_side == "SHORT" and current_signal == "LONG"))
