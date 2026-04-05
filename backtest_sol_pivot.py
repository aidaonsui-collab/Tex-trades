"""
backtest_sol_pivot.py — Comprehensive SOL Pivot Point Strategy Backtester

Fetches SOL/USDT data from Binance for the last 2 weeks (Mon-Fri only).
Tests dozens of indicator combinations across 15m, 30m, 1h timeframes.
Target: 3% TP, 1.5% SL, no leverage.

Goal: Win the DegenClaw competition hosted by Virtuals.
"""

import json
import math
import time
import requests
import numpy as np
import pandas as pd
from datetime import datetime, timedelta, timezone
from itertools import product as iter_product
from typing import Optional

# ═══════════════════════════════════════════════════════════════
# DATA FETCHING
# ═══════════════════════════════════════════════════════════════

def fetch_binance_klines(symbol: str, interval: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    """Fetch klines from Binance public API in chunks."""
    url = "https://api.binance.com/api/v3/klines"
    all_data = []
    current_start = start_ms
    limit = 1000

    while current_start < end_ms:
        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": current_start,
            "endTime": end_ms,
            "limit": limit,
        }
        for attempt in range(4):
            try:
                resp = requests.get(url, params=params, timeout=15)
                resp.raise_for_status()
                data = resp.json()
                break
            except Exception as e:
                if attempt < 3:
                    time.sleep(2 ** attempt)
                else:
                    print(f"Failed to fetch data after 4 attempts: {e}")
                    return pd.DataFrame()

        if not data:
            break

        all_data.extend(data)
        current_start = data[-1][0] + 1

        if len(data) < limit:
            break

    if not all_data:
        return pd.DataFrame()

    df = pd.DataFrame(all_data, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_base",
        "taker_buy_quote", "ignore"
    ])

    for col in ["open", "high", "low", "close", "volume", "quote_volume"]:
        df[col] = df[col].astype(float)

    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms", utc=True)
    df.set_index("open_time", inplace=True)

    return df


def filter_weekdays(df: pd.DataFrame) -> pd.DataFrame:
    """Keep only Monday-Friday candles."""
    return df[df.index.dayofweek < 5].copy()


# ═══════════════════════════════════════════════════════════════
# INDICATORS
# ═══════════════════════════════════════════════════════════════

def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()

def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period).mean()

def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def stochastic(df: pd.DataFrame, k_period: int = 14, d_period: int = 3):
    low_min = df["low"].rolling(k_period).min()
    high_max = df["high"].rolling(k_period).max()
    k = 100 * (df["close"] - low_min) / (high_max - low_min + 1e-10)
    d = k.rolling(d_period).mean()
    return k, d

def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = ema(series, fast)
    ema_slow = ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram

def bollinger_bands(series: pd.Series, period: int = 20, std_dev: float = 2.0):
    mid = sma(series, period)
    std = series.rolling(period).std()
    upper = mid + std_dev * std
    lower = mid - std_dev * std
    return upper, mid, lower

def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"].shift(1)
    tr = pd.concat([h - l, (h - c).abs(), (l - c).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()

def vwap(df: pd.DataFrame) -> pd.Series:
    """Session VWAP (resets daily)."""
    typical = (df["high"] + df["low"] + df["close"]) / 3
    cum_tp_vol = (typical * df["volume"]).groupby(df.index.date).cumsum()
    cum_vol = df["volume"].groupby(df.index.date).cumsum()
    return cum_tp_vol / (cum_vol + 1e-10)

def pivot_points(df: pd.DataFrame) -> pd.DataFrame:
    """Classic pivot points from previous session's HLC."""
    daily = df.resample("1D").agg({"high": "max", "low": "min", "close": "last"}).dropna()
    daily["pivot"] = (daily["high"] + daily["low"] + daily["close"]) / 3
    daily["r1"] = 2 * daily["pivot"] - daily["low"]
    daily["s1"] = 2 * daily["pivot"] - daily["high"]
    daily["r2"] = daily["pivot"] + (daily["high"] - daily["low"])
    daily["s2"] = daily["pivot"] - (daily["high"] - daily["low"])
    daily["r3"] = daily["high"] + 2 * (daily["pivot"] - daily["low"])
    daily["s3"] = daily["low"] - 2 * (daily["high"] - daily["pivot"])
    # Shift forward so today uses yesterday's pivots
    return daily.shift(1)

def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average Directional Index."""
    high, low, close = df["high"], df["low"], df["close"]
    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

    tr = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
    atr_val = tr.rolling(period).mean()

    plus_di = 100 * (plus_dm.rolling(period).mean() / (atr_val + 1e-10))
    minus_di = 100 * (minus_dm.rolling(period).mean() / (atr_val + 1e-10))

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-10)
    return dx.rolling(period).mean()

def williams_r(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high_max = df["high"].rolling(period).max()
    low_min = df["low"].rolling(period).min()
    return -100 * (high_max - df["close"]) / (high_max - low_min + 1e-10)

def cci(df: pd.DataFrame, period: int = 20) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3
    sma_tp = sma(tp, period)
    mad = tp.rolling(period).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
    return (tp - sma_tp) / (0.015 * mad + 1e-10)

def supertrend(df: pd.DataFrame, period: int = 10, multiplier: float = 3.0):
    """Supertrend indicator."""
    hl2 = (df["high"] + df["low"]) / 2
    atr_val = atr(df, period)
    upper_band = hl2 + multiplier * atr_val
    lower_band = hl2 - multiplier * atr_val

    st = pd.Series(np.nan, index=df.index)
    direction = pd.Series(1, index=df.index)

    for i in range(period, len(df)):
        if df["close"].iloc[i] > upper_band.iloc[i - 1]:
            direction.iloc[i] = 1
        elif df["close"].iloc[i] < lower_band.iloc[i - 1]:
            direction.iloc[i] = -1
        else:
            direction.iloc[i] = direction.iloc[i - 1]

        if direction.iloc[i] == 1:
            st.iloc[i] = lower_band.iloc[i]
        else:
            st.iloc[i] = upper_band.iloc[i]

    return st, direction


# ═══════════════════════════════════════════════════════════════
# STRATEGY DEFINITIONS
# ═══════════════════════════════════════════════════════════════

def generate_signals(df: pd.DataFrame, strategy_name: str, params: dict) -> pd.Series:
    """Generate LONG/SHORT/NONE signals for a given strategy + params."""
    signals = pd.Series("NONE", index=df.index)
    close = df["close"]

    if strategy_name == "ema_cross":
        fast_p, slow_p = params["fast"], params["slow"]
        ema_fast = ema(close, fast_p)
        ema_slow = ema(close, slow_p)
        # Cross above = LONG, cross below = SHORT
        prev_fast = ema_fast.shift(1)
        prev_slow = ema_slow.shift(1)
        signals[(prev_fast <= prev_slow) & (ema_fast > ema_slow)] = "LONG"
        signals[(prev_fast >= prev_slow) & (ema_fast < ema_slow)] = "SHORT"

    elif strategy_name == "rsi_reversal":
        rsi_val = rsi(close, params["period"])
        oversold, overbought = params["oversold"], params["overbought"]
        prev_rsi = rsi_val.shift(1)
        # Cross back above oversold = LONG, cross back below overbought = SHORT
        signals[(prev_rsi <= oversold) & (rsi_val > oversold)] = "LONG"
        signals[(prev_rsi >= overbought) & (rsi_val < overbought)] = "SHORT"

    elif strategy_name == "macd_cross":
        fast_p, slow_p, sig_p = params["fast"], params["slow"], params["signal"]
        ml, sl, hist = macd(close, fast_p, slow_p, sig_p)
        prev_hist = hist.shift(1)
        signals[(prev_hist <= 0) & (hist > 0)] = "LONG"
        signals[(prev_hist >= 0) & (hist < 0)] = "SHORT"

    elif strategy_name == "bollinger_bounce":
        upper, mid, lower = bollinger_bands(close, params["period"], params["std"])
        prev_close = close.shift(1)
        # Price touches lower band and bounces = LONG
        signals[(prev_close <= lower.shift(1)) & (close > lower)] = "LONG"
        # Price touches upper band and drops = SHORT
        signals[(prev_close >= upper.shift(1)) & (close < upper)] = "SHORT"

    elif strategy_name == "stoch_cross":
        k, d = stochastic(df, params["k_period"], params["d_period"])
        prev_k, prev_d = k.shift(1), d.shift(1)
        signals[(prev_k <= prev_d) & (k > d) & (k < params["oversold"])] = "LONG"
        signals[(prev_k >= prev_d) & (k < d) & (k > params["overbought"])] = "SHORT"

    elif strategy_name == "pivot_breakout":
        pivots = pivot_points(df)
        pivot_r = pivots["pivot"].reindex(df.index.date)
        pivot_r.index = df.index
        r1 = pivots["r1"].reindex(df.index.date)
        r1.index = df.index
        s1 = pivots["s1"].reindex(df.index.date)
        s1.index = df.index
        prev_close = close.shift(1)
        # Break above R1 = LONG, break below S1 = SHORT
        signals[(prev_close < r1) & (close >= r1)] = "LONG"
        signals[(prev_close > s1) & (close <= s1)] = "SHORT"

    elif strategy_name == "vwap_trend":
        vwap_val = vwap(df)
        ema_trend = ema(close, params["ema_period"])
        rsi_val = rsi(close, 14)
        # LONG: price crosses above VWAP + trend bullish + RSI not overbought
        prev_close = close.shift(1)
        signals[(prev_close < vwap_val.shift(1)) & (close > vwap_val) &
                (close > ema_trend) & (rsi_val < 70)] = "LONG"
        signals[(prev_close > vwap_val.shift(1)) & (close < vwap_val) &
                (close < ema_trend) & (rsi_val > 30)] = "SHORT"

    elif strategy_name == "supertrend_follow":
        st, direction = supertrend(df, params["period"], params["multiplier"])
        prev_dir = direction.shift(1)
        signals[(prev_dir == -1) & (direction == 1)] = "LONG"
        signals[(prev_dir == 1) & (direction == -1)] = "SHORT"

    elif strategy_name == "cci_reversal":
        cci_val = cci(df, params["period"])
        prev_cci = cci_val.shift(1)
        signals[(prev_cci <= -100) & (cci_val > -100)] = "LONG"
        signals[(prev_cci >= 100) & (cci_val < 100)] = "SHORT"

    elif strategy_name == "williams_r_reversal":
        wr = williams_r(df, params["period"])
        prev_wr = wr.shift(1)
        signals[(prev_wr <= -80) & (wr > -80)] = "LONG"
        signals[(prev_wr >= -20) & (wr < -20)] = "SHORT"

    elif strategy_name == "macd_rsi_combo":
        ml, sl, hist = macd(close, params["macd_fast"], params["macd_slow"], params["macd_signal"])
        rsi_val = rsi(close, params["rsi_period"])
        prev_hist = hist.shift(1)
        signals[(prev_hist <= 0) & (hist > 0) & (rsi_val < 60)] = "LONG"
        signals[(prev_hist >= 0) & (hist < 0) & (rsi_val > 40)] = "SHORT"

    elif strategy_name == "ema_rsi_combo":
        ema_fast = ema(close, params["fast"])
        ema_slow = ema(close, params["slow"])
        rsi_val = rsi(close, params["rsi_period"])
        prev_fast = ema_fast.shift(1)
        prev_slow = ema_slow.shift(1)
        signals[(prev_fast <= prev_slow) & (ema_fast > ema_slow) & (rsi_val < 65)] = "LONG"
        signals[(prev_fast >= prev_slow) & (ema_fast < ema_slow) & (rsi_val > 35)] = "SHORT"

    elif strategy_name == "bollinger_rsi_combo":
        upper, mid, lower = bollinger_bands(close, params["bb_period"], params["bb_std"])
        rsi_val = rsi(close, params["rsi_period"])
        prev_close = close.shift(1)
        signals[(prev_close <= lower.shift(1)) & (close > lower) & (rsi_val < 35)] = "LONG"
        signals[(prev_close >= upper.shift(1)) & (close < upper) & (rsi_val > 65)] = "SHORT"

    elif strategy_name == "supertrend_macd_combo":
        st, direction = supertrend(df, params["st_period"], params["st_mult"])
        ml, sl, hist = macd(close, params["macd_fast"], params["macd_slow"], 9)
        prev_dir = direction.shift(1)
        signals[(prev_dir == -1) & (direction == 1) & (hist > 0)] = "LONG"
        signals[(prev_dir == 1) & (direction == -1) & (hist < 0)] = "SHORT"

    elif strategy_name == "pivot_vwap_combo":
        pivots = pivot_points(df)
        vwap_val = vwap(df)
        rsi_val = rsi(close, 14)
        pivot_r = pivots["pivot"].reindex(df.index.date)
        pivot_r.index = df.index
        r1 = pivots["r1"].reindex(df.index.date)
        r1.index = df.index
        s1 = pivots["s1"].reindex(df.index.date)
        s1.index = df.index
        prev_close = close.shift(1)
        # LONG: break above pivot + above VWAP + RSI confirmation
        signals[(prev_close < pivot_r) & (close >= pivot_r) & (close > vwap_val) & (rsi_val < 65)] = "LONG"
        signals[(prev_close > pivot_r) & (close <= pivot_r) & (close < vwap_val) & (rsi_val > 35)] = "SHORT"

    elif strategy_name == "triple_ema":
        e1 = ema(close, params["fast"])
        e2 = ema(close, params["mid"])
        e3 = ema(close, params["slow"])
        prev_e1, prev_e2 = e1.shift(1), e2.shift(1)
        # All EMAs aligned + crossover
        signals[(prev_e1 <= prev_e2) & (e1 > e2) & (e2 > e3)] = "LONG"
        signals[(prev_e1 >= prev_e2) & (e1 < e2) & (e2 < e3)] = "SHORT"

    elif strategy_name == "stoch_rsi_combo":
        k, d = stochastic(df, params["k_period"], 3)
        rsi_val = rsi(close, params["rsi_period"])
        prev_k, prev_d = k.shift(1), d.shift(1)
        signals[(prev_k <= prev_d) & (k > d) & (k < 30) & (rsi_val < 40)] = "LONG"
        signals[(prev_k >= prev_d) & (k < d) & (k > 70) & (rsi_val > 60)] = "SHORT"

    elif strategy_name == "adx_trend":
        adx_val = adx(df, params["period"])
        ema_fast = ema(close, params["ema_fast"])
        ema_slow = ema(close, params["ema_slow"])
        prev_fast = ema_fast.shift(1)
        prev_slow = ema_slow.shift(1)
        # Only trade when ADX > threshold (strong trend)
        signals[(prev_fast <= prev_slow) & (ema_fast > ema_slow) & (adx_val > params["threshold"])] = "LONG"
        signals[(prev_fast >= prev_slow) & (ema_fast < ema_slow) & (adx_val > params["threshold"])] = "SHORT"

    return signals


# ═══════════════════════════════════════════════════════════════
# BACKTESTER
# ═══════════════════════════════════════════════════════════════

def backtest(df: pd.DataFrame, signals: pd.Series, tp_pct: float = 3.0, sl_pct: float = 1.5) -> dict:
    """
    Backtest signals with fixed % TP/SL.
    No leverage. One position at a time.
    """
    trades = []
    position = None  # {"side", "entry_price", "entry_time", "tp", "sl"}

    for i in range(len(df)):
        idx = df.index[i]
        row = df.iloc[i]
        sig = signals.iloc[i]

        # Check exit if in position
        if position is not None:
            side = position["side"]
            entry = position["entry_price"]

            if side == "LONG":
                # Check SL first (worst case)
                if row["low"] <= position["sl"]:
                    pnl_pct = ((position["sl"] / entry) - 1) * 100
                    trades.append({
                        "side": side, "entry": entry, "exit": position["sl"],
                        "entry_time": position["entry_time"], "exit_time": idx,
                        "pnl_pct": pnl_pct, "result": "SL"
                    })
                    position = None
                elif row["high"] >= position["tp"]:
                    pnl_pct = ((position["tp"] / entry) - 1) * 100
                    trades.append({
                        "side": side, "entry": entry, "exit": position["tp"],
                        "entry_time": position["entry_time"], "exit_time": idx,
                        "pnl_pct": pnl_pct, "result": "TP"
                    })
                    position = None

            elif side == "SHORT":
                if row["high"] >= position["sl"]:
                    pnl_pct = (1 - (position["sl"] / entry)) * 100
                    trades.append({
                        "side": side, "entry": entry, "exit": position["sl"],
                        "entry_time": position["entry_time"], "exit_time": idx,
                        "pnl_pct": pnl_pct, "result": "SL"
                    })
                    position = None
                elif row["low"] <= position["tp"]:
                    pnl_pct = (1 - (position["tp"] / entry)) * 100
                    trades.append({
                        "side": side, "entry": entry, "exit": position["tp"],
                        "entry_time": position["entry_time"], "exit_time": idx,
                        "pnl_pct": pnl_pct, "result": "TP"
                    })
                    position = None

            # Signal flip exit
            if position is not None:
                if (side == "LONG" and sig == "SHORT") or (side == "SHORT" and sig == "LONG"):
                    exit_price = row["close"]
                    if side == "LONG":
                        pnl_pct = ((exit_price / entry) - 1) * 100
                    else:
                        pnl_pct = (1 - (exit_price / entry)) * 100
                    trades.append({
                        "side": side, "entry": entry, "exit": exit_price,
                        "entry_time": position["entry_time"], "exit_time": idx,
                        "pnl_pct": pnl_pct, "result": "FLIP"
                    })
                    position = None

        # Open new position if no current position
        if position is None and sig in ("LONG", "SHORT"):
            entry_price = row["close"]
            if sig == "LONG":
                tp_price = entry_price * (1 + tp_pct / 100)
                sl_price = entry_price * (1 - sl_pct / 100)
            else:
                tp_price = entry_price * (1 - tp_pct / 100)
                sl_price = entry_price * (1 + sl_pct / 100)

            position = {
                "side": sig,
                "entry_price": entry_price,
                "entry_time": idx,
                "tp": tp_price,
                "sl": sl_price,
            }

    # Close any remaining position at market
    if position is not None:
        entry = position["entry_price"]
        exit_price = df.iloc[-1]["close"]
        if position["side"] == "LONG":
            pnl_pct = ((exit_price / entry) - 1) * 100
        else:
            pnl_pct = (1 - (exit_price / entry)) * 100
        trades.append({
            "side": position["side"], "entry": entry, "exit": exit_price,
            "entry_time": position["entry_time"], "exit_time": df.index[-1],
            "pnl_pct": pnl_pct, "result": "CLOSE"
        })

    # Calculate metrics
    if not trades:
        return {
            "total_trades": 0, "wins": 0, "losses": 0, "win_rate": 0,
            "total_pnl_pct": 0, "avg_pnl_pct": 0, "max_drawdown_pct": 0,
            "profit_factor": 0, "avg_win": 0, "avg_loss": 0,
            "tp_hits": 0, "sl_hits": 0, "flips": 0,
            "sortino": 0, "trades": []
        }

    pnls = [t["pnl_pct"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    tp_hits = sum(1 for t in trades if t["result"] == "TP")
    sl_hits = sum(1 for t in trades if t["result"] == "SL")
    flips = sum(1 for t in trades if t["result"] == "FLIP")

    gross_profit = sum(wins) if wins else 0
    gross_loss = abs(sum(losses)) if losses else 0.001
    profit_factor = gross_profit / gross_loss

    # Sortino ratio (annualized for the period)
    pnl_series = pd.Series(pnls)
    downside = pnl_series[pnl_series < 0]
    downside_std = downside.std() if len(downside) > 1 else 1.0
    sortino = (pnl_series.mean() / downside_std) if downside_std > 0 else 0

    # Max drawdown
    cumulative = np.cumsum(pnls)
    peak = np.maximum.accumulate(cumulative)
    drawdown = cumulative - peak
    max_dd = abs(min(drawdown)) if len(drawdown) > 0 else 0

    return {
        "total_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(trades) * 100,
        "total_pnl_pct": sum(pnls),
        "avg_pnl_pct": np.mean(pnls),
        "max_drawdown_pct": max_dd,
        "profit_factor": profit_factor,
        "avg_win": np.mean(wins) if wins else 0,
        "avg_loss": np.mean(losses) if losses else 0,
        "tp_hits": tp_hits,
        "sl_hits": sl_hits,
        "flips": flips,
        "sortino": sortino,
        "trades": trades,
    }


# ═══════════════════════════════════════════════════════════════
# STRATEGY PARAMETER GRID
# ═══════════════════════════════════════════════════════════════

STRATEGY_GRID = {
    "ema_cross": [
        {"fast": 5, "slow": 13},
        {"fast": 8, "slow": 21},
        {"fast": 9, "slow": 21},
        {"fast": 5, "slow": 20},
        {"fast": 12, "slow": 26},
        {"fast": 8, "slow": 34},
    ],
    "rsi_reversal": [
        {"period": 14, "oversold": 30, "overbought": 70},
        {"period": 14, "oversold": 25, "overbought": 75},
        {"period": 7, "oversold": 25, "overbought": 75},
        {"period": 7, "oversold": 30, "overbought": 70},
        {"period": 10, "oversold": 28, "overbought": 72},
        {"period": 21, "oversold": 35, "overbought": 65},
    ],
    "macd_cross": [
        {"fast": 12, "slow": 26, "signal": 9},
        {"fast": 8, "slow": 17, "signal": 9},
        {"fast": 5, "slow": 13, "signal": 8},
        {"fast": 6, "slow": 19, "signal": 9},
        {"fast": 10, "slow": 21, "signal": 7},
    ],
    "bollinger_bounce": [
        {"period": 20, "std": 2.0},
        {"period": 20, "std": 2.5},
        {"period": 14, "std": 2.0},
        {"period": 14, "std": 1.5},
        {"period": 10, "std": 2.0},
    ],
    "stoch_cross": [
        {"k_period": 14, "d_period": 3, "oversold": 25, "overbought": 75},
        {"k_period": 14, "d_period": 3, "oversold": 20, "overbought": 80},
        {"k_period": 9, "d_period": 3, "oversold": 25, "overbought": 75},
        {"k_period": 5, "d_period": 3, "oversold": 20, "overbought": 80},
    ],
    "pivot_breakout": [{}],
    "vwap_trend": [
        {"ema_period": 20},
        {"ema_period": 50},
        {"ema_period": 13},
    ],
    "supertrend_follow": [
        {"period": 10, "multiplier": 3.0},
        {"period": 10, "multiplier": 2.0},
        {"period": 7, "multiplier": 3.0},
        {"period": 14, "multiplier": 2.5},
        {"period": 7, "multiplier": 2.0},
    ],
    "cci_reversal": [
        {"period": 20},
        {"period": 14},
        {"period": 10},
    ],
    "williams_r_reversal": [
        {"period": 14},
        {"period": 10},
        {"period": 21},
    ],
    "macd_rsi_combo": [
        {"macd_fast": 12, "macd_slow": 26, "macd_signal": 9, "rsi_period": 14},
        {"macd_fast": 8, "macd_slow": 17, "macd_signal": 9, "rsi_period": 14},
        {"macd_fast": 5, "macd_slow": 13, "macd_signal": 8, "rsi_period": 7},
    ],
    "ema_rsi_combo": [
        {"fast": 8, "slow": 21, "rsi_period": 14},
        {"fast": 5, "slow": 13, "rsi_period": 7},
        {"fast": 9, "slow": 21, "rsi_period": 14},
        {"fast": 12, "slow": 26, "rsi_period": 14},
    ],
    "bollinger_rsi_combo": [
        {"bb_period": 20, "bb_std": 2.0, "rsi_period": 14},
        {"bb_period": 14, "bb_std": 2.0, "rsi_period": 7},
        {"bb_period": 20, "bb_std": 2.5, "rsi_period": 14},
    ],
    "supertrend_macd_combo": [
        {"st_period": 10, "st_mult": 3.0, "macd_fast": 12, "macd_slow": 26},
        {"st_period": 7, "st_mult": 2.0, "macd_fast": 8, "macd_slow": 17},
        {"st_period": 10, "st_mult": 2.0, "macd_fast": 12, "macd_slow": 26},
    ],
    "pivot_vwap_combo": [{}],
    "triple_ema": [
        {"fast": 5, "mid": 13, "slow": 34},
        {"fast": 8, "mid": 21, "slow": 55},
        {"fast": 5, "mid": 8, "slow": 21},
        {"fast": 9, "mid": 21, "slow": 50},
    ],
    "stoch_rsi_combo": [
        {"k_period": 14, "rsi_period": 14},
        {"k_period": 9, "rsi_period": 7},
        {"k_period": 5, "rsi_period": 14},
    ],
    "adx_trend": [
        {"period": 14, "ema_fast": 8, "ema_slow": 21, "threshold": 25},
        {"period": 14, "ema_fast": 5, "ema_slow": 13, "threshold": 20},
        {"period": 14, "ema_fast": 12, "ema_slow": 26, "threshold": 25},
    ],
}


# ═══════════════════════════════════════════════════════════════
# MAIN RUNNER
# ═══════════════════════════════════════════════════════════════

def run_full_backtest():
    """Run all strategy combinations across all timeframes."""
    from generate_sol_data import generate_all_timeframes

    tp_pct = 3.0
    sl_pct = 1.5

    all_results = []

    # Try live data first, fall back to generated data
    timeframes_data = {}
    try:
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=16)
        start_ms = int(start_date.timestamp() * 1000)
        end_ms = int(end_date.timestamp() * 1000)

        for tf_name, tf_interval in {"15m": "15m", "30m": "30m", "1h": "1h"}.items():
            df = fetch_binance_klines("SOLUSDT", tf_interval, start_ms, end_ms)
            if not df.empty:
                timeframes_data[tf_name] = filter_weekdays(df)

        if not timeframes_data:
            raise Exception("No live data available")
        print("Using LIVE Binance data")
    except Exception:
        print("Live data unavailable — using generated data from known SOL price anchors")
        timeframes_data = generate_all_timeframes()

    for tf_name, df_weekday in timeframes_data.items():
        print(f"\n{'='*70}")
        print(f"  BACKTESTING SOL/USDT {tf_name}")
        print(f"{'='*70}")
        print(f"  Weekday candles: {len(df_weekday)}")
        print(f"  Date range: {df_weekday.index[0]} to {df_weekday.index[-1]}")
        print(f"  Price range: ${df_weekday['low'].min():.2f} - ${df_weekday['high'].max():.2f}")

        for strat_name, param_list in STRATEGY_GRID.items():
            for params in param_list:
                try:
                    signals = generate_signals(df_weekday, strat_name, params)
                    result = backtest(df_weekday, signals, tp_pct, sl_pct)

                    result["strategy"] = strat_name
                    result["params"] = params
                    result["timeframe"] = tf_name
                    all_results.append(result)

                except Exception as e:
                    pass  # Skip failing combos silently

    # Rank results
    # Score: weighted combo of total_pnl, win_rate, profit_factor, sortino
    for r in all_results:
        r["score"] = (
            r["total_pnl_pct"] * 2.0 +
            r["win_rate"] * 0.5 +
            min(r["profit_factor"], 10) * 3.0 +
            min(r["sortino"], 5) * 4.0 -
            r["max_drawdown_pct"] * 1.5
        )

    all_results.sort(key=lambda x: x["score"], reverse=True)

    # Print top results
    print(f"\n{'='*70}")
    print(f"  TOP 20 STRATEGY COMBINATIONS (3% TP / 1.5% SL / No Leverage)")
    print(f"{'='*70}")
    print(f"{'Rank':<5} {'Strategy':<25} {'TF':<5} {'Trades':<7} {'Win%':<7} "
          f"{'PnL%':<8} {'PF':<6} {'Sortino':<8} {'MaxDD':<7} {'Score':<8} {'Params'}")
    print("-" * 130)

    for i, r in enumerate(all_results[:20]):
        if r["total_trades"] == 0:
            continue
        print(f"{i+1:<5} {r['strategy']:<25} {r['timeframe']:<5} {r['total_trades']:<7} "
              f"{r['win_rate']:<7.1f} {r['total_pnl_pct']:<8.2f} {r['profit_factor']:<6.2f} "
              f"{r['sortino']:<8.2f} {r['max_drawdown_pct']:<7.2f} {r['score']:<8.2f} {r['params']}")

    # Also print trade details of #1
    if all_results and all_results[0]["total_trades"] > 0:
        best = all_results[0]
        print(f"\n{'='*70}")
        print(f"  BEST STRATEGY DETAILS: {best['strategy']} ({best['timeframe']})")
        print(f"  Params: {best['params']}")
        print(f"{'='*70}")
        print(f"  Total PnL: {best['total_pnl_pct']:.2f}%")
        print(f"  Win Rate: {best['win_rate']:.1f}% ({best['wins']}W / {best['losses']}L)")
        print(f"  Profit Factor: {best['profit_factor']:.2f}")
        print(f"  Sortino: {best['sortino']:.2f}")
        print(f"  TP Hits: {best['tp_hits']} | SL Hits: {best['sl_hits']} | Flips: {best['flips']}")
        print(f"  Avg Win: {best['avg_win']:.2f}% | Avg Loss: {best['avg_loss']:.2f}%")
        print(f"\n  Trade Log:")
        for t in best["trades"]:
            print(f"    {t['side']:<5} entry=${t['entry']:.2f} exit=${t['exit']:.2f} "
                  f"pnl={t['pnl_pct']:+.2f}% [{t['result']}] "
                  f"{t['entry_time']} -> {t['exit_time']}")

    # Save results summary (exclude trade objects for JSON)
    summary = []
    for r in all_results[:30]:
        s = {k: v for k, v in r.items() if k != "trades"}
        summary.append(s)

    with open("/home/user/Tex-trades/backtest_results.json", "w") as f:
        json.dump(summary, f, indent=2, default=str)

    print(f"\nResults saved to backtest_results.json")
    return all_results


if __name__ == "__main__":
    results = run_full_backtest()
