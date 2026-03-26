"""
bot.py — Main entry point for the VWAP Cross trading bot.

Architecture:
  - Runs a loop every LOOP_INTERVAL_SECONDS (15 minutes)
  - Each iteration:
      1. Fetch latest candles from Hyperliquid
      2. Compute VWAP cross + RSI signal
      3. If flat: check for entry signal → place order
      4. If in position: check for exit signal → close position
  - Position state persisted to JSON file for crash recovery
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

class PositionState:
    """
    In-memory position tracker persisted to a JSON file so state survives
    bot restarts (important when deployed on Railway).
    """

    def __init__(self, filepath: str):
        self.filepath = filepath
        self.side: Optional[str] = None          # "LONG" | "SHORT" | None
        self.size: float = 0.0                   # base asset units
        self.entry_price: float = 0.0
        self.entry_time: float = 0.0             # unix timestamp
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

    def _save(self) -> None:
        try:
            with open(self.filepath, "w") as f:
                json.dump(
                    {
                        "side": self.side,
                        "size": self.size,
                        "entry_price": self.entry_price,
                        "entry_time": self.entry_time,
                    },
                    f,
                    indent=2,
                )
        except OSError as exc:
            logger.warning("Could not save position state: %s", exc)

    def _load(self) -> None:
        if not os.path.exists(self.filepath):
            return
        try:
            with open(self.filepath) as f:
                data = json.load(f)
            self.side = data.get("side")
            self.size = float(data.get("size", 0))
            self.entry_price = float(data.get("entry_price", 0))
            self.entry_time = float(data.get("entry_time", 0))
            if self.side:
                logger.info(
                    "Restored position from state file: %s %.4f @ $%.2f",
                    self.side, self.size, self.entry_price,
                )
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            logger.warning("Could not load position state: %s", exc)


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

def run_loop(state: PositionState) -> None:
    """Execute a single strategy iteration."""
    try:
        candles = exchange.get_candles(
            config.SYMBOL, config.CANDLE_INTERVAL, config.CANDLE_LOOKBACK
        )
    except Exception as exc:
        logger.error("Failed to fetch candles: %s", exc)
        telegram.send_error("get_candles failed", exc)
        return

    try:
        result = strategy.compute_signal(candles)
    except Exception as exc:
        logger.error("Strategy computation error: %s", exc)
        telegram.send_error("compute_signal failed", exc)
        return

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

    while not _shutdown_requested:
        loop_start = time.time()
        loop_count += 1
        logger.info("─── Loop %d ───", loop_count)

        run_loop(state)

        # Periodic health heartbeat
        if loop_count % config.HEALTH_LOG_INTERVAL == 0:
            uptime = time.time() - start_time
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
