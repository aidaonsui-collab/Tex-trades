"""
bot_v4.py — Stochastic+MACD Trading Bot for DegenClaw

Architecture:
  - Runs every LOOP_INTERVAL (1h for 1h candles)
  - Fetches candles from Hyperliquid
  - If flat: check for StochK cross + MACD confirmation → enter
  - If in position: check TP/SL on latest candle → exit
  - No signal-flip exits (hold until TP or SL)
  - Trade history persisted to Redis for dashboard
  - Telegram alerts for signals, orders, closes, heartbeat, weekly summary
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

import sys
import config_v4 as config
sys.modules['config'] = config
import exchange_v2 as exchange
import strategy_v4 as strategy
import telegram

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("bot_v4")

_shutdown_requested = False


def _handle_signal(signum, frame):
    global _shutdown_requested
    logger.info("Shutdown signal received")
    _shutdown_requested = True


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


# ─────────────────────────────────────────────
# Weekly tracker (same as bot.py)
# ─────────────────────────────────────────────

class WeeklyTracker:
    def __init__(self):
        self.trades: list[dict] = []
        self.week_start = self._current_week_start()

    @staticmethod
    def _current_week_start() -> datetime:
        now = datetime.now(timezone.utc)
        monday = now - timedelta(days=now.weekday())
        return monday.replace(hour=0, minute=0, second=0, microsecond=0)

    def check_reset(self) -> Optional[dict]:
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
        rets = [p / config.POSITION_SIZE_USD for p in pnls]
        mean_r = sum(rets) / len(rets)
        downside = [min(0, r) ** 2 for r in rets]
        dd = math.sqrt(sum(downside) / len(downside))
        sortino = mean_r / dd if dd > 0 else (10.0 if mean_r > 0 else 0)
        return {"trades": len(self.trades), "wins": len(wins), "pnl": self.total_pnl,
                "sortino": sortino, "pf": pf, "best": max(pnls), "worst": min(pnls)}


# ─────────────────────────────────────────────
# Redis state (same as bot.py)
# ─────────────────────────────────────────────

class RedisState:
    KEY = "stochmacd_bot:position"
    HISTORY_KEY = "stochmacd_bot:trade_history"

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

    def append_trade(self, trade: dict) -> bool:
        try:
            raw = self._exec("GET", self.HISTORY_KEY)
            history = json.loads(raw) if raw else []
            history.append(trade)
            if len(history) > 500:
                history = history[-500:]
            self._exec("SET", self.HISTORY_KEY, json.dumps(history))
            return True
        except Exception as e:
            logger.warning("Redis trade history save failed: %s", e)
            return False


# ─────────────────────────────────────────────
# Position state
# ─────────────────────────────────────────────

class PositionState:
    def __init__(self, filepath: str):
        self.filepath = filepath
        self.side: Optional[str] = None
        self.size: float = 0.0
        self.entry_price: float = 0.0
        self.entry_time: float = 0.0
        self.initial_entry: float = 0.0  # price of layer 1 (for DCA trigger calc)
        self.layers_filled: int = 0      # 0 = flat, 1 = layer 1 only, 2 = both layers
        self.layer_usd: float = 0.0      # margin per layer

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

    def open(self, side: str, size: float, price: float,
             initial_entry: Optional[float] = None, layers_filled: int = 1,
             layer_usd: Optional[float] = None) -> None:
        self.side = side
        self.size = size
        self.entry_price = price  # this is the AVG entry (for single layer = initial)
        self.entry_time = time.time()
        self.initial_entry = initial_entry if initial_entry is not None else price
        self.layers_filled = layers_filled
        self.layer_usd = layer_usd if layer_usd is not None else config.POSITION_SIZE_USD
        self._save()

    def add_layer(self, layer_price: float, layer_size: float) -> None:
        """Add DCA layer — recalculates avg entry price."""
        # Weighted average entry
        total_cost = self.entry_price * self.size + layer_price * layer_size
        self.size = self.size + layer_size
        self.entry_price = total_cost / self.size
        self.layers_filled += 1
        self._save()

    def close(self) -> dict:
        snapshot = {"side": self.side, "size": self.size,
                    "entry_price": self.entry_price, "entry_time": self.entry_time,
                    "initial_entry": self.initial_entry, "layers_filled": self.layers_filled}
        self.side = None
        self.size = 0.0
        self.entry_price = 0.0
        self.entry_time = 0.0
        self.initial_entry = 0.0
        self.layers_filled = 0
        self._save()
        return snapshot

    def _current_data(self) -> dict:
        return {"side": self.side, "size": self.size, "entry_price": self.entry_price,
                "entry_time": self.entry_time, "initial_entry": self.initial_entry,
                "layers_filled": self.layers_filled, "layer_usd": self.layer_usd}

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
        if not data:
            data = self._load_file()
        if data:
            self.side = data.get("side")
            self.size = float(data.get("size", 0))
            self.entry_price = float(data.get("entry_price", 0))
            self.entry_time = float(data.get("entry_time", 0))
            self.initial_entry = float(data.get("initial_entry", self.entry_price))
            self.layers_filled = int(data.get("layers_filled", 1 if self.side else 0))
            self.layer_usd = float(data.get("layer_usd", config.POSITION_SIZE_USD / 2))
            if self.side:
                logger.info("Restored: %s %.4f @ $%.2f (layers %d/2, init $%.2f)",
                            self.side, self.size, self.entry_price,
                            self.layers_filled, self.initial_entry)

    def _load_file(self) -> Optional[dict]:
        if not os.path.exists(self.filepath):
            return None
        try:
            with open(self.filepath) as f:
                return json.load(f)
        except Exception:
            return None


# ─────────────────────────────────────────────
# Candle fetching
# ─────────────────────────────────────────────

def fetch_candles(symbol: str, interval: str, limit: int = 100) -> list[dict]:
    return exchange.get_candles(symbol, interval, limit)


def _get_current_price(symbol: str) -> float:
    try:
        return exchange.get_current_price(symbol)
    except Exception:
        return 0.0


# ─────────────────────────────────────────────
# Entry / Exit handlers
# ─────────────────────────────────────────────

def handle_entry(result, state: PositionState, tracker: WeeklyTracker) -> None:
    """Enter layer 1 of DCA (50% of position size). Layer 2 added separately if price moves adverse."""
    sig = result["signal"]
    price = result["price"]

    logger.info("Entry L1: %s price=$%.2f StochK=%.1f MACD=%.3f RSI=%.1f",
                sig, price, result["stoch_k"], result["macd"], result["rsi"])

    # Layer 1 = 50% of total margin budget
    layer_margin = config.POSITION_SIZE_USD / config.DCA_LAYERS
    # TP/SL from initial entry (will recalc if layer 2 fills)
    tp_price = price * (1 + config.TP_PERCENT / 100) if sig == "LONG" else price * (1 - config.TP_PERCENT / 100)
    sl_price = price * (1 - config.SL_PERCENT / 100) if sig == "LONG" else price * (1 + config.SL_PERCENT / 100)
    # Layer 2 trigger price
    l2_trigger = (price * (1 - config.DCA_TRIGGER_PCT / 100) if sig == "LONG"
                  else price * (1 + config.DCA_TRIGGER_PCT / 100))

    # Telegram signal alert
    emoji = "🟢" if sig == "LONG" else "🔴"
    score = result.get("score", 0)
    gate = result.get("gate_count", 0)
    htf = result.get("htf_bias", 0)
    htf_str = "▲▲" if htf>=2 else "▲" if htf==1 else "▼▼" if htf<=-2 else "▼" if htf==-1 else "—"
    star = "⭐" if score >= 4.5 else ""

    msg = (
        f"{emoji} <b>{'LONG' if sig=='LONG' else 'SHORT'}</b> L1 Signal {star} {'🟡 DRY' if config.DRY_RUN else '🔴 LIVE'}\n\n"
        f"Symbol: <code>{config.SYMBOL}</code>\n"
        f"Price: <code>${price:,.2f}</code>\n\n"
        f"📊 <b>Composite Score: {score:.2f}</b>\n"
        f"Gate: {gate}/5 ✅\n"
        f"4H Bias: {htf_str} ({htf})\n"
        f"StochK: {result['stoch_k']:.1f} / D: {result['stoch_d']:.1f}\n"
        f"MACD: {result['macd']:.3f} | Hist: {result['macd_hist']:.3f}\n"
        f"RSI: {result['rsi']:.1f}\n\n"
        f"🎯 <b>DCA Layer 1/{config.DCA_LAYERS}</b>\n"
        f"Margin: <code>${layer_margin:.0f}</code> @ {config.LEVERAGE}x = <code>${layer_margin * config.LEVERAGE:.0f}</code>\n"
        f"L2 Trigger: <code>${l2_trigger:,.2f}</code> ({config.DCA_TRIGGER_PCT}% adverse)\n"
        f"TP: <code>${tp_price:,.2f}</code> (+{config.TP_PERCENT}%)\n"
        f"SL: <code>${sl_price:,.2f}</code> (-{config.SL_PERCENT}%)"
    )
    telegram._send(msg)

    if not config.DRY_RUN:
        try:
            order_result = exchange.market_open(
                config.SYMBOL, sig, layer_margin, config.LEVERAGE,
                tp_price=tp_price, sl_price=sl_price,
            )
            size = order_result["size"]
            fill_price = order_result.get("price", price)
            logger.info("Filled L1: %s %.4f %s @ $%.2f (notional $%.0f) TP=$%.2f SL=$%.2f L2@$%.2f",
                        sig, size, config.SYMBOL, fill_price,
                        order_result["notional"], tp_price, sl_price, l2_trigger)
        except Exception as exc:
            logger.error("Order L1 failed: %s", exc)
            telegram.send_error("market_open L1 failed", exc)
            return
    else:
        size = (layer_margin * config.LEVERAGE) / price
        fill_price = price

    state.open(sig, size, fill_price,
               initial_entry=fill_price, layers_filled=1, layer_usd=layer_margin)
    telegram.send_order_placed(sig, size, fill_price, config.LEVERAGE, 0, dry_run=config.DRY_RUN)


def handle_dca_layer(state: PositionState, current_price: float) -> bool:
    """Check if price has moved adverse to trigger layer 2 entry. Returns True if filled."""
    if state.layers_filled >= config.DCA_LAYERS:
        return False
    # Calculate trigger price based on initial entry
    trigger_pct = state.layers_filled * config.DCA_TRIGGER_PCT / 100
    if state.side == "LONG":
        trigger_px = state.initial_entry * (1 - trigger_pct)
        if current_price > trigger_px:
            return False
    else:  # SHORT
        trigger_px = state.initial_entry * (1 + trigger_pct)
        if current_price < trigger_px:
            return False

    # Trigger hit — add next layer
    next_layer = state.layers_filled + 1
    logger.info("DCA Layer %d triggered: price $%.2f %s trigger $%.2f",
                next_layer, current_price,
                "≤" if state.side == "LONG" else "≥", trigger_px)

    if not config.DRY_RUN:
        try:
            order_result = exchange.market_open(
                config.SYMBOL, state.side, state.layer_usd, config.LEVERAGE,
                tp_price=None, sl_price=None,  # will reset TP/SL after
            )
            layer_size = order_result["size"]
            fill_price = order_result.get("price", current_price)
        except Exception as exc:
            logger.error("L%d order failed: %s", next_layer, exc)
            telegram.send_error(f"L{next_layer} order failed", exc)
            return False
    else:
        layer_size = (state.layer_usd * config.LEVERAGE) / current_price
        fill_price = current_price

    # Update state — this recalculates avg entry
    state.add_layer(fill_price, layer_size)

    # Recalculate TP/SL from new avg entry
    tp_price = (state.entry_price * (1 + config.TP_PERCENT / 100) if state.side == "LONG"
                else state.entry_price * (1 - config.TP_PERCENT / 100))
    sl_price = (state.entry_price * (1 - config.SL_PERCENT / 100) if state.side == "LONG"
                else state.entry_price * (1 + config.SL_PERCENT / 100))

    # Cancel old TP/SL and set new ones with updated size
    if not config.DRY_RUN:
        try:
            exchange.cancel_all_orders(config.SYMBOL)
            exchange._set_tp_sl_orders(config.SYMBOL, state.side == "LONG", state.size,
                                        tp_price, sl_price)
            logger.info("TP/SL updated: TP=$%.2f SL=$%.2f (avg $%.2f, %d layers)",
                        tp_price, sl_price, state.entry_price, state.layers_filled)
        except Exception as exc:
            logger.warning("TP/SL update failed: %s", exc)

    # Telegram alert
    emoji = "🟢" if state.side == "LONG" else "🔴"
    msg = (
        f"{emoji} <b>DCA Layer {next_layer}/{config.DCA_LAYERS} Filled</b>\n\n"
        f"Fill: <code>${fill_price:,.2f}</code>\n"
        f"Layer margin: <code>${state.layer_usd:.0f}</code>\n\n"
        f"📊 <b>New Position</b>\n"
        f"Initial entry: <code>${state.initial_entry:,.2f}</code>\n"
        f"Avg entry: <code>${state.entry_price:,.2f}</code>\n"
        f"Total size: <code>{state.size:.4f}</code>\n\n"
        f"🎯 <b>New Levels</b> (from avg)\n"
        f"TP: <code>${tp_price:,.2f}</code>\n"
        f"SL: <code>${sl_price:,.2f}</code>"
    )
    telegram._send(msg)
    return True


def handle_exit(exit_price: float, exit_reason: str, state: PositionState,
                tracker: WeeklyTracker) -> None:
    snapshot = state.close()

    # P&L = price_diff × size (size is already the leveraged quantity in SOL)
    if snapshot["side"] == "LONG":
        pnl = (exit_price - snapshot["entry_price"]) * snapshot["size"]
    else:
        pnl = (snapshot["entry_price"] - exit_price) * snapshot["size"]

    logger.info("Exit: %s @ $%.2f (entry $%.2f) reason=%s pnl=$%.2f",
                snapshot["side"], exit_price, snapshot["entry_price"], exit_reason, pnl)

    if not config.DRY_RUN:
        # Check if position is still open (native TP/SL may have already closed it)
        try:
            open_pos = exchange.get_open_position(config.SYMBOL)
            if open_pos and abs(open_pos["size"]) > 0.001:
                exchange.market_close(config.SYMBOL)
                logger.info("Position closed via market_close")
            else:
                logger.info("Position already closed by native TP/SL")
            # Cancel any remaining trigger orders (TP/SL that didn't fire)
            exchange.cancel_all_orders(config.SYMBOL)
        except Exception as exc:
            logger.warning("Close/cancel error (non-fatal): %s", exc)

    tracker.add_trade(pnl, snapshot["side"], exit_reason)

    if state._redis:
        try:
            trade_record = {
                "id": f"{int(time.time())}_{snapshot['side'][:1]}",
                "symbol": config.SYMBOL,
                "side": snapshot["side"],
                "entry": snapshot["entry_price"],  # avg entry
                "initial_entry": snapshot.get("initial_entry", snapshot["entry_price"]),
                "layers_filled": snapshot.get("layers_filled", 1),
                "exit": exit_price,
                "size": snapshot["size"],
                "leverage": config.LEVERAGE,
                "pnl": round(pnl, 4),
                "roi": round(((exit_price - snapshot["entry_price"]) / snapshot["entry_price"] * 100)
                             if snapshot["side"] == "LONG"
                             else ((snapshot["entry_price"] - exit_price) / snapshot["entry_price"] * 100), 4),
                "reason": exit_reason,
                "ts": datetime.now(timezone.utc).isoformat(),
            }
            state._redis.append_trade(trade_record)
        except Exception as exc:
            logger.warning("Redis trade record failed (non-fatal): %s", exc)

    telegram.send_position_closed(
        snapshot["side"], snapshot["entry_price"], exit_price, snapshot["size"], pnl,
        exit_reason=exit_reason,
        weekly_pnl=tracker.total_pnl,
        weekly_trades=tracker.total_trades,
        weekly_wins=tracker.wins,
        dry_run=config.DRY_RUN,
    )


# ─────────────────────────────────────────────
# Main loop
# ─────────────────────────────────────────────

def run_loop(state: PositionState, tracker: WeeklyTracker, candles: list, candles_4h: list = None) -> Optional[dict]:
    summary = tracker.check_reset()
    if summary and summary["trades"] > 0:
        telegram.send_weekly_summary(
            summary["trades"], summary["wins"], summary["pnl"],
            summary["sortino"], summary["pf"],
            summary["best"], summary["worst"],
        )

    result = strategy.compute_signal(
        candles,
        candles_4h=candles_4h,
        min_score=config.MIN_SCORE,
        htf_bonus=config.HTF_BONUS,
        k_period=config.STOCH_K_PERIOD,
        d_period=config.STOCH_D_PERIOD,
        macd_fast=config.MACD_FAST,
        macd_slow=config.MACD_SLOW,
        macd_sig_period=config.MACD_SIGNAL,
        rsi_period=config.RSI_PERIOD,
        regime_buy_pct=config.REGIME_BUY_PCT,
        regime_sell_pct=config.REGIME_SELL_PCT,
        exhaust_rsi_low=config.EXHAUST_RSI_LOW,
        exhaust_rsi_high=config.EXHAUST_RSI_HIGH,
        exhaust_stk_low=config.EXHAUST_STK_LOW,
        exhaust_stk_high=config.EXHAUST_STK_HIGH,
    )

    block = result.get("block_reason", "")
    if block:
        logger.info("BLOCKED: %s (score=%.2f)", block, result["score"])

    logger.info("Signal=%s price=$%.2f score=%.2f gate=%d htf=%d StochK=%.1f/D=%.1f MACD=%.3f RSI=%.1f dist_hi=%.1f%% dist_lo=%.1f%% pos=%s",
                result["signal"], result["price"], result["score"],
                result["gate_count"], result["htf_bias"],
                result["stoch_k"], result["stoch_d"],
                result["macd"], result["rsi"],
                result.get("dist_from_high", 0), result.get("dist_from_low", 0),
                state.side or "FLAT")

    if state.is_open():
        latest = candles[-1]
        should_exit, reason, exit_price = strategy.check_exit(
            latest, state.side, state.entry_price,
            tp_pct=config.TP_PERCENT, sl_pct=config.SL_PERCENT,
        )
        if should_exit:
            handle_exit(exit_price, reason, state, tracker)
    else:
        if result["signal"] in ("LONG", "SHORT"):
            handle_entry(result, state, tracker)

    return result


def main() -> None:
    logger.info("=" * 60)
    logger.info("Composite Bot v4.2 starting (Composite + Regime + DCA)")
    logger.info("Symbol=%s  Interval=%s  Leverage=%dx  Size=$%.0f  DryRun=%s",
                config.SYMBOL, config.CANDLE_INTERVAL, config.LEVERAGE,
                config.POSITION_SIZE_USD, config.DRY_RUN)
    logger.info("TP=%.1f%%  SL=%.1f%%  MinScore=%.1f  HTF_Bonus=%.1f",
                config.TP_PERCENT, config.SL_PERCENT, config.MIN_SCORE, config.HTF_BONUS)
    logger.info("Regime: buy_block>%.0f%% sell_block<%.0f%%  Exhaust: RSI %.0f-%.0f / K %.0f-%.0f",
                config.REGIME_BUY_PCT, config.REGIME_SELL_PCT,
                config.EXHAUST_RSI_LOW, config.EXHAUST_RSI_HIGH,
                config.EXHAUST_STK_LOW, config.EXHAUST_STK_HIGH)
    if config.DCA_ENABLED:
        logger.info("DCA: %d layers, %.2f%% adverse trigger ($%.0f per layer)",
                    config.DCA_LAYERS, config.DCA_TRIGGER_PCT,
                    config.POSITION_SIZE_USD / config.DCA_LAYERS)
    else:
        logger.info("DCA: disabled (single entry)")
    logger.info("=" * 60)

    config.validate()

    state = PositionState(config.STATE_FILE)
    tracker = WeeklyTracker()

    # Sync position state from Hyperliquid (source of truth)
    if not config.DRY_RUN:
        try:
            hl_pos = exchange.get_open_position(config.SYMBOL)
            if hl_pos:
                logger.info("Hyperliquid position found: %s %.4f @ $%.2f (uPnL $%.2f)",
                            hl_pos["side"], hl_pos["size"], hl_pos["entry_price"], hl_pos["unrealized_pnl"])
                if not state.is_open():
                    state.open(hl_pos["side"], hl_pos["size"], hl_pos["entry_price"])
                    logger.info("State synced from Hyperliquid")
            else:
                if state.is_open():
                    logger.info("No Hyperliquid position but state shows open — clearing state")
                    state.close()
                logger.info("Position: FLAT")
            balance = exchange.get_balance()
            logger.info("Account balance: $%.2f", balance)
        except Exception as exc:
            logger.warning("Hyperliquid sync failed: %s (using local state)", exc)

    loop_count = 0
    start_time = time.time()

    while not _shutdown_requested:
        loop_count += 1

        last_result = None
        try:
            candles = fetch_candles(config.SYMBOL, config.CANDLE_INTERVAL, config.CANDLE_LOOKBACK)
            candles_4h = fetch_candles(config.SYMBOL, config.HTF_INTERVAL, 50)
            if len(candles) < 30:
                logger.warning("Only %d candles fetched, skipping", len(candles))
            else:
                last_result = run_loop(state, tracker, candles, candles_4h)
        except Exception as exc:
            logger.error("Loop error: %s", exc, exc_info=True)
            telegram.send_error("loop_error", exc)

        # Heartbeat with indicator values
        if loop_count % config.HEALTH_LOG_INTERVAL == 0:
            uptime = (time.time() - start_time) / 3600
            if state.is_open() and last_result:
                # Calculate uPnL
                current_price = last_result["price"]
                if state.side == "LONG":
                    upnl = ((current_price - state.entry_price) / state.entry_price) * 100
                    tp_price = state.entry_price * (1 + config.TP_PERCENT / 100)
                    sl_price = state.entry_price * (1 - config.SL_PERCENT / 100)
                else:
                    upnl = ((state.entry_price - current_price) / state.entry_price) * 100
                    tp_price = state.entry_price * (1 - config.TP_PERCENT / 100)
                    sl_price = state.entry_price * (1 + config.SL_PERCENT / 100)

                dist_tp = abs(current_price - tp_price) / current_price * 100
                dist_sl = abs(current_price - sl_price) / current_price * 100

                msg = (
                    f"💓 Heartbeat 🔴 {'DRY RUN' if config.DRY_RUN else 'LIVE'}\n\n"
                    f"Symbol: {config.SYMBOL}\n"
                    f"Price: ${current_price:.2f}\n"
                    f"Position: {state.side} @ ${state.entry_price:.2f}\n"
                    f"Layers: {state.layers_filled}/{config.DCA_LAYERS}"
                    + (f" (init ${state.initial_entry:.2f})" if state.layers_filled > 1 else "")
                    + f"\nuPnL: {upnl:+.2f}% ({upnl*config.LEVERAGE:+.1f}% leveraged)\n"
                    f"TP: ${tp_price:.2f} ({dist_tp:.2f}% away)\n"
                    f"SL: ${sl_price:.2f} ({dist_sl:.2f}% away)\n\n"
                    f"📊 Indicators\n"
                    f"StochK: {last_result['stoch_k']:.1f} / D: {last_result['stoch_d']:.1f}\n"
                    f"MACD: {last_result['macd']:.3f} | Hist: {last_result['macd_hist']:.3f}\n"
                    f"RSI: {last_result['rsi']:.1f}\n\n"
                    f"📈 Week: {tracker.total_trades} trades | ${tracker.total_pnl:+.2f}\n"
                    f"Loops: {loop_count} | Uptime: {uptime:.1f}h"
                )
                telegram._send(msg, parse_mode="")
            elif last_result:
                # Flat — show composite score status
                k = last_result["stoch_k"]
                d = last_result["stoch_d"]
                k_above_d = k > d
                macd = last_result["macd"]
                hist = last_result["macd_hist"]
                rsi_val = last_result["rsi"]
                score = last_result.get("score", 0)
                gate = last_result.get("gate_count", 0)
                htf = last_result.get("htf_bias", 0)
                dist_hi = last_result.get("dist_high", 0)
                dist_lo = last_result.get("dist_low", 0)

                htf_str = "▲▲" if htf>=2 else "▲" if htf==1 else "▼▼" if htf<=-2 else "▼" if htf==-1 else "—"
                regime_buy_ok = dist_hi <= config.REGIME_BUY_PCT
                regime_sell_ok = dist_lo >= config.REGIME_SELL_PCT
                cross_status = "K > D (watching DOWN cross)" if k_above_d else "K < D (watching UP cross)"

                msg = (
                    f"💓 Heartbeat {'🟡 DRY RUN' if config.DRY_RUN else '🔴 LIVE'}\n\n"
                    f"Symbol: {config.SYMBOL}\n"
                    f"Price: ${last_result['price']:.2f}\n"
                    f"Position: FLAT\n\n"
                    f"📊 Composite Strategy\n"
                    f"Score: {score:.2f} / {config.MIN_SCORE}\n"
                    f"Gate: {gate}/5\n"
                    f"4H Bias: {htf_str} ({htf:+d})\n"
                    f"Regime: {'B✓' if regime_buy_ok else 'B✗'} {'S✓' if regime_sell_ok else 'S✗'}\n"
                    f"Dist Hi: {dist_hi:.1f}% | Lo: {dist_lo:.1f}%\n\n"
                    f"📈 Indicators\n"
                    f"StochK: {k:.1f} / D: {d:.1f}\n"
                    f"{cross_status}\n"
                    f"MACD: {macd:.3f} | Hist: {hist:.3f}\n"
                    f"RSI: {rsi_val:.1f}\n\n"
                    f"⚙️ DCA: {config.DCA_LAYERS} layers @ {config.DCA_TRIGGER_PCT}% adverse\n"
                    f"⏳ Waiting for StochK cross + score ≥{config.MIN_SCORE}\n\n"
                    f"📈 Week: {tracker.total_trades} trades | ${tracker.total_pnl:+.2f}\n"
                    f"Loops: {loop_count} | Uptime: {uptime:.1f}h"
                )
                telegram._send(msg, parse_mode="")
            else:
                telegram.send_health(loop_count, uptime)

        if _shutdown_requested:
            break

        # Fast TP/SL check: while in position, check price every 5 min
        # instead of waiting for the full hourly loop
        if state.is_open():
            check_interval = 300  # 5 minutes
            while not _shutdown_requested and state.is_open():
                # Check how long until next candle close (+30s buffer)
                now = time.time()
                secs_into_hour = now % 3600
                secs_until_close = 3600 - secs_into_hour + 30  # 30s after the hour
                if secs_until_close > 3600:
                    secs_until_close -= 3600

                if secs_until_close <= check_interval:
                    # Close to candle close — wait for it and break to main loop
                    logger.debug("Candle close in %.0fs, waiting...", secs_until_close)
                    time.sleep(secs_until_close)
                    break

                # Not close to candle close — do a price check
                time.sleep(check_interval)
                try:
                    price_now = _get_current_price(config.SYMBOL)
                    if price_now <= 0:
                        continue

                    # DCA: check if layer 2 should trigger (before checking TP/SL)
                    if state.layers_filled < config.DCA_LAYERS:
                        if handle_dca_layer(state, price_now):
                            # Layer filled — continue monitoring
                            continue

                    fake_candle = {"high": price_now, "low": price_now, "close": price_now,
                                   "open": price_now, "volume": 0, "timestamp": 0}
                    should_exit, reason, exit_price = strategy.check_exit(
                        fake_candle, state.side, state.entry_price,
                        tp_pct=config.TP_PERCENT, sl_pct=config.SL_PERCENT,
                    )
                    if should_exit:
                        logger.info("Fast TP/SL check: %s at $%.2f", reason, price_now)
                        handle_exit(exit_price, reason, state, tracker)
                        break
                    else:
                        ep = state.entry_price
                        if state.side == "LONG":
                            upnl_pct = (price_now - ep) / ep * 100
                        else:
                            upnl_pct = (ep - price_now) / ep * 100
                        logger.debug("Price check: $%.2f uPnL=%.2f%% layers=%d/%d",
                                     price_now, upnl_pct, state.layers_filled, config.DCA_LAYERS)
                except Exception as exc:
                    logger.warning("Fast price check error: %s", exc)
        else:
            # FLAT — sleep until 30 seconds after the next candle close
            # For 1h: check at :00:30 of each hour
            # For 4h: check at 00:00:30, 04:00:30, 08:00:30, 12:00:30, 16:00:30, 20:00:30 UTC
            interval_secs = {"15m": 900, "30m": 1800, "1h": 3600, "4h": 14400, "1d": 86400}.get(
                config.CANDLE_INTERVAL, 3600
            )
            now = time.time()
            secs_into_interval = now % interval_secs
            sleep_secs = interval_secs - secs_into_interval + 30  # 30s after close
            if sleep_secs > interval_secs:
                sleep_secs -= interval_secs
            logger.info("Sleeping %.0fs (%.1fh) until next %s candle close",
                        sleep_secs, sleep_secs/3600, config.CANDLE_INTERVAL)
            time.sleep(sleep_secs)

    logger.info("Bot stopped after %d loops", loop_count)


if __name__ == "__main__":
    main()
