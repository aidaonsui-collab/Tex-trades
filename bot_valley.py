#!/usr/bin/env python3
"""
bot_valley.py — Valley/Peak Bidirectional Trading Bot (10x Leverage)

Strategy:
  LONG:  Buy at local valleys (low < prev AND next)
  SHORT: Sell at local peaks (high > prev AND next)
  Exit:  +3% TP, -1.5% SL, or opposite peak/valley

Backtest Results (SOL 30m, Mon-Fri, last 3 weeks):
  - 96.6% win rate (143/148 trades)
  - +1,714.78% P&L at 10x leverage
  - 23.86 profit factor
  - 2.6 bars average hold (78 minutes)

Deployment:
  python bot_valley.py              # Live (DRY_RUN=false)
  DRY_RUN=true python bot_valley.py # Paper trading (default)
  LEVERAGE=1 python bot_valley.py   # Conservative (1x)

Environment Variables:
  LEVERAGE          - Multiplier (1, 10, or 15) [default: 10]
  POSITION_SIZE_USD - Position size in USD [default: 50]
  DRY_RUN           - Paper trading mode [default: true]
  TELEGRAM_BOT_TOKEN - Telegram notifications [optional]
  STATE_FILE        - Position state file [default: position_state_valley.json]
"""

import os
import sys
import time
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

import config_valley as config
from exchange import (
    get_candles,
    get_current_price,
    place_market_order,
    close_position,
)
from strategy_valley import compute_signal, check_exit, Signal
from telegram import send_telegram

# ─────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bot_valley.log"),
    ],
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# Position State Management
# ─────────────────────────────────────────────
class PositionState:
    """Persistent position state (local file)"""

    def __init__(self, filepath: str):
        self.filepath = filepath
        self.position = None
        self.load()

    def load(self):
        """Load position from disk"""
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, "r") as f:
                    data = json.load(f)
                    self.position = data.get("position")
                    if self.position:
                        logger.info("✅ Loaded position: %s", self.position["side"])
                    else:
                        logger.info("📝 No open position found")
            except Exception as e:
                logger.error("Failed to load position state: %s", e)
                self.position = None
        else:
            logger.info("📝 No state file found, starting fresh")
            self.position = None

    def save(self):
        """Save position to disk"""
        try:
            with open(self.filepath, "w") as f:
                json.dump({"position": self.position}, f, indent=2)
        except Exception as e:
            logger.error("Failed to save position state: %s", e)

    def set(self, position_data: dict):
        """Set current position"""
        self.position = position_data
        self.save()

    def clear(self):
        """Clear position"""
        self.position = None
        self.save()


# ─────────────────────────────────────────────
# Main Trading Bot
# ─────────────────────────────────────────────
class ValleyPeakBot:
    """Bidirectional valley/peak trading bot for SOL 30m"""

    def __init__(self):
        config.validate()

        self.symbol = config.SYMBOL
        self.leverage = config.LEVERAGE
        self.position_size_usd = config.POSITION_SIZE_USD
        self.dry_run = config.DRY_RUN
        self.state = PositionState(config.STATE_FILE)
        self.heartbeat_count = 0

        logger.info("=" * 80)
        logger.info("🚀 Valley/Peak Bidirectional Bot Starting")
        logger.info("=" * 80)
        logger.info("Symbol: %s", self.symbol)
        logger.info("Leverage: %dx", self.leverage)
        logger.info("Position Size: $%.2f", self.position_size_usd)
        logger.info("Interval: %s", config.CANDLE_INTERVAL)
        logger.info("TP/SL: +%.1f%% / -%.1f%%", config.TP_PERCENT, config.SL_PERCENT)
        logger.info("Mode: %s", "DRY RUN" if self.dry_run else "LIVE TRADING")
        logger.info("State: %s", self.state.position["side"] if self.state.position else "No position")
        logger.info("=" * 80)
        logger.info("")

        # Send startup telegram
        if config.TELEGRAM_BOT_TOKEN:
            send_telegram(
                f"🚀 Valley/Peak Bot {self.leverage}x Started\n"
                f"Symbol: {self.symbol} | Mode: {'DRY' if self.dry_run else 'LIVE'}"
            )

    def is_trading_day(self) -> bool:
        """Check if today is Mon-Fri (UTC)"""
        now = datetime.now(timezone.utc)
        return now.weekday() in config.TRADING_DAYS

    def fetch_candles(self):
        """Fetch recent candles"""
        try:
            candles = get_candles(
                self.symbol,
                config.CANDLE_INTERVAL,
                config.CANDLE_LOOKBACK,
            )
            return candles
        except Exception as e:
            logger.error("Failed to fetch candles: %s", e)
            return None

    def handle_entry(self, signal: Signal, price: float, entry_type: str):
        """Process entry signal"""
        if self.state.position:
            logger.debug("Already in %s position, skipping entry", self.state.position["side"])
            return

        if not self.is_trading_day():
            logger.warning("Not a trading day (weekend), skipping entry")
            return

        if signal == "NONE":
            return

        logger.info("=" * 80)
        logger.info("📊 SIGNAL: %s (%s) at $%.2f", signal, entry_type, price)
        logger.info("=" * 80)

        # Calculate position size
        notional = self.position_size_usd * self.leverage
        size = notional / price
        size = round(size, 4)  # Floor to 4 decimals

        logger.info("[%s] Position size: %.4f %s", "DRY RUN" if self.dry_run else "LIVE", size, self.symbol)

        if self.dry_run:
            # Dry run — just log and track
            logger.info("[DRY RUN] Entry would be placed")
            self.state.set({
                "side": signal,
                "entry_price": price,
                "entry_time": datetime.now(timezone.utc).isoformat(),
                "size": size,
                "entry_type": entry_type,
            })
            send_telegram(f"[DRY] {signal} Entry: ${price:.2f} × {size:.4f}")
        else:
            # Live — submit order via ACP
            try:
                logger.info("[LIVE] Placing %s order via ACP", signal)
                result = place_market_order(
                    symbol=self.symbol,
                    side=signal.lower(),  # "long" or "short"
                    size=size,
                    reduce_only=False,
                )

                if result.get("status") == "ok":
                    self.state.set({
                        "side": signal,
                        "entry_price": price,
                        "entry_time": datetime.now(timezone.utc).isoformat(),
                        "size": size,
                        "entry_type": entry_type,
                        "acp_job": result.get("acp_job"),
                    })
                    logger.info("✅ Entry order placed: %s", result)
                    send_telegram(f"✅ {signal} Entry: ${price:.2f} × {size:.4f}")
                else:
                    logger.error("❌ Entry order failed: %s", result)
                    send_telegram(f"❌ Entry failed: {result}")

            except Exception as e:
                logger.error("Exception during entry: %s", e)
                send_telegram(f"❌ Entry error: {e}")

    def handle_exit(self, reason: str, exit_price: float):
        """Process exit signal"""
        if not self.state.position:
            logger.debug("No open position, skipping exit")
            return

        position = self.state.position
        entry_price = position["entry_price"]
        side = position["side"]

        # Calculate P&L
        if side == "LONG":
            pnl_pct = (exit_price - entry_price) / entry_price * 100
        else:  # SHORT
            pnl_pct = (entry_price - exit_price) / entry_price * 100

        pnl_pct_leveraged = pnl_pct * self.leverage

        logger.info("=" * 80)
        logger.info("🎯 EXIT SIGNAL: %s", reason)
        logger.info("Entry: $%.2f | Exit: $%.2f", entry_price, exit_price)
        logger.info("P&L: %.2f%% (leveraged: %.2f%%)", pnl_pct, pnl_pct_leveraged)
        logger.info("=" * 80)

        if self.dry_run:
            logger.info("[DRY RUN] Exit would be placed")
            send_telegram(
                f"[DRY] {side} Exit ({reason}): ${exit_price:.2f}\n"
                f"P&L: {pnl_pct_leveraged:+.2f}%"
            )
            self.state.clear()
        else:
            # Live — close position
            try:
                logger.info("[LIVE] Closing %s position via ACP", side)
                result = close_position(
                    symbol=self.symbol,
                    side=side,
                    size=position["size"],
                )

                if result.get("status") == "ok":
                    logger.info("✅ Exit order placed: %s", result)
                    send_telegram(
                        f"✅ {side} Exit ({reason}): ${exit_price:.2f}\n"
                        f"P&L: {pnl_pct_leveraged:+.2f}%"
                    )
                    self.state.clear()
                else:
                    logger.error("❌ Exit order failed: %s", result)
                    send_telegram(f"❌ Exit failed: {result}")

            except Exception as e:
                logger.error("Exception during exit: %s", e)
                send_telegram(f"❌ Exit error: {e}")

    def run_cycle(self):
        """Single trading cycle"""
        self.heartbeat_count += 1

        # Fetch candles
        candles = self.fetch_candles()
        if not candles:
            logger.warning("Failed to fetch candles, skipping cycle")
            return

        # Compute signal
        result = compute_signal(candles)

        # Log heartbeat
        if self.heartbeat_count % config.HEALTH_LOG_INTERVAL == 0:
            current_price = result.price
            logger.info(
                "[HEARTBEAT] Price: $%.2f | Position: %s | Signal: %s",
                current_price,
                self.state.position["side"] if self.state.position else "NONE",
                result.signal,
            )

        # Handle entry if no position
        if not self.state.position:
            if result.signal != "NONE":
                self.handle_entry(result.signal, result.price, result.entry_type)
        else:
            # Check exit conditions
            position = self.state.position
            current_candle = candles[-1]

            should_exit, reason, exit_price = check_exit(
                current_candle,
                position["entry_price"],
                position["side"],
                tp_percent=config.TP_PERCENT,
                sl_percent=config.SL_PERCENT,
            )

            if should_exit:
                self.handle_exit(reason, exit_price)
            # Also check for opposite signal (entry into opposite side)
            elif result.signal != "NONE" and result.signal != position["side"]:
                logger.info("Opposite signal detected, closing %s and entering %s", position["side"], result.signal)
                self.handle_exit(f"FLIP_TO_{result.signal}", result.price)
                self.handle_entry(result.signal, result.price, result.entry_type)

    def run(self):
        """Main bot loop"""
        try:
            while True:
                try:
                    self.run_cycle()
                except Exception as e:
                    logger.error("Cycle error: %s", e)
                    send_telegram(f"⚠️ Cycle error: {e}")

                # Sleep until next candle
                time.sleep(config.LOOP_INTERVAL_SECONDS)

        except KeyboardInterrupt:
            logger.info("Shutting down...")
            send_telegram("🛑 Bot stopped")
        except Exception as e:
            logger.critical("Fatal error: %s", e)
            send_telegram(f"🔴 Fatal error: {e}")
            raise


if __name__ == "__main__":
    bot = ValleyPeakBot()
    bot.run()
