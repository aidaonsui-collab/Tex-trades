"""
telegram.py — Telegram alert notifications for the Momentum Breakout bot.

All public send_* functions are fire-and-forget: they log on failure but
never raise, so a Telegram outage cannot crash the main bot loop.

Message types:
  send_startup()          — bot has started with strategy config
  send_signal()           — breakout signal detected with levels
  send_order_placed()     — order sent to exchange with SL/TP
  send_position_closed()  — position closed with PnL + running weekly stats
  send_error()            — unexpected error / connection issue
  send_health()           — periodic heartbeat with market snapshot
  send_weekly_summary()   — end-of-week performance summary
"""

import logging
import time
from typing import Optional

import requests

import config

logger = logging.getLogger(__name__)

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


# ─────────────────────────────────────────────
# Core send function
# ─────────────────────────────────────────────

def _send(text: str, parse_mode: str = "HTML") -> bool:
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        logger.debug("Telegram not configured — skipping alert")
        return False
    url = _TELEGRAM_API.format(token=config.TELEGRAM_BOT_TOKEN)
    payload = {"chat_id": config.TELEGRAM_CHAT_ID, "text": text, "parse_mode": parse_mode}
    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        return True
    except requests.RequestException as exc:
        logger.warning("Telegram send failed: %s", exc)
        return False


def _escape(text: str) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _mode_tag() -> str:
    return "🟡 <b>DRY RUN</b>" if config.DRY_RUN else "🔴 <b>LIVE</b>"


def _bar(value: float, max_val: float = 10, length: int = 10) -> str:
    """Simple text progress bar."""
    filled = min(int(abs(value) / max_val * length), length)
    return "█" * filled + "░" * (length - filled)


# ─────────────────────────────────────────────
# Startup
# ─────────────────────────────────────────────

def send_startup() -> None:
    mode = "DRY RUN (paper trading)" if config.DRY_RUN else "LIVE TRADING"
    text = (
        f"🚀 <b>MomBreak Bot Started</b>\n"
        f"{_mode_tag()}\n\n"
        f"<b>Strategy:</b> Momentum Breakout\n"
        f"Symbol    : <code>{_escape(config.SYMBOL)}</code>\n"
        f"Interval  : <code>{config.CANDLE_INTERVAL}</code>\n"
        f"Leverage  : <code>{config.LEVERAGE}x</code>\n"
        f"Size      : <code>${config.POSITION_SIZE_USD:.0f}</code>\n"
        f"Mode      : <code>{mode}</code>\n\n"
        f"<b>Breakout Config:</b>\n"
        f"Lookback  : <code>{config.BREAKOUT_LOOKBACK} bars</code>\n"
        f"ROC thresh: <code>{config.ROC_THRESHOLD}%</code>\n"
        f"Vol filter: <code>{config.VOLUME_MULTIPLIER}x avg</code>\n"
        f"Trend EMA : <code>{config.TREND_EMA_PERIOD}</code>\n\n"
        f"<b>Risk Management:</b>\n"
        f"Stop loss : <code>ATR × {config.ATR_MULTIPLIER}</code>\n"
        f"Take profit: <code>ATR × {config.ATR_MULTIPLIER} × {config.REWARD_RISK_RATIO}</code>\n"
        f"R:R ratio : <code>{config.REWARD_RISK_RATIO}:1</code>"
    )
    _send(text)
    logger.info("Telegram startup alert sent")


# ─────────────────────────────────────────────
# Signal detected
# ─────────────────────────────────────────────

def send_signal(
    signal: str,
    price: float,
    atr: float,
    roc: float,
    channel_high: float,
    channel_low: float,
    ema_trend: float,
    volume: float,
    volume_avg: float,
) -> None:
    emoji = "🟢" if signal == "LONG" else "🔴"
    direction = "▲ BREAKOUT UP" if signal == "LONG" else "▼ BREAKDOWN"
    trend = "BULLISH" if price > ema_trend else "BEARISH"

    # Compute SL/TP levels
    stop_dist = atr * config.ATR_MULTIPLIER
    tp_dist = stop_dist * config.REWARD_RISK_RATIO
    if signal == "LONG":
        sl = price - stop_dist
        tp = price + tp_dist
    else:
        sl = price + stop_dist
        tp = price - tp_dist

    vol_ratio = volume / volume_avg if volume_avg > 0 else 0

    text = (
        f"{emoji} <b>{direction}</b>  {_mode_tag()}\n\n"
        f"Symbol  : <code>{_escape(config.SYMBOL)}</code>\n"
        f"Price   : <code>${price:,.2f}</code>\n"
        f"Channel : <code>${channel_low:,.2f} — ${channel_high:,.2f}</code>\n\n"
        f"📊 <b>Indicators</b>\n"
        f"ROC({config.BREAKOUT_LOOKBACK}): <code>{roc:+.2f}%</code>\n"
        f"ATR(14) : <code>${atr:,.2f}</code>\n"
        f"EMA({config.TREND_EMA_PERIOD}) : <code>${ema_trend:,.2f}</code>  ({trend})\n"
        f"Volume  : <code>{vol_ratio:.1f}x avg</code>\n\n"
        f"🎯 <b>Levels</b>\n"
        f"Stop    : <code>${sl:,.2f}</code>  ({config.ATR_MULTIPLIER}x ATR)\n"
        f"Target  : <code>${tp:,.2f}</code>  ({config.REWARD_RISK_RATIO}:1 R:R)\n"
        f"Risk    : <code>${stop_dist:,.2f}</code>  →  Reward: <code>${tp_dist:,.2f}</code>"
    )
    _send(text)


# ─────────────────────────────────────────────
# Order placed
# ─────────────────────────────────────────────

def send_order_placed(
    signal: str,
    size: float,
    price: float,
    leverage: int,
    atr: float,
    dry_run: bool = False,
) -> None:
    emoji = "🟢" if signal == "LONG" else "🔴"
    prefix = "[DRY RUN] " if dry_run else ""

    stop_dist = atr * config.ATR_MULTIPLIER
    tp_dist = stop_dist * config.REWARD_RISK_RATIO
    if signal == "LONG":
        sl, tp = price - stop_dist, price + tp_dist
    else:
        sl, tp = price + stop_dist, price - tp_dist

    # Max loss / gain estimates
    max_loss = (stop_dist / price) * config.POSITION_SIZE_USD * leverage
    max_gain = (tp_dist / price) * config.POSITION_SIZE_USD * leverage

    text = (
        f"{emoji} <b>{prefix}Order: {signal}</b>\n\n"
        f"Symbol   : <code>{_escape(config.SYMBOL)}</code>\n"
        f"Size     : <code>{size:.4f}</code>\n"
        f"Entry    : <code>${price:,.2f}</code>\n"
        f"Leverage : <code>{leverage}x</code>\n"
        f"Notional : <code>${size * price:,.2f}</code>\n\n"
        f"🎯 <b>Risk Plan</b>\n"
        f"Stop loss  : <code>${sl:,.2f}</code>  (max loss: <code>${max_loss:,.2f}</code>)\n"
        f"Take profit: <code>${tp:,.2f}</code>  (max gain: <code>${max_gain:,.2f}</code>)\n"
        f"R:R        : <code>{config.REWARD_RISK_RATIO}:1</code>"
    )
    _send(text)


# ─────────────────────────────────────────────
# Position closed
# ─────────────────────────────────────────────

def send_position_closed(
    side: str,
    entry_price: float,
    exit_price: float,
    size: float,
    pnl_usd: float,
    exit_reason: str = "signal",
    weekly_pnl: float = 0.0,
    weekly_trades: int = 0,
    weekly_wins: int = 0,
    dry_run: bool = False,
) -> None:
    pnl_emoji = "✅" if pnl_usd >= 0 else "❌"
    prefix = "[DRY RUN] " if dry_run else ""
    direction = "▲" if side == "LONG" else "▼"

    # Exit reason label
    reason_labels = {
        "stop": "🛑 STOP LOSS",
        "tp": "🎯 TAKE PROFIT",
        "signal": "🔄 SIGNAL FLIP",
    }
    reason_str = reason_labels.get(exit_reason, exit_reason.upper())

    # Move percentage
    if side == "LONG":
        move_pct = (exit_price - entry_price) / entry_price * 100
    else:
        move_pct = (entry_price - exit_price) / entry_price * 100

    # Weekly stats
    wr = (weekly_wins / weekly_trades * 100) if weekly_trades > 0 else 0

    text = (
        f"{pnl_emoji} <b>{prefix}{reason_str}</b>\n\n"
        f"Side      : <code>{direction} {side}</code>\n"
        f"Symbol    : <code>{_escape(config.SYMBOL)}</code>\n"
        f"Entry     : <code>${entry_price:,.2f}</code>\n"
        f"Exit      : <code>${exit_price:,.2f}</code>  ({move_pct:+.2f}%)\n"
        f"Size      : <code>{size:.4f}</code>\n"
        f"PnL       : <code>${pnl_usd:+.2f}</code>\n\n"
        f"📈 <b>Weekly Stats</b>\n"
        f"Trades  : <code>{weekly_trades}</code>  ({weekly_wins}W / {weekly_trades - weekly_wins}L)\n"
        f"Win rate: <code>{wr:.0f}%</code>\n"
        f"Week PnL: <code>${weekly_pnl:+.2f}</code>"
    )
    _send(text)


# ─────────────────────────────────────────────
# Weekly summary (sent at end of each season)
# ─────────────────────────────────────────────

def send_weekly_summary(
    total_trades: int,
    wins: int,
    total_pnl: float,
    sortino: float,
    profit_factor: float,
    best_trade: float,
    worst_trade: float,
) -> None:
    wr = (wins / total_trades * 100) if total_trades > 0 else 0
    pnl_emoji = "🏆" if total_pnl > 0 else "📉"
    ret_pct = total_pnl / config.POSITION_SIZE_USD * 100  # approximate

    text = (
        f"{pnl_emoji} <b>Weekly Season Summary</b>\n"
        f"{'━' * 28}\n\n"
        f"Symbol    : <code>{_escape(config.SYMBOL)}</code>\n"
        f"Strategy  : <code>MomBreak({config.BREAKOUT_LOOKBACK})</code>\n\n"
        f"<b>Results:</b>\n"
        f"Trades    : <code>{total_trades}</code>  ({wins}W / {total_trades - wins}L)\n"
        f"Win rate  : <code>{wr:.0f}%</code>\n"
        f"Total PnL : <code>${total_pnl:+.2f}</code>\n"
        f"Best trade: <code>${best_trade:+.2f}</code>\n"
        f"Worst     : <code>${worst_trade:+.2f}</code>\n\n"
        f"<b>Competition Metrics:</b>\n"
        f"Sortino   : <code>{sortino:.2f}</code>\n"
        f"Profit Fac: <code>{profit_factor:.2f}</code>\n"
        f"Return    : <code>{ret_pct:+.1f}%</code>"
    )
    _send(text)


# ─────────────────────────────────────────────
# Error
# ─────────────────────────────────────────────

def send_error(context: str, exc: Optional[Exception] = None) -> None:
    exc_str = f"\n<code>{_escape(str(exc))}</code>" if exc else ""
    text = (
        f"⚠️ <b>Bot Error</b>  {_mode_tag()}\n\n"
        f"Context: <code>{_escape(context)}</code>"
        f"{exc_str}"
    )
    _send(text)


# ─────────────────────────────────────────────
# Health / heartbeat
# ─────────────────────────────────────────────

def send_health(
    loop_count: int,
    uptime_seconds: float,
    price: Optional[float] = None,
    atr: Optional[float] = None,
    roc: Optional[float] = None,
    ema_trend: Optional[float] = None,
    channel_high: Optional[float] = None,
    channel_low: Optional[float] = None,
    position_side: Optional[str] = None,
    position_entry: Optional[float] = None,
    weekly_pnl: float = 0.0,
    weekly_trades: int = 0,
) -> None:
    hours = uptime_seconds / 3600

    market_lines = ""
    if price is not None:
        trend = "BULL 📈" if ema_trend and price > ema_trend else "BEAR 📉"
        dist_high = ((channel_high - price) / price * 100) if channel_high else 0
        dist_low = ((price - channel_low) / price * 100) if channel_low else 0

        market_lines = (
            f"\n\n📊 <b>Market</b>\n"
            f"Price   : <code>${price:,.2f}</code>  ({trend})\n"
            f"Channel : <code>${channel_low:,.2f} — ${channel_high:,.2f}</code>\n"
            f"Dist    : <code>{dist_high:+.2f}% to high</code> / <code>{dist_low:+.2f}% to low</code>\n"
            f"ROC({config.BREAKOUT_LOOKBACK}): <code>{roc:+.2f}%</code>\n"
            f"ATR(14) : <code>${atr:,.2f}</code>"
        )

    pos_lines = ""
    if position_side and position_entry:
        if price:
            if position_side == "LONG":
                upnl = (price - position_entry) / position_entry * config.LEVERAGE * config.POSITION_SIZE_USD
            else:
                upnl = (position_entry - price) / position_entry * config.LEVERAGE * config.POSITION_SIZE_USD
            pos_lines = (
                f"\n\n📌 <b>Open Position</b>\n"
                f"Side    : <code>{position_side}</code>\n"
                f"Entry   : <code>${position_entry:,.2f}</code>\n"
                f"uPnL    : <code>${upnl:+.2f}</code>"
            )
        else:
            pos_lines = (
                f"\n\n📌 <b>Open Position</b>\n"
                f"Side    : <code>{position_side}</code>\n"
                f"Entry   : <code>${position_entry:,.2f}</code>"
            )

    weekly_lines = ""
    if weekly_trades > 0:
        weekly_lines = f"\n\n📈 Week PnL: <code>${weekly_pnl:+.2f}</code> ({weekly_trades} trades)"

    text = (
        f"💓 <b>Heartbeat</b>  {_mode_tag()}\n\n"
        f"Symbol : <code>{_escape(config.SYMBOL)}</code>\n"
        f"Loops  : <code>{loop_count}</code>\n"
        f"Uptime : <code>{hours:.1f}h</code>"
        f"{market_lines}"
        f"{pos_lines}"
        f"{weekly_lines}"
    )
    _send(text)
