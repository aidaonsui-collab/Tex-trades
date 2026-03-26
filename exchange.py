"""
exchange.py — Hyperliquid REST API integration.

Responsibilities:
  - Fetch live OHLCV candle data via the public /info endpoint
  - Place market orders (long/short) via the hyperliquid-python-sdk
  - Close open positions
  - Query current position state from the exchange
  - Exponential backoff on transient failures

All order-side logic respects DRY_RUN mode: in dry-run, order calls are
logged but never sent to the exchange.
"""

import json
import logging
import math
import time
from typing import Optional

import requests

import config
from strategy import Candle

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# Retry / backoff helper
# ─────────────────────────────────────────────

def _with_backoff(fn, *args, label: str = "", **kwargs):
    """
    Call `fn(*args, **kwargs)` with exponential backoff on exception.
    Raises the last exception if all retries are exhausted.
    """
    delay = config.RETRY_BASE_DELAY
    for attempt in range(1, config.RETRY_MAX_ATTEMPTS + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            if attempt == config.RETRY_MAX_ATTEMPTS:
                logger.error(
                    "%s failed after %d attempts: %s", label or fn.__name__, attempt, exc
                )
                raise
            logger.warning(
                "%s attempt %d/%d failed: %s — retrying in %.1fs",
                label or fn.__name__, attempt, config.RETRY_MAX_ATTEMPTS, exc, delay,
            )
            time.sleep(delay)
            delay = min(delay * 2, config.RETRY_MAX_DELAY)


# ─────────────────────────────────────────────
# Candle data
# ─────────────────────────────────────────────

def get_candles(symbol: str, interval: str, count: int) -> list[Candle]:
    """
    Fetch the most recent `count` OHLCV candles for `symbol` at `interval`.

    Uses the Hyperliquid public REST endpoint:
      POST https://api.hyperliquid.xyz/info
      body: {"type": "candleSnapshot", "req": {"coin": "BTC", "interval": "15m",
             "startTime": <ms>, "endTime": <ms>}}

    Returns a list of Candle dicts ordered oldest → newest.
    """
    # Request enough history: each 15m candle = 900s
    interval_seconds = _interval_to_seconds(interval)
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - (count * interval_seconds * 1000)

    payload = {
        "type": "candleSnapshot",
        "req": {
            "coin": symbol,
            "interval": interval,
            "startTime": start_ms,
            "endTime": now_ms,
        },
    }

    def _fetch():
        resp = requests.post(
            f"{config.HYPERLIQUID_API_URL}/info",
            json=payload,
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()

    raw = _with_backoff(_fetch, label="get_candles")

    candles: list[Candle] = []
    for entry in raw:
        # Hyperliquid candle fields: t (open time ms), T (close time ms),
        # s (symbol), i (interval), o/h/l/c (prices), v (volume), n (trades)
        try:
            candles.append(
                Candle(
                    timestamp=int(entry["t"]),
                    open=float(entry["o"]),
                    high=float(entry["h"]),
                    low=float(entry["l"]),
                    close=float(entry["c"]),
                    volume=float(entry["v"]),
                )
            )
        except (KeyError, ValueError) as exc:
            logger.warning("Skipping malformed candle entry: %s — %s", entry, exc)

    candles.sort(key=lambda c: c["timestamp"])

    if len(candles) < 2:
        raise RuntimeError(
            f"Received only {len(candles)} candles for {symbol}/{interval}; "
            "check symbol and interval"
        )

    logger.debug("Fetched %d candles for %s/%s", len(candles), symbol, interval)
    return candles


def _interval_to_seconds(interval: str) -> int:
    """Convert an interval string like '15m', '1h', '1d' to seconds."""
    mapping = {"m": 60, "h": 3600, "d": 86400}
    unit = interval[-1]
    value = int(interval[:-1])
    return value * mapping.get(unit, 60)


# ─────────────────────────────────────────────
# Exchange client (lazy init)
# ─────────────────────────────────────────────

_exchange_client = None  # hyperliquid.exchange.Exchange instance


def _get_client():
    """
    Lazily initialise and return the Hyperliquid SDK exchange client.
    Raises RuntimeError in DRY_RUN mode (client should never be called then).
    """
    global _exchange_client
    if config.DRY_RUN:
        raise RuntimeError("_get_client() called in DRY_RUN mode — this is a bug")
    if _exchange_client is None:
        # Import here to avoid hard dependency when only using public endpoints
        from eth_account import Account
        from hyperliquid.exchange import Exchange
        from hyperliquid.utils import constants

        account = Account.from_key(config.HYPERLIQUID_PRIVATE_KEY)
        _exchange_client = Exchange(
            account,
            constants.MAINNET_API_URL,
        )
        logger.info("Hyperliquid exchange client initialised (wallet: %s)", account.address)
    return _exchange_client


# ─────────────────────────────────────────────
# Order helpers
# ─────────────────────────────────────────────

def set_leverage(symbol: str, leverage: int) -> None:
    """Set cross-margin leverage for the symbol. No-op in DRY_RUN."""
    if config.DRY_RUN:
        logger.info("[DRY RUN] Would set leverage to %dx for %s", leverage, symbol)
        return
    client = _get_client()

    def _set():
        result = client.update_leverage(leverage, symbol, is_cross=True)
        logger.info("Leverage set to %dx for %s: %s", leverage, symbol, result)

    _with_backoff(_set, label="set_leverage")


def get_current_price(symbol: str) -> float:
    """
    Fetch the latest mid price for `symbol` from the Hyperliquid info endpoint.
    Used for DRY_RUN position sizing.
    """
    def _fetch():
        resp = requests.post(
            f"{config.HYPERLIQUID_API_URL}/info",
            json={"type": "allMids"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        return float(data[symbol])

    return _with_backoff(_fetch, label="get_current_price")


def calculate_size(price: float, size_usd: float, leverage: int) -> float:
    """
    Calculate position size in base asset units.
    size_usd is the notional value (pre-leverage exposure).
    We floor to 4 decimal places to stay within Hyperliquid's precision.
    """
    raw = (size_usd * leverage) / price
    return math.floor(raw * 10_000) / 10_000


def place_market_order(
    symbol: str,
    side: str,          # "buy" or "sell"
    size: float,
    reduce_only: bool = False,
) -> dict:
    """
    Place a market order on Hyperliquid.

    In DRY_RUN mode this function logs what would be placed and returns a
    synthetic response dict — no real order is sent.

    Returns the exchange response dict on success.
    """
    label = f"{'[DRY RUN] ' if config.DRY_RUN else ''}place_market_order"
    logger.info(
        "%s: %s %s %.4f (reduce_only=%s)", label, side.upper(), symbol, size, reduce_only
    )

    if config.DRY_RUN:
        return {
            "status": "ok",
            "dry_run": True,
            "side": side,
            "symbol": symbol,
            "size": size,
            "reduce_only": reduce_only,
        }

    client = _get_client()

    def _place():
        is_buy = side.lower() == "buy"
        # Hyperliquid SDK market order: order_type={"limit": {"tif": "Ioc"}} with
        # a wide price acts as a market order (taker fill guaranteed).
        # Alternatively use the market order helper directly.
        result = client.market_open(
            symbol,
            is_buy,
            size,
            None,          # slippage — None uses SDK default (5%)
            reduce_only,
        )
        if result.get("status") != "ok":
            raise RuntimeError(f"Order rejected: {result}")
        return result

    return _with_backoff(_place, label="place_market_order")


def close_position(symbol: str, side: str, size: float) -> dict:
    """
    Close an existing position by placing a reduce-only market order
    in the opposite direction.

    `side`  : the side of the OPEN position ("LONG" or "SHORT")
    `size`  : the size in base asset units to close
    """
    close_side = "sell" if side == "LONG" else "buy"
    logger.info("Closing %s position: %s %.4f %s", side, close_side, size, symbol)
    return place_market_order(symbol, close_side, size, reduce_only=True)


# ─────────────────────────────────────────────
# Position query (live exchange)
# ─────────────────────────────────────────────

def get_open_position(symbol: str) -> Optional[dict]:
    """
    Query the exchange for any currently open position in `symbol`.
    Returns a dict with keys: side, size, entry_price — or None if flat.
    Only used to reconcile state after restart; not called in DRY_RUN.
    """
    if config.DRY_RUN:
        return None

    from hyperliquid.info import Info
    from hyperliquid.utils import constants
    from eth_account import Account

    account = Account.from_key(config.HYPERLIQUID_PRIVATE_KEY)
    info = Info(constants.MAINNET_API_URL)

    def _query():
        state = info.user_state(account.address)
        for pos in state.get("assetPositions", []):
            p = pos.get("position", {})
            if p.get("coin") == symbol:
                sz = float(p.get("szi", 0))
                if sz != 0:
                    return {
                        "side": "LONG" if sz > 0 else "SHORT",
                        "size": abs(sz),
                        "entry_price": float(p.get("entryPx", 0)),
                    }
        return None

    return _with_backoff(_query, label="get_open_position")
