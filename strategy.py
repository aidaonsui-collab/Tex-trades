"""
strategy.py — VWAP Cross strategy signal computation.

Signal rules:
  LONG  : price crosses ABOVE VWAP  AND  RSI(14) is in [RSI_LOWER, RSI_UPPER]
  SHORT : price crosses BELOW VWAP  AND  RSI(14) is in [RSI_LOWER, RSI_UPPER]
  NONE  : no qualifying cross on the latest candle

All logic operates on a list of OHLCV dicts as returned by exchange.get_candles().
"""

import logging
from typing import Literal, TypedDict

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
# Helper calculations
# ─────────────────────────────────────────────

def compute_vwap(candles: list[Candle]) -> list[float]:
    """
    Compute session VWAP for each candle using cumulative TP*V / cumV.
    We treat all provided candles as one session window (rolling VWAP).
    Returns a list of VWAP values aligned with the input candles.
    """
    cumulative_tpv = 0.0   # sum of (typical_price * volume)
    cumulative_vol = 0.0   # sum of volume
    vwap_values: list[float] = []

    for c in candles:
        typical_price = (c["high"] + c["low"] + c["close"]) / 3.0
        cumulative_tpv += typical_price * c["volume"]
        cumulative_vol += c["volume"]
        vwap = cumulative_tpv / cumulative_vol if cumulative_vol > 0 else c["close"]
        vwap_values.append(vwap)

    return vwap_values


def compute_rsi(closes: list[float], period: int = 14) -> list[float]:
    """
    Compute RSI using Wilder's smoothed moving average method.
    Returns a list of RSI values the same length as `closes`.
    Values before the first full period are returned as NaN (float('nan')).
    """
    if len(closes) < period + 1:
        return [float("nan")] * len(closes)

    rsi_values: list[float] = [float("nan")] * period  # no RSI for first `period` bars

    # Initial average gain/loss over first `period` closes
    gains = []
    losses = []
    for i in range(1, period + 1):
        delta = closes[i] - closes[i - 1]
        gains.append(max(delta, 0))
        losses.append(max(-delta, 0))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    def _rsi_from_avgs(ag: float, al: float) -> float:
        if al == 0:
            return 100.0
        rs = ag / al
        return 100.0 - (100.0 / (1.0 + rs))

    rsi_values.append(_rsi_from_avgs(avg_gain, avg_loss))

    # Wilder smoothing for subsequent candles
    for i in range(period + 1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gain = max(delta, 0)
        loss = max(-delta, 0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        rsi_values.append(_rsi_from_avgs(avg_gain, avg_loss))

    return rsi_values


# ─────────────────────────────────────────────
# Main signal function
# ─────────────────────────────────────────────

class SignalResult:
    """Holds the computed signal and the supporting indicator values."""

    def __init__(
        self,
        signal: Signal,
        price: float,
        vwap: float,
        rsi: float,
        prev_price: float,
        prev_vwap: float,
    ):
        self.signal = signal
        self.price = price         # latest close
        self.vwap = vwap           # latest VWAP
        self.rsi = rsi             # latest RSI
        self.prev_price = prev_price
        self.prev_vwap = prev_vwap

    def __repr__(self) -> str:
        return (
            f"SignalResult(signal={self.signal}, price={self.price:.2f}, "
            f"vwap={self.vwap:.2f}, rsi={self.rsi:.2f})"
        )


def compute_signal(candles: list[Candle]) -> SignalResult:
    """
    Evaluate the VWAP Cross strategy on the given candle list and return a
    SignalResult describing the signal on the *most recent completed candle*.

    A minimum of (RSI_PERIOD + 2) candles is required — fewer returns NONE.
    """
    min_required = config.RSI_PERIOD + 2
    if len(candles) < min_required:
        logger.warning(
            "Not enough candles to compute signal: have %d, need %d",
            len(candles),
            min_required,
        )
        price = candles[-1]["close"] if candles else 0.0
        return SignalResult("NONE", price, price, float("nan"), price, price)

    closes = [c["close"] for c in candles]
    vwap_series = compute_vwap(candles)
    rsi_series = compute_rsi(closes, config.RSI_PERIOD)

    # Operate on the last two completed candles
    prev_close = closes[-2]
    prev_vwap = vwap_series[-2]
    curr_close = closes[-1]
    curr_vwap = vwap_series[-1]
    curr_rsi = rsi_series[-1]

    logger.debug(
        "Indicators — close=%.2f  vwap=%.2f  rsi=%.2f  "
        "prev_close=%.2f  prev_vwap=%.2f",
        curr_close, curr_vwap, curr_rsi, prev_close, prev_vwap,
    )

    # Determine if RSI is in the neutral zone
    rsi_valid = (
        not (curr_rsi != curr_rsi)  # not NaN check
        and config.RSI_LOWER <= curr_rsi <= config.RSI_UPPER
    )

    # Detect VWAP cross direction
    was_below = prev_close < prev_vwap
    is_above = curr_close > curr_vwap
    was_above = prev_close > prev_vwap
    is_below = curr_close < curr_vwap

    crossed_above = was_below and is_above   # bullish cross
    crossed_below = was_above and is_below   # bearish cross

    signal: Signal = "NONE"
    if crossed_above and rsi_valid:
        signal = "LONG"
        logger.info("LONG signal: price crossed above VWAP, RSI=%.2f", curr_rsi)
    elif crossed_below and rsi_valid:
        signal = "SHORT"
        logger.info("SHORT signal: price crossed below VWAP, RSI=%.2f", curr_rsi)
    else:
        if crossed_above or crossed_below:
            logger.info(
                "VWAP cross detected but RSI=%.2f outside neutral zone [%.0f-%.0f] — no signal",
                curr_rsi, config.RSI_LOWER, config.RSI_UPPER,
            )

    return SignalResult(
        signal=signal,
        price=curr_close,
        vwap=curr_vwap,
        rsi=curr_rsi,
        prev_price=prev_close,
        prev_vwap=prev_vwap,
    )


def is_exit_signal(current_signal: Signal, open_side: str) -> bool:
    """
    Determine whether the current signal means we should exit an open position.
    Exit rule: signal flips to the opposite side.

      open_side="LONG"  → exit on "SHORT" signal
      open_side="SHORT" → exit on "LONG"  signal
    """
    if open_side == "LONG" and current_signal == "SHORT":
        return True
    if open_side == "SHORT" and current_signal == "LONG":
        return True
    return False
