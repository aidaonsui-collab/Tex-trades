#!/usr/bin/env python3
"""
btc_backtest.py — VWAP Cross + RSI Strategy Backtest
Matches the EXACT parameters running in the live Tex-trades bot.

Also diagnoses signal frequency — shows why signals might not be firing.

Usage:
    pip install pandas numpy requests
    python btc_backtest.py
    python btc_backtest.py --symbol ETH --days 60
    python btc_backtest.py --diagnose   # just show signal frequency analysis
"""

import argparse
import json
import time
import warnings
from datetime import datetime, timezone

warnings.filterwarnings('ignore')

import numpy as np
import pandas as pd
import requests

# ── Live bot parameters (must match config.py exactly) ───────────────────────
SYMBOL            = "BTC"
INTERVAL          = "15m"          # matches CANDLE_INTERVAL in config.py
RSI_PERIOD        = 14             # matches RSI_PERIOD
RSI_LOWER         = 35.0           # matches RSI_LOWER
RSI_UPPER         = 65.0           # matches RSI_UPPER
LEVERAGE          = 10             # matches LEVERAGE
POSITION_SIZE_USD = 50.0           # matches POSITION_SIZE_USD

# Backtest settings
STARTING_CAPITAL  = 1000.0
FEE_RATE          = 0.00035        # Hyperliquid taker fee ~0.035%
DAYS_BACK         = 30


# ── Data fetching ─────────────────────────────────────────────────────────────

def fetch_candles(symbol: str, interval: str, days: int) -> pd.DataFrame:
    """Fetch OHLCV from Binance.US public API."""
    interval_map = {"15m": 15, "1h": 60, "4h": 240, "1d": 1440}
    mins = interval_map.get(interval, 15)
    limit = min(1000, (days * 24 * 60) // mins)

    url = "https://api.binance.us/api/v3/klines"
    params = {"symbol": f"{symbol}USDT", "interval": interval, "limit": limit}

    print(f"Fetching {symbol}/USDT {interval} candles ({limit} bars, ~{days} days)...")
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    raw = resp.json()

    df = pd.DataFrame(raw, columns=[
        "open_time","open","high","low","close","volume",
        "close_time","quote_vol","trades","taker_buy_base","taker_buy_quote","ignore"
    ])
    df["ts"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    for col in ["open","high","low","close","volume"]:
        df[col] = df[col].astype(float)

    df = df[["ts","open","high","low","close","volume"]].sort_values("ts").reset_index(drop=True)
    print(f"  → {len(df)} candles from {df['ts'].min()} to {df['ts'].max()}")
    return df


# ── Indicators (exact same logic as strategy.py) ─────────────────────────────

def compute_vwap(df: pd.DataFrame) -> pd.Series:
    """Cumulative VWAP — same as strategy.py compute_vwap."""
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    cumtpv  = (typical * df["volume"]).cumsum()
    cumvol  = df["volume"].cumsum()
    return cumtpv / cumvol


def compute_rsi(closes: pd.Series, period: int = 14) -> pd.Series:
    """Wilder RSI — same algorithm as strategy.py compute_rsi."""
    delta = closes.diff()
    gain  = delta.clip(lower=0).ewm(alpha=1/period, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(alpha=1/period, adjust=False).mean()
    rs    = gain / loss.replace(0, np.nan)
    rsi   = 100 - (100 / (1 + rs))
    # First `period` values are NaN (matches strategy.py behaviour)
    rsi.iloc[:period] = np.nan
    return rsi


def add_signals(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["vwap"] = compute_vwap(df)
    df["rsi"]  = compute_rsi(df["close"], RSI_PERIOD)

    # VWAP cross detection (same as strategy.py)
    df["prev_close"] = df["close"].shift(1)
    df["prev_vwap"]  = df["vwap"].shift(1)

    crossed_above = (df["prev_close"] < df["prev_vwap"]) & (df["close"] > df["vwap"])
    crossed_below = (df["prev_close"] > df["prev_vwap"]) & (df["close"] < df["vwap"])
    rsi_in_zone   = df["rsi"].between(RSI_LOWER, RSI_UPPER)

    df["signal_long"]  = crossed_above & rsi_in_zone
    df["signal_short"] = crossed_below & rsi_in_zone
    df["cross_above"]  = crossed_above   # VWAP cross regardless of RSI
    df["cross_below"]  = crossed_below
    return df


# ── Backtest engine ───────────────────────────────────────────────────────────

def run_backtest(df: pd.DataFrame) -> dict:
    """
    Simple backtest:
    - Enter on signal candle close price
    - Exit when opposite signal fires (same as live bot)
    - Track PnL with fees
    """
    capital      = STARTING_CAPITAL
    position     = None   # None | {"side": "LONG"|"SHORT", "entry": float, "size": float, "ts": ts}
    trades       = []
    equity_curve = [capital]

    for i in range(1, len(df)):
        row = df.iloc[i]
        price = row["close"]

        # Check exit first
        if position:
            exit_signal = (
                (position["side"] == "LONG"  and row["signal_short"]) or
                (position["side"] == "SHORT" and row["signal_long"])
            )
            if exit_signal:
                entry = position["entry"]
                size  = position["size"]
                if position["side"] == "LONG":
                    pnl_pct = (price - entry) / entry
                else:
                    pnl_pct = (entry - price) / entry

                gross_pnl = pnl_pct * LEVERAGE * position["usd_size"]
                fee       = (entry + price) * size * FEE_RATE
                net_pnl   = gross_pnl - fee
                capital  += net_pnl

                trades.append({
                    "entry_ts":   position["ts"],
                    "exit_ts":    row["ts"],
                    "side":       position["side"],
                    "entry":      entry,
                    "exit":       price,
                    "pnl_pct":    pnl_pct * 100,
                    "net_pnl":    net_pnl,
                    "capital":    capital,
                    "rsi_entry":  position["rsi"],
                    "hold_candles": i - position["entry_idx"],
                })
                position = None

        # Check entry
        if not position:
            if row["signal_long"]:
                side = "LONG"
            elif row["signal_short"]:
                side = "SHORT"
            else:
                side = None

            if side:
                size = (POSITION_SIZE_USD * LEVERAGE) / price
                position = {
                    "side":      side,
                    "entry":     price,
                    "size":      size,
                    "usd_size":  POSITION_SIZE_USD,
                    "ts":        row["ts"],
                    "entry_idx": i,
                    "rsi":       row["rsi"],
                }

        equity_curve.append(capital)

    return {"trades": trades, "equity_curve": equity_curve, "final_capital": capital}


# ── Signal frequency analysis ─────────────────────────────────────────────────

def analyze_signal_frequency(df: pd.DataFrame):
    """Diagnose why signals might not be firing."""
    total    = len(df)
    longs    = df["signal_long"].sum()
    shorts   = df["signal_short"].sum()
    crosses  = (df["cross_above"] | df["cross_below"]).sum()
    rsi_zone = df["rsi"].between(RSI_LOWER, RSI_UPPER).sum()

    print("\n" + "="*60)
    print("  SIGNAL FREQUENCY ANALYSIS")
    print("="*60)
    print(f"  Timeframe:      {INTERVAL}")
    print(f"  Total candles:  {total}")
    print(f"  Date range:     {df['ts'].min().strftime('%Y-%m-%d')} → {df['ts'].max().strftime('%Y-%m-%d')}")
    print()
    print(f"  VWAP crosses (any):     {crosses:4d}  ({crosses/total*100:.1f}% of candles)")
    print(f"  RSI in [{RSI_LOWER}-{RSI_UPPER}]:         {rsi_zone:4d}  ({rsi_zone/total*100:.1f}% of candles)")
    print()
    print(f"  LONG  signals fired:    {longs:4d}  ({longs/total*100:.2f}% = 1 per {total//max(longs,1)} candles)")
    print(f"  SHORT signals fired:    {shorts:4d}  ({shorts/total*100:.2f}% = 1 per {total//max(shorts,1)} candles)")
    total_signals = longs + shorts
    print(f"  Total signals:          {total_signals:4d}")

    mins_per_candle = {"15m": 15, "1h": 60, "4h": 240}.get(INTERVAL, 15)
    if total_signals > 0:
        avg_hours = (total / total_signals * mins_per_candle) / 60
        print(f"\n  ⏱  Average time between signals: {avg_hours:.1f} hours")
        if avg_hours > 24:
            print(f"  ⚠️  Signal is RARE — expected gap > 24 hours is normal")
        else:
            print(f"  ✅ Signal fires regularly")
    else:
        print(f"\n  ❌ NO SIGNALS fired in this period — strategy is too restrictive")

    # Why are crosses filtered out?
    filtered = df[df["cross_above"] | df["cross_below"]].copy()
    if len(filtered) > 0:
        rsi_vals = filtered["rsi"].dropna()
        below_zone = (rsi_vals < RSI_LOWER).sum()
        above_zone = (rsi_vals > RSI_UPPER).sum()
        in_zone    = rsi_vals.between(RSI_LOWER, RSI_UPPER).sum()
        print(f"\n  Of {len(filtered)} VWAP crosses:")
        print(f"    RSI < {RSI_LOWER} (oversold, filtered): {below_zone} ({below_zone/len(filtered)*100:.0f}%)")
        print(f"    RSI > {RSI_UPPER} (overbought, filtered): {above_zone} ({above_zone/len(filtered)*100:.0f}%)")
        print(f"    RSI in zone (fired):  {in_zone} ({in_zone/len(filtered)*100:.0f}%)")

    # Recent signal times
    recent_longs  = df[df["signal_long"]].tail(5)["ts"].tolist()
    recent_shorts = df[df["signal_short"]].tail(5)["ts"].tolist()
    recent_all    = sorted(
        [(t, "LONG") for t in recent_longs] + [(t, "SHORT") for t in recent_shorts],
        key=lambda x: x[0], reverse=True
    )[:5]

    if recent_all:
        print(f"\n  Last 5 signals:")
        now = pd.Timestamp.now(tz="UTC")
        for ts, side in recent_all:
            hours_ago = (now - ts).total_seconds() / 3600
            print(f"    {side:<6} @ {ts.strftime('%Y-%m-%d %H:%M UTC')}  ({hours_ago:.1f}h ago)")
    print()


# ── Results display ───────────────────────────────────────────────────────────

def print_results(result: dict, df: pd.DataFrame):
    trades = result["trades"]
    final  = result["final_capital"]

    print("\n" + "="*60)
    print("  BACKTEST RESULTS — VWAP Cross + RSI Strategy")
    print(f"  {SYMBOL}/USDT  {INTERVAL}  RSI[{RSI_LOWER}-{RSI_UPPER}]  {LEVERAGE}x  ${POSITION_SIZE_USD}/trade")
    print("="*60)

    if not trades:
        print("  ❌ No trades executed in this period.")
        return

    wins   = [t for t in trades if t["net_pnl"] > 0]
    losses = [t for t in trades if t["net_pnl"] <= 0]
    longs  = [t for t in trades if t["side"] == "LONG"]
    shorts = [t for t in trades if t["side"] == "SHORT"]

    win_rate   = len(wins) / len(trades) * 100
    total_pnl  = sum(t["net_pnl"] for t in trades)
    total_pct  = (final - STARTING_CAPITAL) / STARTING_CAPITAL * 100
    avg_win    = np.mean([t["net_pnl"] for t in wins])  if wins   else 0
    avg_loss   = np.mean([t["net_pnl"] for t in losses]) if losses else 0
    avg_hold   = np.mean([t["hold_candles"] for t in trades])

    mins_per_candle = {"15m": 15, "1h": 60, "4h": 240}.get(INTERVAL, 15)
    avg_hold_hours  = avg_hold * mins_per_candle / 60

    print(f"  Period:       {df['ts'].min().strftime('%Y-%m-%d')} → {df['ts'].max().strftime('%Y-%m-%d')}")
    print(f"  Trades:       {len(trades)}  ({len(longs)} long / {len(shorts)} short)")
    print(f"  Win rate:     {win_rate:.1f}%  ({len(wins)}W / {len(losses)}L)")
    print(f"  Avg hold:     {avg_hold_hours:.1f}h ({avg_hold:.0f} candles)")
    print()
    print(f"  Avg win:      ${avg_win:+.2f}")
    print(f"  Avg loss:     ${avg_loss:+.2f}")
    if avg_loss != 0:
        print(f"  Profit factor:{abs(avg_win / avg_loss):.2f}")
    print()
    print(f"  Total PnL:    ${total_pnl:+.2f}")
    print(f"  Return:       {total_pct:+.1f}%  (${STARTING_CAPITAL:.0f} → ${final:.0f})")
    print()

    # Per-trade breakdown
    print("  Recent trades:")
    print(f"  {'Date':<20} {'Side':<6} {'Entry':>9} {'Exit':>9} {'PnL':>8} {'RSI':>6} {'Hold':>6}")
    print(f"  {'-'*20} {'-'*6} {'-'*9} {'-'*9} {'-'*8} {'-'*6} {'-'*6}")
    for t in trades[-10:]:
        hold_h = t['hold_candles'] * mins_per_candle / 60
        pnl_str = f"${t['net_pnl']:+.2f}"
        print(f"  {str(t['entry_ts'])[:16]:<20} {t['side']:<6} "
              f"{t['entry']:>9.2f} {t['exit']:>9.2f} "
              f"{pnl_str:>8} {t['rsi_entry']:>6.1f} {hold_h:>5.1f}h")

    # Parameter sensitivity — what if we widen the RSI zone?
    print("\n  RSI ZONE SENSITIVITY:")
    print(f"  {'Zone':<12} {'Signals':<10} {'Trades':<8} {'Win%':<8} {'Return'}")
    for lo, hi in [(30,70), (35,65), (40,60), (45,55)]:
        zone_label = f"[{lo}-{hi}]"
        df2 = df.copy()
        rz  = df2["rsi"].between(lo, hi)
        ca  = df2["cross_above"]
        cb  = df2["cross_below"]
        df2["signal_long"]  = ca & rz
        df2["signal_short"] = cb & rz
        r2  = run_backtest(df2)
        t2  = r2["trades"]
        if t2:
            w2  = len([t for t in t2 if t["net_pnl"] > 0])
            wr2 = w2 / len(t2) * 100
            ret2 = (r2["final_capital"] - STARTING_CAPITAL) / STARTING_CAPITAL * 100
            marker = " ← current" if lo == RSI_LOWER and hi == RSI_UPPER else ""
            print(f"  {zone_label:<12} {df2['signal_long'].sum()+df2['signal_short'].sum():<10} {len(t2):<8} {wr2:<8.1f} {ret2:+.1f}%{marker}")
        else:
            print(f"  {zone_label:<12} {'0':<10} {'0':<8} {'—':<8} no trades")
    print()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Tex-trades VWAP Cross backtest")
    parser.add_argument("--symbol",   default="BTC",  help="Coin (default: BTC)")
    parser.add_argument("--interval", default="15m",  help="Candle interval (default: 15m)")
    parser.add_argument("--days",     type=int, default=30, help="Days to backtest (default: 30)")
    parser.add_argument("--diagnose", action="store_true",  help="Signal frequency analysis only")
    parser.add_argument("--save",     action="store_true",  help="Save results to JSON")
    args = parser.parse_args()

    global SYMBOL, INTERVAL, DAYS_BACK
    SYMBOL    = args.symbol.upper()
    INTERVAL  = args.interval
    DAYS_BACK = args.days

    df = fetch_candles(SYMBOL, INTERVAL, DAYS_BACK)
    df = add_signals(df)

    analyze_signal_frequency(df)

    if not args.diagnose:
        result = run_backtest(df)
        print_results(result, df)

        if args.save:
            out = {
                "symbol": SYMBOL, "interval": INTERVAL, "days": DAYS_BACK,
                "rsi_lower": RSI_LOWER, "rsi_upper": RSI_UPPER,
                "leverage": LEVERAGE, "position_size_usd": POSITION_SIZE_USD,
                "results": {
                    "total_trades":  len(result["trades"]),
                    "final_capital": result["final_capital"],
                    "return_pct":    (result["final_capital"] - STARTING_CAPITAL) / STARTING_CAPITAL * 100,
                },
                "trades": result["trades"],
            }
            fname = f"backtest_{SYMBOL}_{INTERVAL}_{DAYS_BACK}d.json"
            with open(fname, "w") as f:
                json.dump(out, f, indent=2, default=str)
            print(f"  Results saved to {fname}")


if __name__ == "__main__":
    main()
