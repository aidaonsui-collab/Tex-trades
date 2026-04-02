"""
bot.py — Main entry point for the Momentum Breakout trading bot.

Architecture:
  - Runs a loop every LOOP_INTERVAL_SECONDS (1 hour for 1h candles)
  - Each iteration:
      1. Fetch latest candles from Hyperliquid
      2. Check ATR stop/TP exit if position is open
      3. Compute momentum breakout signal
      4. If flat: check for entry signal → place order
      5. If in position: check for signal flip exit
  - Position state persisted to Upstash Redis or local JSON
  - Telegram alerts with SL/TP levels, weekly P&L, and competition metrics
  - Weekly summary sent at season reset
"""

import json
import logging
import math
import os
import signal
import sys
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests

import config
import exchange
import strategy
import telegram

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("bot")


# ─────────────────────────────────────────────
# Weekly P&L tracker
# ─────────────────────────────────────────────

class WeeklyTracker:
    """Track trades and P&L for the current weekly season."""

    def __init__(self):
        self.trades: list[dict] = []
        self.week_start = self._current_week_start()

    @staticmethod
    def _current_week_start() -> datetime:
        now = datetime.now(timezone.utc)
        # Week starts Monday 00:00 UTC
        monday = now - timedelta(days=now.weekday())
        monday = monday.replace(hour=0, minute=0, second=0, microsecond=0)
        return monday

    def check_reset(self) -> Optional[dict]:
        """Check if we've entered a new week. Returns summary if reset occurred."""
        current_week = self._current_week_start()
        if current_week > self.week_start and self.trades:
            summary = self.get_summary()
            self.trades = []
            self.week_start = current_week
            return summary
        elif current_week > self.week_start:
            self.week_start = current_week
        return None

    def add_trade(self, pnl: float, side: str, reason: str):
        self.trades.append({"pnl": pnl, "side": side, "reason": reason,
                            "ts": datetime.now(timezone.utc).isoformat()})

    @property
    def total_pnl(self) -> float:
        return sum(t["pnl"] for t in self.trades)

    @property
    def total_trades(self) -> int:
        return len(self.trades)

    @property
    def wins(self) -> int:
        return sum(1 for t in self.trades if t["pnl"] > 0)

    def get_summary(self) -> dict:
        if not self.trades:
            return {"trades": 0, "wins": 0, "pnl": 0, "sortino": 0, "pf": 0,
                    "best": 0, "worst": 0}
        wins = [t for t in self.trades if t["pnl"] > 0]
        losses = [t for t in self.trades if t["pnl"] <= 0]
        pnls = [t["pnl"] for t in self.trades]

        gw = sum(t["pnl"] for t in wins) if wins else 0
        gl = abs(sum(t["pnl"] for t in losses)) if losses else 0.01
        pf = gw / gl if gl > 0 else 10.0

        # Sortino ratio
        rets = [p / config.POSITION_SIZE_USD for p in pnls]
        mean_r = sum(rets) / len(rets)
        downside = [min(0, r) ** 2 for r in rets]
        dd = math.sqrt(sum(downside) / len(downside))
        sortino = mean_r / dd if dd > 0 else (10.0 if mean_r > 0 else 0)

        return {
            "trades": len(self.trades), "wins": len(wins),
            "pnl": self.total_pnl, "sortino": sortino, "pf": pf,
            "best": max(pnls), "worst": min(pnls),
        }


# ─────────────────────────────────────────────
# Position state (same as before, with ATR tracking)
# ─────────────────────────────────────────────

class RedisState:
    KEY = "mombreak_bot:position"

    def __init__(self, url: str, token: str):
        self.base_url = url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def _exec(self, *args):
        resp = requests.post(self.base_url, headers=self.headers, json=list(args), timeout=5)
        resp.raise_for_status()
        return resp.json().get("result")

    def save(self, data: dict) -> bool:
        try: self._exec("SET", self.KEY, json.dumps(data)); return True
        except Exception as e: logger.warning("Redis save failed: %s", e); return False

    def load(self) -> Optional[dict]:
        try:
            raw = self._exec("GET", self.KEY)
            return json.loads(raw) if raw else None
        except Exception as e: logger.warning("Redis load failed: %s", e); return None


class PositionState:
    def __init__(self, filepath: str):
        self.filepath = filepath
        self.side: Optional[str] = None
        self.size: float = 0.0
        self.entry_price: float = 0.0
        self.entry_time: float = 0.0
        self.entry_atr: float = 0.0  # ATR at entry for stop/TP calculation

        redis_url = os.getenv("UPSTASH_REDIS_REST_URL", "").strip()
        redis_token = os.getenv("UPSTASH_REDIS_REST_TOKEN", "").strip()
        if redis_url and redis_token:
            self._redis: Optional[RedisState] = RedisState(redis_url, redis_token)
            logger.info("State backend: Upstash Redis")
        else:
            self._redis = None
            logger.info("State backend: local file (%s)", filepath)
        self._load()

    def is_open(self) -> bool:
        return self.side is not None

    def open(self, side: str, size: float, price: float, entry_atr: float) -> None:
        self.side = side
        self.size = size
        self.entry_price = price
        self.entry_time = time.time()
        self.entry_atr = entry_atr
        self._save()

    def close(self) -> dict:
        snapshot = {"side": self.side, "size": self.size,
                    "entry_price": self.entry_price, "entry_time": self.entry_time,
                    "entry_atr": self.entry_atr}
        self.side = None
        self.size = 0.0
        self.entry_price = 0.0
        self.entry_time = 0.0
        self.entry_atr = 0.0
        self._save()
        return snapshot

    def _current_data(self) -> dict:
        return {"side": self.side, "size": self.size, "entry_price": self.entry_price,
                "entry_time": self.entry_time, "entry_atr": self.entry_atr}

    def _save(self):
        data = self._current_data()
        if self._redis and not self._redis.save(data):
            self._save_file(data)
        elif not self._redis:
            self._save_file(data)

    def _save_file(self, data):
        try:
            with open(self.filepath, "w") as f: json.dump(data, f, indent=2)
        except OSError as e: logger.warning("File save failed: %s", e)

    def _load(self):
        data = None
        if self._redis:
            data = self._redis.load()
            if not data: data = self._load_file()
        else:
            data = self._load_file()
        if data:
            self.side = data.get("side")
            self.size = float(data.get("size", 0))
            self.entry_price = float(data.get("entry_price", 0))
            self.entry_time = float(data.get("entry_time", 0))
            self.entry_atr = float(data.get("entry_atr", 0))
            if self.side:
                logger.info("Restored: %s %.4f @ $%.2f (ATR=%.2f)",
                            self.side, self.size, self.entry_price, self.entry_atr)

    def _load_file(self) -> Optional[dict]:
        if not os.path.exists(self.filepath): return None
        try:
            with open(self.filepath) as f: return json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("File load failed: %s", e); return None


# ─────────────────────────────────────────────
# Shutdown handler
# ─────────────────────────────────────────────

_shutdown_requested = False

def _handle_shutdown(signum, frame):
    global _shutdown_requested
    logger.info("Shutdown signal (%s) — finishing current loop", signum)
    _shutdown_requested = True

signal.signal(signal.SIGTERM, _handle_shutdown)
signal.signal(signal.SIGINT, _handle_shutdown)


# ─────────────────────────────────────────────
# Trading logic
# ─────────────────────────────────────────────

def handle_entry(result: strategy.SignalResult, state: PositionState,
                 tracker: WeeklyTracker) -> None:
    sig = result.signal
    price = result.price

    size = exchange.calculate_size(price, config.POSITION_SIZE_USD, config.LEVERAGE)
    if size <= 0:
        logger.error("Zero size (price=%.2f)", price)
        return

    logger.info("Entry: %s price=$%.2f size=%.4f atr=%.2f lev=%dx",
                sig, price, size, result.atr, config.LEVERAGE)

    # Telegram signal alert with all indicator values
    telegram.send_signal(
        sig, price, result.atr, result.roc,
        result.channel_high, result.channel_low, result.ema_trend,
        result.volume, result.volume_avg,
    )

    try:
        exchange.set_leverage(config.SYMBOL, config.LEVERAGE)
    except Exception as exc:
        logger.error("Leverage failed: %s", exc)
        telegram.send_error("set_leverage failed", exc)
        return

    order_side = "buy" if sig == "LONG" else "sell"
    try:
        exchange.place_market_order(config.SYMBOL, order_side, size)
    except Exception as exc:
        logger.error("Order failed: %s", exc)
        telegram.send_error("place_market_order failed", exc)
        return

    state.open(sig, size, price, result.atr)

    telegram.send_order_placed(sig, size, price, config.LEVERAGE, result.atr,
                               dry_run=config.DRY_RUN)


def handle_exit(exit_price: float, exit_reason: str, state: PositionState,
                tracker: WeeklyTracker) -> None:
    snapshot = {"side": state.side, "size": state.size,
                "entry_price": state.entry_price}

    logger.info("Exit: %s @ $%.2f (entry $%.2f) reason=%s",
                snapshot["side"], exit_price, snapshot["entry_price"], exit_reason)

    try:
        exchange.close_position(config.SYMBOL, snapshot["side"], snapshot["size"])
    except Exception as exc:
        logger.error("Close failed: %s", exc)
        telegram.send_error("close_position failed", exc)
        return

    entry = snapshot["entry_price"]
    size = snapshot["size"]
    if snapshot["side"] == "LONG":
        pnl = (exit_price - entry) * size * config.LEVERAGE
    else:
        pnl = (entry - exit_price) * size * config.LEVERAGE

    state.close()
    tracker.add_trade(pnl, snapshot["side"], exit_reason)

    telegram.send_position_closed(
        snapshot["side"], entry, exit_price, size, pnl,
        exit_reason=exit_reason,
        weekly_pnl=tracker.total_pnl,
        weekly_trades=tracker.total_trades,
        weekly_wins=tracker.wins,
        dry_run=config.DRY_RUN,
    )


# ─────────────────────────────────────────────
# Main loop
# ─────────────────────────────────────────────

def run_loop(state: PositionState, tracker: WeeklyTracker, candles: list) -> Optional[strategy.SignalResult]:
    """Single strategy iteration."""
    # Check weekly reset
    summary = tracker.check_reset()
    if summary and summary["trades"] > 0:
        telegram.send_weekly_summary(
            summary["trades"], summary["wins"], summary["pnl"],
            summary["sortino"], summary["pf"],
            summary["best"], summary["worst"],
        )

    # Compute signal
    try:
        result = strategy.compute_signal(candles)
    except Exception as exc:
        logger.error("Strategy error: %s", exc)
        telegram.send_error("compute_signal failed", exc)
        return None

    logger.info("Signal=%s price=$%.2f atr=$%.2f roc=%.2f%% trend=%s pos=%s",
                result.signal, result.price, result.atr, result.roc,
                result.trend_direction, state.side or "FLAT")

    if state.is_open():
        # Check ATR stop/TP on latest candle
        latest_candle = candles[-1]
        should_exit, reason, exit_price = strategy.check_exit(
            latest_candle, state.side, state.entry_price, state.entry_atr
        )

        if should_exit:
            handle_exit(exit_price, reason, state, tracker)
        elif strategy.is_exit_signal(result.signal, state.side):
            handle_exit(result.price, "signal", state, tracker)
        else:
            logger.debug("Holding %s — no exit", state.side)
    else:
        if result.signal in ("LONG", "SHORT"):
            handle_entry(result, state, tracker)
        else:
            logger.debug("Flat — no signal")

    return result


def main() -> None:
    logger.info("=" * 60)
    logger.info("Momentum Breakout Bot starting")
    logger.info("Symbol=%s  Interval=%s  Leverage=%dx  Size=$%.0f  DryRun=%s",
                config.SYMBOL, config.CANDLE_INTERVAL, config.LEVERAGE,
                config.POSITION_SIZE_USD, config.DRY_RUN)
    logger.info("Breakout: LB=%d  ROC=%.1f%%  Vol=%.1fx  EMA=%d  ATR=%.1fx  RR=%.1f:1",
                config.BREAKOUT_LOOKBACK, config.ROC_THRESHOLD,
                config.VOLUME_MULTIPLIER, config.TREND_EMA_PERIOD,
                config.ATR_MULTIPLIER, config.REWARD_RISK_RATIO)
    logger.info("=" * 60)

    try:
        config.validate()
    except EnvironmentError as exc:
        logger.critical("Config error:\n%s", exc)
        sys.exit(1)

    state = PositionState(config.STATE_FILE)
    tracker = WeeklyTracker()

    # Reconcile with exchange
    if not config.DRY_RUN and not state.is_open():
        try:
            live_pos = exchange.get_open_position(config.SYMBOL)
            if live_pos:
                logger.info("Syncing live position: %s", live_pos)
                state.open(live_pos["side"], live_pos["size"],
                           live_pos["entry_price"], 0)
        except Exception as exc:
            logger.warning("Position sync failed: %s", exc)

    telegram.send_startup()

    start_time = time.time()
    loop_count = 0
    _last_result = None

    while not _shutdown_requested:
        loop_start = time.time()
        loop_count += 1
        logger.info("─── Loop %d ───", loop_count)

        # Fetch candles
        try:
            candles = exchange.get_candles(
                config.SYMBOL, config.CANDLE_INTERVAL, config.CANDLE_LOOKBACK
            )
        except Exception as exc:
            logger.error("Candle fetch failed: %s", exc)
            telegram.send_error("get_candles failed", exc)
            candles = None

        if candles:
            result = run_loop(state, tracker, candles)
            if result:
                _last_result = result

        # Heartbeat
        if loop_count % config.HEALTH_LOG_INTERVAL == 0:
            uptime = time.time() - start_time
            if _last_result:
                telegram.send_health(
                    loop_count, uptime,
                    price=_last_result.price,
                    atr=_last_result.atr,
                    roc=_last_result.roc,
                    ema_trend=_last_result.ema_trend,
                    channel_high=_last_result.channel_high,
                    channel_low=_last_result.channel_low,
                    position_side=state.side,
                    position_entry=state.entry_price if state.is_open() else None,
                    weekly_pnl=tracker.total_pnl,
                    weekly_trades=tracker.total_trades,
                )
            else:
                telegram.send_health(loop_count, uptime)

        if _shutdown_requested:
            break

        elapsed = time.time() - loop_start
        sleep_time = max(0, config.LOOP_INTERVAL_SECONDS - elapsed)
        logger.info("Sleeping %.0fs", sleep_time)

        slept = 0.0
        while slept < sleep_time and not _shutdown_requested:
            chunk = min(5.0, sleep_time - slept)
            time.sleep(chunk)
            slept += chunk

    logger.info("Shutdown after %d loops", loop_count)


if __name__ == "__main__":
    main()
