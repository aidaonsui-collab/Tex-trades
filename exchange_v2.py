"""
exchange_v2.py — Direct Hyperliquid API trading (no ACP routing).

Uses the hyperliquid-python-sdk for:
  - Market open/close orders
  - Leverage management
  - Native TP/SL via trigger orders
  - Position queries (no Redis needed)
  - Account balance queries

Requires:
  HYPERLIQUID_PRIVATE_KEY — API wallet private key
  HYPERLIQUID_ACCOUNT_ADDRESS — Main account address (vault/subaccount owner)
"""

import json
import logging
import math
import os
import time
from typing import Optional

import eth_account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils import constants

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Initialization
# ─────────────────────────────────────────────

_PRIVATE_KEY = os.environ.get("HYPERLIQUID_PRIVATE_KEY", "")
_ACCOUNT_ADDRESS = os.environ.get("HYPERLIQUID_ACCOUNT_ADDRESS", "")
_API_URL = constants.MAINNET_API_URL

_exchange: Optional[Exchange] = None
_info: Optional[Info] = None


def _get_exchange() -> Exchange:
    global _exchange
    if _exchange is None:
        if not _PRIVATE_KEY:
            raise RuntimeError("HYPERLIQUID_PRIVATE_KEY not set")
        wallet = eth_account.Account.from_key(_PRIVATE_KEY)
        _exchange = Exchange(
            wallet,
            _API_URL,
            account_address=_ACCOUNT_ADDRESS or None,
        )
        logger.info("Hyperliquid Exchange initialized (account=%s)", _ACCOUNT_ADDRESS or wallet.address)
    return _exchange


def _get_info() -> Info:
    global _info
    if _info is None:
        _info = Info(_API_URL, skip_ws=True)
        logger.info("Hyperliquid Info initialized")
    return _info


# ─────────────────────────────────────────────
# Market data
# ─────────────────────────────────────────────

def get_current_price(symbol: str) -> float:
    """Get current mid price for a symbol."""
    info = _get_info()
    mids = info.all_mids()
    price = float(mids.get(symbol, 0))
    return price


def get_candles(symbol: str, interval: str, count: int) -> list[dict]:
    """Fetch OHLCV candles from Hyperliquid."""
    import requests
    interval_ms = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240}
    mins = interval_ms.get(interval, 60)
    now = int(time.time() * 1000)
    start = now - (count * mins * 60 * 1000)

    resp = requests.post(f"{_API_URL}/info", json={
        "type": "candleSnapshot",
        "req": {"coin": symbol, "interval": interval, "startTime": start, "endTime": now}
    }, timeout=15)
    resp.raise_for_status()
    raw = resp.json()

    candles = []
    for c in raw:
        candles.append({
            "timestamp": int(c["t"]),
            "open": float(c["o"]),
            "high": float(c["h"]),
            "low": float(c["l"]),
            "close": float(c["c"]),
            "volume": float(c["v"]),
        })
    return candles


# ─────────────────────────────────────────────
# Account & Position queries
# ─────────────────────────────────────────────

def get_account_state() -> dict:
    """Get full account state including margin, equity, positions."""
    info = _get_info()
    addr = _ACCOUNT_ADDRESS or eth_account.Account.from_key(_PRIVATE_KEY).address
    state = info.user_state(addr)
    return state


def get_open_position(symbol: str) -> Optional[dict]:
    """Query Hyperliquid for current open position on a symbol.
    Returns dict with side, size, entry_price, unrealized_pnl or None if flat.
    """
    state = get_account_state()
    for pos in state.get("assetPositions", []):
        p = pos.get("position", {})
        if p.get("coin") == symbol:
            size = float(p.get("szi", 0))
            if abs(size) < 1e-8:
                return None
            return {
                "side": "LONG" if size > 0 else "SHORT",
                "size": abs(size),
                "entry_price": float(p.get("entryPx", 0)),
                "unrealized_pnl": float(p.get("unrealizedPnl", 0)),
                "leverage": int(float(p.get("leverage", {}).get("value", 1))),
                "margin_used": float(p.get("marginUsed", 0)),
            }
    return None


def get_balance() -> float:
    """Get available balance in USDC."""
    state = get_account_state()
    margin = state.get("marginSummary", {})
    return float(margin.get("accountValue", 0))


# ─────────────────────────────────────────────
# Trading
# ─────────────────────────────────────────────

def set_leverage(symbol: str, leverage: int) -> None:
    """Set leverage for a symbol (cross margin)."""
    exchange = _get_exchange()
    result = exchange.update_leverage(leverage, symbol, is_cross=True)
    logger.info("Leverage set: %s %dx → %s", symbol, leverage, result)


def market_open(symbol: str, side: str, size_usd: float, leverage: int,
                tp_price: Optional[float] = None, sl_price: Optional[float] = None) -> dict:
    """
    Open a position with market order.
    
    Args:
        symbol: e.g. "SOL"
        side: "LONG" or "SHORT"
        size_usd: position size in USD (NOT notional — this is the margin)
        leverage: e.g. 10
        tp_price: take profit price (optional)
        sl_price: stop loss price (optional)
    
    Returns dict with order details.
    """
    exchange = _get_exchange()
    
    # Set leverage first
    set_leverage(symbol, leverage)
    
    # Calculate size in coins: margin × leverage / price = notional / price
    price = get_current_price(symbol)
    if price <= 0:
        raise RuntimeError(f"Could not get price for {symbol}")
    
    notional = size_usd * leverage
    sz = notional / price
    # Round to appropriate precision
    sz = math.floor(sz * 1000) / 1000  # 3 decimal places for SOL
    
    is_buy = side.upper() == "LONG"
    
    logger.info("Market open: %s %s %.3f %s (margin=$%.0f, notional=$%.0f, price=$%.2f)",
                side, symbol, sz, "BUY" if is_buy else "SELL", size_usd, notional, price)
    
    result = exchange.market_open(symbol, is_buy, sz, slippage=0.01)
    logger.info("Order result: %s", result)
    
    # Set TP/SL if provided
    if tp_price or sl_price:
        time.sleep(1)  # Brief delay to let position settle
        _set_tp_sl_orders(symbol, is_buy, sz, tp_price, sl_price)
    
    return {
        "status": "filled",
        "side": side,
        "size": sz,
        "price": price,
        "notional": notional,
        "result": result,
    }


def market_close(symbol: str, size: Optional[float] = None) -> dict:
    """Close a position with market order."""
    exchange = _get_exchange()
    
    logger.info("Market close: %s (size=%s)", symbol, size or "all")
    result = exchange.market_close(symbol, sz=size, slippage=0.01)
    logger.info("Close result: %s", result)
    
    return {"status": "closed", "result": result}


def _set_tp_sl_orders(symbol: str, is_buy: bool, size: float,
                       tp_price: Optional[float], sl_price: Optional[float]) -> None:
    """Set TP and SL as trigger orders on Hyperliquid."""
    exchange = _get_exchange()
    
    orders = []
    
    if tp_price:
        # TP: reduce-only order that triggers when price reaches target
        # For long: sell when price >= tp (trigger above)
        # For short: buy when price <= tp (trigger below)
        tp_trigger = {"triggerPx": str(tp_price), "isMarket": True, "tpsl": "tp"}
        orders.append({
            "coin": symbol,
            "is_buy": not is_buy,  # opposite side to close
            "sz": size,
            "limit_px": tp_price,
            "order_type": {"trigger": tp_trigger},
            "reduce_only": True,
        })
        logger.info("TP order: %s @ $%.2f", "sell" if is_buy else "buy", tp_price)
    
    if sl_price:
        sl_trigger = {"triggerPx": str(sl_price), "isMarket": True, "tpsl": "sl"}
        orders.append({
            "coin": symbol,
            "is_buy": not is_buy,
            "sz": size,
            "limit_px": sl_price,
            "order_type": {"trigger": sl_trigger},
            "reduce_only": True,
        })
        logger.info("SL order: %s @ $%.2f", "sell" if is_buy else "buy", sl_price)
    
    if orders:
        for o in orders:
            try:
                result = exchange.order(
                    o["coin"], o["is_buy"], o["sz"], o["limit_px"],
                    o["order_type"], reduce_only=True,
                )
                logger.info("Trigger order placed: %s", result)
            except Exception as exc:
                logger.warning("Trigger order failed (will use software TP/SL): %s", exc)


def cancel_all_orders(symbol: str) -> None:
    """Cancel all open orders for a symbol."""
    exchange = _get_exchange()
    info = _get_info()
    addr = _ACCOUNT_ADDRESS or eth_account.Account.from_key(_PRIVATE_KEY).address
    
    open_orders = info.open_orders(addr)
    for order in open_orders:
        if order.get("coin") == symbol:
            try:
                exchange.cancel(symbol, order["oid"])
                logger.info("Cancelled order %s", order["oid"])
            except Exception as exc:
                logger.warning("Cancel failed for %s: %s", order["oid"], exc)
