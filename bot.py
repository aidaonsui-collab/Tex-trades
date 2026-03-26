"""
bot.py — Main entry point for the VWAP Cross trading bot.

Architecture:
  - Runs a loop every LOOP_INTERVAL_SECONDS (15 minutes)
  - Each iteration:
      1. Fetch latest candles from Hyperliquid
      2. Compute VWAP cross + RSI signal
      3. If flat: check for entry signal → place order
      4. If in position: check for exit signal → close position
  - Position state persisted to Upstash Redis (if configured) or local JSON file
  - Telegram alerts on every meaningful event
  - Graceful shutdown on SIGTERM / SIGINT
  - Periodic health heartbeat every ~1 hour

Usage:
  python bot.py
"""

import json
import logging
import math
import os
import signal
import sys
import time
from typing import Optional

import requests

import config
import exchange
import strategy
import telegram

# ─────────────────────────────────────────────
# Logging setup
# ─────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("bot")

# ─────────────────────────────────────────────
# Position state
# ─────────────────────────────────────────────

class RedisState:
    """
    Upstash Redis backend for position state, accessed via the Upstash HTTP
    REST API.  No Redis client library is required — uses the `requests`
    package that is already in requirements.txt.

    Upstash REST API docs: https://upstash.com/docs/redis/features/restapi
    """

    # Redis key used to store the position state JSON blob
    KEY = "vwap_bot:position"

    def __init__(self, url: str, token: str) -> None:
        # Remove any trailing slash so URL construction is consistent
        self.base_url = url.rstrip("/")
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    def _exec(self, *args):
        """
        Execute an arbitrary Redis command via the Upstash REST endpoint.
        Commands are sent as a JSON array: ["COMMAND", "arg1", "arg2", ...].
        Returns the "result" field from the Upstash JSON response.
        """
        resp = requests.post(
            self.base_url,
            headers=self.headers,
            json=list(args),
            timeout=5,
        )
        resp.raise_for_status()
        return resp.json().get("result")

    def save(self, data: dict) -> bool:
        """
        Serialise `data` to a JSON string and store it in Redis under KEY.
        Returns True on success, False if the request failed (caller should
        fall back to local file storage).
        """
        try:
            self._exec("SET", self.KEY, json.dumps(data))
            return True
        except Exception as exc:
            logger.warning("Upstash Redis save failed: %s", exc)
            return False

    def load(self) -> Optional[dict]:
        """
        Retrieve and deserialise position state from Redis.
        Returns None if the key does not exist or the request failed.
        """
        try:
            raw = self._exec("GET", self.KEY)
            if raw is None:
                return None
            return json.loads(raw)
        except Exception as exc:
            logger.warning("Upstash Redis load failed: %s", exc)
            return None


class PositionState:
    """
    In-memory position tracker with pluggable persistence backends.

    Priority order:
      1. Upstash Redis (if UPSTASH_REDIS_REST_URL + UPSTASH_REDIS_REST_TOKEN
         are set in the environment) — survives Railway restarts/redeploys.
      2. Local JSON file (STATE_FILE) — used when Upstash is not configured,
         or as an emergency write-through fallback if a Redis save fails.

    The public interface (open / close / is_open) is unchanged — the rest of
    bot.py does not need to know which backend is active.
    """

    def __init__(self, filepath: str) -> None:
        self.filepath = filepath
        self.side: Optional[str] = None       # "LONG" | "SHORT" | None
        self.size: float = 0.0                # base asset units
        self.entry_price: float = 0.0
        self.entry_time: float = 0.0          # unix timestamp

        # Select backend based on environment variables
        redis_url = os.getenv("UPSTASH_REDIS_REST_URL", "").strip()
        redis_token = os.getenv("UPSTASH_REDIS_REST_TOKEN", "").strip()
        if redis_url and redis_token:
            self._redis: Optional[RedisState] = RedisState(redis_url, redis_token)
            logger.info("Position state backend: Upstash Redis")
        else:
            self._redis = None
            logger.info("Position state backend: local file (%s)", filepath)

        self._load()

    def is_open(self) -> bool:
        return self.side is not None

    def open(self, side: str, size: float, price: float) -> None:
        self.side = side
        self.size = size
        self.entry_price = price
        self.entry_time = time.time()
        self._save()

    def close(self) -> dict:
        """Clear state and return a snapshot of the closed position."""
        snapshot = {
            "side": self.side,
            "size": self.size,
            "entry_price": self.entry_price,
            "entry_time": self.entry_time,
        }
        self.side = None
        self.size = 0.0
        self.entry_price = 0.0
        self.entry_time = 0.0
        self._save()
        return snapshot

    # ── Private helpers ────────────────────────────────────────────────────

    def _current_data(self) -> dict:
        return {
            "side": self.side,
            "size": self.size,
            "entry_price": self.entry_price,
            "entry_time": self.entry_time,
        }

    def _save(self) -> None:
        data = self._current_data()
        if self._redis is not None:
            # Attempt Redis first; write to local file as emergency backup
            # if the Redis call fails (e.g., network blip).
            if not self._redis.save(data):
                logger.warning("Redis save failed — writing emergency backup to local file")
                self._save_to_file(data)
        else:
            self._save_to_file(data)

    def _save_to_file(self, data: dict) -> None:
        try:
            with open(self.filepath, "w") as f:
                json.dump(data, f, indent=2)
        except OSError as exc:
            logger.warning("Could not save position state to file: %s", exc)

    def _load(self) -> None:
        data: Optional[dict] = None

        if self._redis is not None:
            data = self._redis.load()
            if data is None:
                # Redis returned nothing (first run, key expired, etc.).
                # Check the local file so we don't lose state on the first
                # deployment after adding Upstash.
                logger.info(
                    "No state found in Redis — checking local file fallback (%s)",
                    self.filepath,
                )
                data = self._load_from_file()
        else:
            data = self._load_from_file()

        if data:
            self.side = data.get("side")
            self.size = float(data.get("size", 0))
            self.entry_price = float(data.get("entry_price", 0))
            self.entry_time = float(data.get("entry_time", 0))
            if self.side:
                logger.info(
                    "Restored position from state: %s %.4f @ $%.2f",
                    self.side, self.size, self.entry_price,
                )

    def _load_from_file(self) -> Optional[dict]:
        if not os.path.exists(self.filepath):
            return None
        try:
            with open(self.filepath) as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            logger.warning("Could not load position state from file: %s", exc)
            return None


# ─────────────────────────────────────────────
# Shutdown handler
# ─────────────────────────────────────────────

_shutdown_requested = False


def _handle_shutdown(signum, frame):
    global _shutdown_requested
    logger.info("Shutdown signal received (%s) — finishing current loop then exiting", signum)
    _shutdown_requested = True


signal.signal(signal.SIGTERM, _handle_shutdown)
signal.signal(signal.SIGINT, _handle_shutdown)


# ─────────────────────────────────────────────
# Core trading logic
# ─────────────────────────────────────────────

def handle_entry(result: strategy.SignalResult, state: PositionState) -> None:
    """
    Process a potential entry signal when no position is open.
    Computes size, places market order, updates state, sends Telegram alert.
    """
    sig = result.signal
    price = result.price

    # Calculate position size in base asset units
    size = exchange.calculate_size(price, config.POSITION_SIZE_USD, config.LEVERAGE)
    if size <= 0:
        logger.error(
            "Computed position size is zero (price=%.2f, usd=%.2f, lev=%d)",
            price, config.POSITION_SIZE_USD, config.LEVERAGE,
        )
        return

    logger.info(
        "Entry signal: %s  price=$%.2f  size=%.4f  leverage=%dx",
        sig, price, size, config.LEVERAGE,
    )

    # Telegram: signal detected
    telegram.send_signal(sig, price, result.vwap, result.rsi)

    # Set leverage (no-op in DRY_RUN)
    try:
        exchange.set_leverage(config.SYMBOL, config.LEVERAGE)
    except Exception as exc:
        logger.error("Failed to set leverage: %s", exc)
        telegram.send_error("set_leverage failed", exc)
        return

    # Place order
    order_side = "buy" if sig == "LONG" else "sell"
    try:
        exchange.place_market_order(config.SYMBOL, order_side, size)
    except Exception as exc:
        logger.error("Order placement failed: %s", exc)
        telegram.send_error("place_market_order failed", exc)
        return

    # Update state
    state.open(sig, size, price)

    # Telegram: order placed
    telegram.send_order_placed(
        sig, size, price, config.LEVERAGE, dry_run=config.DRY_RUN
    )


def handle_exit(result: strategy.SignalResult, state: PositionState) -> None:
    """
    Process an exit when the current signal flips against the open position.
    Closes the position, calculates estimated PnL, updates state, alerts.
    """
    exit_price = result.price
    snapshot = {
        "side": state.side,
        "size": state.size,
        "entry_price": state.entry_price,
    }

    logger.info(
        "Exit signal: %s position at $%.2f (entry was $%.2f)",
        snapshot["side"], exit_price, snapshot["entry_price"],
    )

    try:
        exchange.close_position(config.SYMBOL, snapshot["side"], snapshot["size"])
    except Exception as exc:
        logger.error("Failed to close position: %s", exc)
        telegram.send_error("close_position failed", exc)
        return

    # Estimate PnL (before fees / funding)
    entry = snapshot["entry_price"]
    size = snapshot["size"]
    if snapshot["side"] == "LONG":
        pnl = (exit_price - entry) * size * config.LEVERAGE
    else:
        pnl = (entry - exit_price) * size * config.LEVERAGE

    state.close()

    telegram.send_position_closed(
        snapshot["side"],
        entry,
        exit_price,
        size,
        pnl,
        dry_run=config.DRY_RUN,
    )


# ─────────────────────────────────────────────
# Main loop
# ─────────────────────────────────────────────

def run_loop(state: PositionState):
    """Execute a single strategy iteration. Returns the strategy result for heartbeat tracking."""
    try:
        candles = exchange.get_candles(
            config.SYMBOL, config.CANDLE_INTERVAL, config.CANDLE_LOOKBACK
        )
    except Exception as exc:
        logger.error("Failed to fetch candles: %s", exc)
        telegram.send_error("get_candles failed", exc)
        return None

    try:
        result = strategy.compute_signal(candles)
    except Exception as exc:
        logger.error("Strategy computation error: %s", exc)
        telegram.send_error("compute_signal failed", exc)
        return None

    logger.info(
        "Signal=%s  price=$%.2f  vwap=$%.2f  rsi=%.2f  position=%s",
        result.signal, result.price, result.vwap, result.rsi,
        state.side if state.is_open() else "FLAT",
    )

    if state.is_open():
        # Check for exit
        if strategy.is_exit_signal(result.signal, state.side):
            handle_exit(result, state)
        else:
            logger.debug("Holding %s position — no exit signal", state.side)
    else:
        # Check for entry
        if result.signal in ("LONG", "SHORT"):
            handle_entry(result, state)
        else:
            logger.debug("Flat — no entry signal this candle")

    return result


def main() -> None:
    logger.info("=" * 60)
    logger.info("VWAP Cross Trading Bot starting up")
    logger.info("Symbol=%s  Leverage=%dx  Size=$%.0f  DryRun=%s",
                config.SYMBOL, config.LEVERAGE,
                config.POSITION_SIZE_USD, config.DRY_RUN)
    logger.info("=" * 60)

    # Validate config — raises on misconfiguration
    try:
        config.validate()
    except EnvironmentError as exc:
        logger.critical("Config validation failed:\n%s", exc)
        sys.exit(1)

    # Reconcile state with live exchange (only in live mode)
    state = PositionState(config.STATE_FILE)
    if not config.DRY_RUN and not state.is_open():
        try:
            live_pos = exchange.get_open_position(config.SYMBOL)
            if live_pos:
                logger.info(
                    "Found open exchange position not in state file — syncing: %s", live_pos
                )
                state.open(live_pos["side"], live_pos["size"], live_pos["entry_price"])
        except Exception as exc:
            logger.warning("Could not reconcile position state: %s", exc)

    # Send startup notification
    telegram.send_startup()

    start_time = time.time()
    loop_count = 0
    _last_result = None  # track latest market snapshot for heartbeat

    while not _shutdown_requested:
        loop_start = time.time()
        loop_count += 1
        logger.info("─── Loop %d ───", loop_count)

        result = run_loop(state)
        if result is not None:
            _last_result = result

        # Periodic health heartbeat — include market snapshot if available
        if loop_count % config.HEALTH_LOG_INTERVAL == 0:
            uptime = time.time() - start_time
            if _last_result is not None:
                telegram.send_health(loop_count, uptime,
                                     price=_last_result.price,
                                     vwap=_last_result.vwap,
                                     rsi=_last_result.rsi)
            else:
                telegram.send_health(loop_count, uptime)
            logger.info("Health check — uptime=%.1fh  loops=%d", uptime / 3600, loop_count)

        if _shutdown_requested:
            break

        # Sleep until next candle boundary
        elapsed = time.time() - loop_start
        sleep_time = max(0, config.LOOP_INTERVAL_SECONDS - elapsed)
        logger.info("Sleeping %.1fs until next iteration", sleep_time)

        # Sleep in small increments so we can respond to shutdown quickly
        slept = 0.0
        while slept < sleep_time and not _shutdown_requested:
            chunk = min(5.0, sleep_time - slept)
            time.sleep(chunk)
            slept += chunk

    logger.info("Shutdown complete after %d loops", loop_count)


if __name__ == "__main__":
    main()
