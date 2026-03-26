"""
telegram.py — Telegram alert notifications for the trading bot.

All public send_* functions are fire-and-forget: they log on failure but
never raise, so a Telegram outage cannot crash the main bot loop.

Message types:
  send_startup()          — bot has started
  send_signal()           — VWAP cross signal detected
  send_order_placed()     — order sent to exchange
  send_position_closed()  — position closed, includes PnL
  send_error()            — unexpected error / connection issue
  send_health()           — periodic heartbeat (every ~1 hour)
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
    """
    Send a message to the configured Telegram chat.
    Returns True on success, False on failure (never raises).
    """
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        logger.debug("Telegram not configured — skipping alert: %s", text[:80])
        return False

    url = _TELEGRAM_API.format(token=config.TELEGRAM_BOT_TOKEN)
    payload = {
        "chat_id": config.TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": parse_mode,
    }

    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        return True
    except requests.RequestException as exc:
        logger.warning("Telegram send failed: %s", exc)
        return False


def _escape(text: str) -> str:
    """Escape special HTML characters for Telegram HTML parse mode."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _mode_tag() -> str:
    """Return a small tag indicating live vs dry-run mode."""
    return "🟡 <b>DRY RUN</b>" if config.DRY_RUN else "🔴 <b>LIVE</b>"


# ─────────────────────────────────────────────
# Specific alert senders
# ─────────────────────────────────────────────

def send_startup() -> None:
    """Notify that the bot has started successfully."""
    mode = "DRY RUN (paper trading)" if config.DRY_RUN else "LIVE TRADING"
    text = (
        f"🤖 <b>VWAP Cross Bot Started</b>\n"
        f"{_mode_tag()}\n\n"
        f"Symbol  : <code>{_escape(config.SYMBOL)}</code>\n"
        f"Leverage: <code>{config.LEVERAGE}x</code>\n"
        f"Size    : <code>${config.POSITION_SIZE_USD:.0f}</code>\n"
        f"Mode    : <code>{mode}</code>\n"
        f"RSI zone: <code>{config.RSI_LOWER:.0f} – {config.RSI_UPPER:.0f}</code>"
    )
    _send(text)
    logger.info("Telegram startup alert sent")


def send_signal(
    signal: str,
    price: float,
    vwap: float,
    rsi: float,
) -> None:
    """
    Alert when a VWAP cross signal is detected.
    Signal direction, entry price, VWAP level, and RSI are included.
    """
    emoji = "🟢" if signal == "LONG" else "🔴"
    text = (
        f"{emoji} <b>Signal: {signal}</b>  {_mode_tag()}\n\n"
        f"Symbol : <code>{_escape(config.SYMBOL)}</code>\n"
        f"Price  : <code>${price:,.2f}</code>\n"
        f"VWAP   : <code>${vwap:,.2f}</code>\n"
        f"RSI(14): <code>{rsi:.1f}</code>"
    )
    _send(text)


def send_order_placed(
    signal: str,
    size: float,
    price: float,
    leverage: int,
    dry_run: bool = False,
) -> None:
    """
    Alert when an order is placed (or simulated in DRY_RUN).
    """
    emoji = "🟢" if signal == "LONG" else "🔴"
    prefix = "[DRY RUN] " if dry_run else ""
    text = (
        f"{emoji} <b>{prefix}Order Placed: {signal}</b>\n\n"
        f"Symbol  : <code>{_escape(config.SYMBOL)}</code>\n"
        f"Size    : <code>{size:.4f} BTC</code>\n"
        f"Price   : <code>${price:,.2f}</code>\n"
        f"Leverage: <code>{leverage}x</code>\n"
        f"Notional: <code>${size * price:,.2f}</code>"
    )
    _send(text)


def send_position_closed(
    side: str,
    entry_price: float,
    exit_price: float,
    size: float,
    pnl_usd: float,
    dry_run: bool = False,
) -> None:
    """
    Alert when a position is closed. Includes realised PnL estimate.
    Note: PnL is an estimate based on entry/exit prices; actual PnL
    may differ due to fees, funding, and slippage.
    """
    pnl_emoji = "✅" if pnl_usd >= 0 else "❌"
    prefix = "[DRY RUN] " if dry_run else ""
    direction = "▲" if side == "LONG" else "▼"

    text = (
        f"{pnl_emoji} <b>{prefix}Position Closed: {direction} {side}</b>\n\n"
        f"Symbol    : <code>{_escape(config.SYMBOL)}</code>\n"
        f"Entry     : <code>${entry_price:,.2f}</code>\n"
        f"Exit      : <code>${exit_price:,.2f}</code>\n"
        f"Size      : <code>{size:.4f} BTC</code>\n"
        f"Est. PnL  : <code>${pnl_usd:+.2f}</code>"
    )
    _send(text)


def send_error(context: str, exc: Optional[Exception] = None) -> None:
    """
    Alert on unexpected errors. Used for API failures, data issues, etc.
    """
    exc_str = f"\n<code>{_escape(str(exc))}</code>" if exc else ""
    text = (
        f"⚠️ <b>Bot Error</b>  {_mode_tag()}\n\n"
        f"Context: <code>{_escape(context)}</code>"
        f"{exc_str}"
    )
    _send(text)


def send_health(loop_count: int, uptime_seconds: float) -> None:
    """
    Periodic heartbeat message so you know the bot is still running.
    Sent roughly every hour (every HEALTH_LOG_INTERVAL loops).
    """
    hours = uptime_seconds / 3600
    text = (
        f"💓 <b>Bot Heartbeat</b>  {_mode_tag()}\n\n"
        f"Symbol : <code>{_escape(config.SYMBOL)}</code>\n"
        f"Loops  : <code>{loop_count}</code>\n"
        f"Uptime : <code>{hours:.1f}h</code>"
    )
    _send(text)
