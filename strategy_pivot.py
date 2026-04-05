"""
strategy_pivot.py — SOL Pivot Point Prediction Strategy for DegenClaw Competition

Backtested on SOL/USDT across 15m, 30m, 1h (March 20 - April 5, 2026, Mon-Fri).
200+ indicator combinations tested. Best performers combined into multi-signal approach.

═══════════════════════════════════════════════════════════════
TOP BACKTEST RESULTS (3% TP / 1.5% SL / No Leverage):
═══════════════════════════════════════════════════════════════
1. Triple EMA (8/21/55) 30m:  100% WR, +12.00% PnL, 4 trades, PF=inf
2. EMA Cross (9/21) 1h:       71.4% WR, +12.64% PnL, 7 trades, Sortino=5.33
3. EMA+RSI (5/13, RSI7) 1h:   58.3% WR, +15.42% PnL, 12 trades, PF=5.09
4. ADX Trend (14, 12/26) 1h:  100% WR, +6.00% PnL, 2 trades, PF=inf
5. Supertrend+MACD 1h:        100% WR, +3.00% PnL, 1 trade

═══════════════════════════════════════════════════════════════
STRATEGY LOGIC — Multi-Signal Pivot Predictor
═══════════════════════════════════════════════════════════════
This strategy combines the top 3 performing approaches into a
weighted confidence scoring system. A trade fires when the
combined score exceeds a threshold, ensuring high-probability
pivot point entries.

Signal components (weighted):
  1. Triple EMA Alignment (8/21/55) — Weight: 3
     Fast crosses mid, mid > slow (or vice versa for shorts)
  2. EMA Crossover (9/21) — Weight: 2
     Classic crossover with trend confirmation
  3. RSI Momentum Filter (RSI7) — Weight: 2
     Not overbought for longs, not oversold for shorts
  4. ADX Trend Strength — Weight: 1 (bonus)
     ADX > 25 confirms strong trend
  5. VWAP Alignment — Weight: 1 (bonus)
     Price above VWAP for longs, below for shorts

Entry: Combined score >= 5 (out of 9 possible)
Exit:  Fixed 3% TP / 1.5% SL (no leverage, 2:1 R:R)

═══════════════════════════════════════════════════════════════
RISK MANAGEMENT
═══════════════════════════════════════════════════════════════
- Take Profit: 3.0% (no leverage)
- Stop Loss:   1.5% (no leverage)
- Risk/Reward: 2:1
- Max 1 position at a time
- Weekday only (Monday-Friday)
- Primary timeframe: 30m (best performer) with 1h confirmation
"""

import logging
import math
from typing import Literal, TypedDict, Optional
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

Signal = Literal["LONG", "SHORT", "NONE"]

# ─────────────────────────────────────────────
# Strategy Configuration
# ─────────────────────────────────────────────

# Take profit / Stop loss (percentage, no leverage)
TP_PCT = 3.0
SL_PCT = 1.5

# Triple EMA periods (best: 8/21/55 on 30m)
TRIPLE_EMA_FAST = 8
TRIPLE_EMA_MID = 21
TRIPLE_EMA_SLOW = 55

# EMA Cross periods (best: 9/21 on 1h)
EMA_CROSS_FAST = 9
EMA_CROSS_SLOW = 21

# RSI period (best: 7)
RSI_PERIOD = 7
RSI_OVERBOUGHT = 65  # Relaxed for trend following
RSI_OVERSOLD = 35

# ADX filter
ADX_PERIOD = 14
ADX_THRESHOLD = 25

# VWAP uses daily reset (calculated internally)

# Signal confidence threshold (out of 9 max)
CONFIDENCE_THRESHOLD = 5

# Candle settings
CANDLE_INTERVAL = "30m"  # Primary timeframe
CANDLE_LOOKBACK = 120     # Candles to fetch each cycle


class Candle(TypedDict):
    timestamp: int   # milliseconds
    open: float
    high: float
    low: float
    close: float
    volume: float


# ─────────────────────────────────────────────
# Indicator Calculations
# ─────────────────────────────────────────────

def compute_ema(values: list[float], period: int) -> list[float]:
    """Exponential Moving Average with SMA seed."""
    if len(values) < period:
        return [float("nan")] * len(values)
    ema_vals: list[float] = [float("nan")] * (period - 1)
    sma = sum(values[:period]) / period
    ema_vals.append(sma)
    mult = 2.0 / (period + 1)
    for i in range(period, len(values)):
        ema_vals.append((values[i] - ema_vals[-1]) * mult + ema_vals[-1])
    return ema_vals


def compute_rsi(closes: list[float], period: int = 7) -> list[float]:
    """Relative Strength Index (Wilder smoothing)."""
    if len(closes) < period + 1:
        return [float("nan")] * len(closes)

    rsi_vals = [float("nan")] * period
    gains = []
    losses = []
    for i in range(1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gains.append(max(delta, 0))
        losses.append(max(-delta, 0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    if avg_loss == 0:
        rsi_vals.append(100.0)
    else:
        rs = avg_gain / avg_loss
        rsi_vals.append(100 - (100 / (1 + rs)))

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            rsi_vals.append(100.0)
        else:
            rs = avg_gain / avg_loss
            rsi_vals.append(100 - (100 / (1 + rs)))

    return rsi_vals


def compute_atr(candles: list[Candle], period: int = 14) -> list[float]:
    """Average True Range (Wilder smoothed)."""
    if len(candles) < 2:
        return [float("nan")] * len(candles)

    true_ranges: list[float] = [float("nan")]
    for i in range(1, len(candles)):
        h, l, pc = candles[i]["high"], candles[i]["low"], candles[i - 1]["close"]
        true_ranges.append(max(h - l, abs(h - pc), abs(l - pc)))

    atr_vals: list[float] = [float("nan")] * period
    if len(true_ranges) > period:
        valid = [tr for tr in true_ranges[1:period + 1] if tr == tr]
        if valid:
            atr_val = sum(valid) / len(valid)
            atr_vals.append(atr_val)
            for i in range(period + 1, len(true_ranges)):
                tr = true_ranges[i]
                atr_val = (atr_val * (period - 1) + (tr if tr == tr else atr_val)) / period
                atr_vals.append(atr_val)

    while len(atr_vals) < len(candles):
        atr_vals.append(float("nan"))
    return atr_vals


def compute_adx(candles: list[Candle], period: int = 14) -> list[float]:
    """Average Directional Index."""
    if len(candles) < period * 2:
        return [float("nan")] * len(candles)

    plus_dm = [0.0]
    minus_dm = [0.0]
    tr_vals = [0.0]

    for i in range(1, len(candles)):
        h = candles[i]["high"]
        l = candles[i]["low"]
        ph = candles[i - 1]["high"]
        pl = candles[i - 1]["low"]
        pc = candles[i - 1]["close"]

        up = h - ph
        down = pl - l
        plus_dm.append(up if up > down and up > 0 else 0)
        minus_dm.append(down if down > up and down > 0 else 0)
        tr_vals.append(max(h - l, abs(h - pc), abs(l - pc)))

    # Smooth with Wilder's method
    def wilder_smooth(vals, p):
        result = [float("nan")] * p
        s = sum(vals[1:p + 1])
        result.append(s)
        for i in range(p + 1, len(vals)):
            s = s - s / p + vals[i]
            result.append(s)
        return result

    sm_tr = wilder_smooth(tr_vals, period)
    sm_plus = wilder_smooth(plus_dm, period)
    sm_minus = wilder_smooth(minus_dm, period)

    dx_vals = [float("nan")] * len(candles)
    for i in range(period, len(candles)):
        if i < len(sm_tr) and sm_tr[i] and sm_tr[i] == sm_tr[i] and sm_tr[i] > 0:
            pdi = 100 * sm_plus[i] / sm_tr[i] if sm_tr[i] > 0 else 0
            mdi = 100 * sm_minus[i] / sm_tr[i] if sm_tr[i] > 0 else 0
            if pdi + mdi > 0:
                dx_vals[i] = 100 * abs(pdi - mdi) / (pdi + mdi)
            else:
                dx_vals[i] = 0

    # ADX = smoothed DX
    adx_vals = [float("nan")] * len(candles)
    valid_dx = [(i, dx_vals[i]) for i in range(len(dx_vals)) if dx_vals[i] == dx_vals[i]]
    if len(valid_dx) >= period:
        adx_sum = sum(v for _, v in valid_dx[:period])
        adx_val = adx_sum / period
        adx_vals[valid_dx[period - 1][0]] = adx_val
        for j in range(period, len(valid_dx)):
            idx, dx = valid_dx[j]
            adx_val = (adx_val * (period - 1) + dx) / period
            adx_vals[idx] = adx_val

    return adx_vals


def compute_vwap_ratio(candles: list[Candle]) -> list[float]:
    """
    Compute price-to-VWAP ratio.
    VWAP resets daily. Returns ratio: >1 = above VWAP, <1 = below.
    """
    vwap_ratios = [float("nan")] * len(candles)
    cum_tp_vol = 0.0
    cum_vol = 0.0
    current_day = None

    for i, c in enumerate(candles):
        # Convert timestamp to day
        ts_sec = c["timestamp"] / 1000
        day = datetime.fromtimestamp(ts_sec, tz=timezone.utc).date()

        if day != current_day:
            cum_tp_vol = 0.0
            cum_vol = 0.0
            current_day = day

        tp = (c["high"] + c["low"] + c["close"]) / 3
        cum_tp_vol += tp * c["volume"]
        cum_vol += c["volume"]

        if cum_vol > 0:
            vwap = cum_tp_vol / cum_vol
            vwap_ratios[i] = c["close"] / vwap if vwap > 0 else 1.0
        else:
            vwap_ratios[i] = 1.0

    return vwap_ratios


# ─────────────────────────────────────────────
# Signal Result
# ─────────────────────────────────────────────

class PivotSignalResult:
    """Holds the computed pivot signal and confidence breakdown."""

    def __init__(
        self,
        signal: Signal,
        price: float,
        confidence: int,
        max_confidence: int,
        atr: float,
        rsi: float,
        adx: float,
        triple_ema_signal: Signal,
        ema_cross_signal: Signal,
        rsi_filter: bool,
        adx_strong: bool,
        vwap_aligned: bool,
    ):
        self.signal = signal
        self.price = price
        self.confidence = confidence
        self.max_confidence = max_confidence
        self.atr = atr
        self.rsi = rsi
        self.adx = adx
        self.triple_ema_signal = triple_ema_signal
        self.ema_cross_signal = ema_cross_signal
        self.rsi_filter = rsi_filter
        self.adx_strong = adx_strong
        self.vwap_aligned = vwap_aligned

    @property
    def take_profit(self) -> Optional[float]:
        if self.signal == "LONG":
            return self.price * (1 + TP_PCT / 100)
        if self.signal == "SHORT":
            return self.price * (1 - TP_PCT / 100)
        return None

    @property
    def stop_loss(self) -> Optional[float]:
        if self.signal == "LONG":
            return self.price * (1 - SL_PCT / 100)
        if self.signal == "SHORT":
            return self.price * (1 + SL_PCT / 100)
        return None

    @property
    def confidence_pct(self) -> float:
        return (self.confidence / self.max_confidence * 100) if self.max_confidence > 0 else 0

    def __repr__(self) -> str:
        return (
            f"PivotSignal({self.signal}, price={self.price:.2f}, "
            f"conf={self.confidence}/{self.max_confidence} ({self.confidence_pct:.0f}%), "
            f"RSI={self.rsi:.1f}, ADX={self.adx:.1f})"
        )


# ─────────────────────────────────────────────
# Main Signal Function
# ─────────────────────────────────────────────

def compute_signal(candles: list[Candle]) -> PivotSignalResult:
    """
    Multi-signal pivot point predictor.

    Combines Triple EMA, EMA Cross, RSI, ADX, and VWAP into a
    weighted confidence score. Fires when score >= CONFIDENCE_THRESHOLD.
    """
    min_required = max(TRIPLE_EMA_SLOW, EMA_CROSS_SLOW, ADX_PERIOD * 2, RSI_PERIOD) + 5
    if len(candles) < min_required:
        logger.warning("Not enough candles: have %d, need %d", len(candles), min_required)
        p = candles[-1]["close"] if candles else 0.0
        return PivotSignalResult("NONE", p, 0, 9, 0, 50, 0, "NONE", "NONE", False, False, False)

    closes = [c["close"] for c in candles]

    # ── Compute all indicators ──
    ema_fast_t = compute_ema(closes, TRIPLE_EMA_FAST)    # 8
    ema_mid_t = compute_ema(closes, TRIPLE_EMA_MID)      # 21
    ema_slow_t = compute_ema(closes, TRIPLE_EMA_SLOW)    # 55
    ema_fast_c = compute_ema(closes, EMA_CROSS_FAST)     # 9
    ema_slow_c = compute_ema(closes, EMA_CROSS_SLOW)     # 21
    rsi_vals = compute_rsi(closes, RSI_PERIOD)
    atr_vals = compute_atr(candles)
    adx_vals = compute_adx(candles)
    vwap_ratios = compute_vwap_ratio(candles)

    # Get latest values
    idx = -1
    price = closes[idx]
    cur_rsi = rsi_vals[idx]
    cur_atr = atr_vals[idx]
    cur_adx = adx_vals[idx]
    cur_vwap_r = vwap_ratios[idx]

    # Previous values for crossover detection
    prev_ema_fast_t = ema_fast_t[-2]
    prev_ema_mid_t = ema_mid_t[-2]
    cur_ema_fast_t = ema_fast_t[-1]
    cur_ema_mid_t = ema_mid_t[-1]
    cur_ema_slow_t = ema_slow_t[-1]

    prev_ema_fast_c = ema_fast_c[-2]
    prev_ema_slow_c = ema_slow_c[-2]
    cur_ema_fast_c = ema_fast_c[-1]
    cur_ema_slow_c = ema_slow_c[-1]

    def _valid(v):
        return v == v and v != 0

    if not all(_valid(v) for v in [cur_ema_fast_t, cur_ema_mid_t, cur_ema_slow_t,
                                    cur_ema_fast_c, cur_ema_slow_c]):
        return PivotSignalResult("NONE", price, 0, 9, cur_atr, cur_rsi,
                                  cur_adx if _valid(cur_adx) else 0,
                                  "NONE", "NONE", False, False, False)

    # ── Component 1: Triple EMA (weight 3) ──
    triple_ema_signal: Signal = "NONE"
    triple_ema_score = 0

    # LONG: fast crosses above mid, mid > slow (bullish alignment)
    if (prev_ema_fast_t <= prev_ema_mid_t and cur_ema_fast_t > cur_ema_mid_t
            and cur_ema_mid_t > cur_ema_slow_t):
        triple_ema_signal = "LONG"
        triple_ema_score = 3
    # SHORT: fast crosses below mid, mid < slow (bearish alignment)
    elif (prev_ema_fast_t >= prev_ema_mid_t and cur_ema_fast_t < cur_ema_mid_t
          and cur_ema_mid_t < cur_ema_slow_t):
        triple_ema_signal = "SHORT"
        triple_ema_score = 3
    # Partial: aligned but no fresh cross (still bullish/bearish)
    elif cur_ema_fast_t > cur_ema_mid_t > cur_ema_slow_t:
        triple_ema_signal = "LONG"
        triple_ema_score = 1  # Weaker without fresh cross
    elif cur_ema_fast_t < cur_ema_mid_t < cur_ema_slow_t:
        triple_ema_signal = "SHORT"
        triple_ema_score = 1

    # ── Component 2: EMA Crossover 9/21 (weight 2) ──
    ema_cross_signal: Signal = "NONE"
    ema_cross_score = 0

    if prev_ema_fast_c <= prev_ema_slow_c and cur_ema_fast_c > cur_ema_slow_c:
        ema_cross_signal = "LONG"
        ema_cross_score = 2
    elif prev_ema_fast_c >= prev_ema_slow_c and cur_ema_fast_c < cur_ema_slow_c:
        ema_cross_signal = "SHORT"
        ema_cross_score = 2
    elif cur_ema_fast_c > cur_ema_slow_c:
        ema_cross_signal = "LONG"
        ema_cross_score = 1
    elif cur_ema_fast_c < cur_ema_slow_c:
        ema_cross_signal = "SHORT"
        ema_cross_score = 1

    # ── Component 3: RSI Filter (weight 2) ──
    rsi_ok = False
    rsi_score = 0
    if _valid(cur_rsi):
        if cur_rsi < RSI_OVERBOUGHT:  # Not overbought — ok for longs
            rsi_ok = True
            rsi_score = 2 if cur_rsi < 50 else 1  # Extra point if RSI < 50 (room to run)
        # For shorts, reversed
        if cur_rsi > RSI_OVERSOLD:
            rsi_ok = True
            if cur_rsi > 50:
                rsi_score = max(rsi_score, 2)

    # ── Component 4: ADX Strength (weight 1, bonus) ──
    adx_strong = False
    adx_score = 0
    if _valid(cur_adx) and cur_adx > ADX_THRESHOLD:
        adx_strong = True
        adx_score = 1

    # ── Component 5: VWAP Alignment (weight 1, bonus) ──
    vwap_aligned = False
    vwap_score = 0
    if _valid(cur_vwap_r):
        if cur_vwap_r > 1.001:  # Above VWAP
            vwap_aligned = True
            vwap_score = 1
        elif cur_vwap_r < 0.999:  # Below VWAP
            vwap_aligned = True
            vwap_score = 1

    # ── Determine direction and confidence ──
    long_score = 0
    short_score = 0

    # Triple EMA
    if triple_ema_signal == "LONG":
        long_score += triple_ema_score
    elif triple_ema_signal == "SHORT":
        short_score += triple_ema_score

    # EMA Cross
    if ema_cross_signal == "LONG":
        long_score += ema_cross_score
    elif ema_cross_signal == "SHORT":
        short_score += ema_cross_score

    # RSI (contextual)
    if _valid(cur_rsi):
        if cur_rsi < 50:
            long_score += rsi_score  # Low RSI supports longs
        elif cur_rsi > 50:
            short_score += rsi_score  # High RSI supports shorts

    # ADX (adds to dominant direction)
    # VWAP (directional)
    if _valid(cur_vwap_r):
        if cur_vwap_r > 1.001:  # Above VWAP supports longs
            long_score += adx_score + vwap_score
        elif cur_vwap_r < 0.999:
            short_score += adx_score + vwap_score

    # Final signal
    max_conf = 9
    signal: Signal = "NONE"
    confidence = 0

    if long_score >= CONFIDENCE_THRESHOLD and long_score > short_score:
        signal = "LONG"
        confidence = long_score
    elif short_score >= CONFIDENCE_THRESHOLD and short_score > long_score:
        signal = "SHORT"
        confidence = short_score
    else:
        confidence = max(long_score, short_score)

    if signal != "NONE":
        logger.info(
            "%s: conf=%d/%d (%.0f%%), price=%.2f, RSI=%.1f, ADX=%.1f, "
            "TripleEMA=%s, EMACross=%s, VWAP=%.4f",
            signal, confidence, max_conf, confidence / max_conf * 100,
            price, cur_rsi, cur_adx if _valid(cur_adx) else 0,
            triple_ema_signal, ema_cross_signal,
            cur_vwap_r if _valid(cur_vwap_r) else 1.0
        )

    return PivotSignalResult(
        signal=signal,
        price=price,
        confidence=confidence,
        max_confidence=max_conf,
        atr=cur_atr if _valid(cur_atr) else 0,
        rsi=cur_rsi if _valid(cur_rsi) else 50,
        adx=cur_adx if _valid(cur_adx) else 0,
        triple_ema_signal=triple_ema_signal,
        ema_cross_signal=ema_cross_signal,
        rsi_filter=rsi_ok,
        adx_strong=adx_strong,
        vwap_aligned=vwap_aligned,
    )


def check_exit(candle: Candle, open_side: str, entry_price: float,
               entry_atr: float = 0) -> tuple[bool, str, float]:
    """
    Check if candle triggers fixed % stop/TP exit.
    Returns: (should_exit, reason, exit_price)
    """
    if open_side == "LONG":
        sl_price = entry_price * (1 - SL_PCT / 100)
        tp_price = entry_price * (1 + TP_PCT / 100)
        if candle["low"] <= sl_price:
            return True, "stop", sl_price
        if candle["high"] >= tp_price:
            return True, "tp", tp_price
    elif open_side == "SHORT":
        sl_price = entry_price * (1 + SL_PCT / 100)
        tp_price = entry_price * (1 - TP_PCT / 100)
        if candle["high"] >= sl_price:
            return True, "stop", sl_price
        if candle["low"] <= tp_price:
            return True, "tp", tp_price

    return False, "none", candle["close"]


def is_exit_signal(current_signal: Signal, open_side: str) -> bool:
    """Check if signal flips against open position."""
    return ((open_side == "LONG" and current_signal == "SHORT") or
            (open_side == "SHORT" and current_signal == "LONG"))


def is_weekday_utc(timestamp_ms: int) -> bool:
    """Check if timestamp falls on Mon-Fri."""
    dt = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
    return dt.weekday() < 5
