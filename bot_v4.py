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
sys.modules['config'] = config  # ensure exchange.py uses config_v4
import exchange
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

    def open(self, side: str, size: float, price: float) -> None:
        self.side = side
        self.size = size
        self.entry_price = price
        self.entry_time = time.time()
        self._save()

    def close(self) -> dict:
        snapshot = {"side": self.side, "size": self.size,
                    "entry_price": self.entry_price, "entry_time": self.entry_time}
        self.side = None
        self.size = 0.0
        self.entry_price = 0.0
        self.entry_time = 0.0
        self._save()
        return snapshot

    def _current_data(self) -> dict:
        return {"side": self.side, "size": self.size, "entry_price": self.entry_price,
                "entry_time": self.entry_time}

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
            if self.side:
                logger.info("Restored: %s %.4f @ $%.2f", self.side, self.size, self.entry_price)

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
    interval_ms = {"15m": 15, "30m": 30, "1h": 60, "4h": 240}
    mins = interval_ms.get(interval, 60)
    now = int(time.time() * 1000)
    start = now - (limit * mins * 60 * 1000)

    resp = requests.post(f"{config.HYPERLIQUID_API_URL}/info", json={
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


def _get_current_price(symbol: str) -> float:
    """Quick price fetch using latest 1m candle."""
    try:
        now = int(time.time() * 1000)
        start = now - (5 * 60 * 1000)
        resp = requests.post(f"{config.HYPERLIQUID_API_URL}/info", json={
            "type": "candleSnapshot",
            "req": {"coin": symbol, "interval": "1m", "startTime": start, "endTime": now}
        }, timeout=10)
        resp.raise_for_status()
        raw = resp.json()
        if raw:
            return float(raw[-1]["c"])
    except Exception:
        pass
    return 0.0


# ─────────────────────────────────────────────
# Entry / Exit handlers
# ─────────────────────────────────────────────

def handle_entry(result, state: PositionState, tracker: WeeklyTracker) -> None:
    sig = result["signal"]
    price = result["price"]

    size = strategy.calculate_size(price, config.POSITION_SIZE_USD, config.LEVERAGE)
    if size <= 0:
        logger.error("Zero size (price=%.2f)", price)
        return

    logger.info("Entry: %s price=$%.2f size=%.4f StochK=%.1f MACD=%.3f RSI=%.1f",
                sig, price, size, result["stoch_k"], result["macd"], result["rsi"])

    tp_price = price * (1 + config.TP_PERCENT / 100) if sig == "LONG" else price * (1 - config.TP_PERCENT / 100)
    sl_price = price * (1 - config.SL_PERCENT / 100) if sig == "LONG" else price * (1 + config.SL_PERCENT / 100)

    # Custom signal alert for composite strategy
    emoji = "🟢" if sig == "LONG" else "🔴"
    score = result.get("score", 0)
    gate = result.get("gate_count", 0)
    htf = result.get("htf_bias", 0)
    htf_str = "▲▲" if htf>=2 else "▲" if htf==1 else "▼▼" if htf<=-2 else "▼" if htf==-1 else "—"
    star = "⭐" if score >= 4.5 else ""

    msg = (
        f"{emoji} <b>{'LONG' if sig=='LONG' else 'SHORT'}</b> Signal {star} {'🟡 DRY' if config.DRY_RUN else '🔴 LIVE'}\n\n"
        f"Symbol: <code>{config.SYMBOL}</code>\n"
        f"Price: <code>${price:,.2f}</code>\n\n"
        f"📊 <b>Composite Score: {score:.2f}</b>\n"
        f"Gate: {gate}/5 ✅\n"
        f"4H Bias: {htf_str} ({htf})\n"
        f"StochK: {result['stoch_k']:.1f} / D: {result['stoch_d']:.1f}\n"
        f"MACD: {result['macd']:.3f} | Hist: {result['macd_hist']:.3f}\n"
        f"RSI: {result['rsi']:.1f}\n\n"
        f"🎯 <b>Levels</b>\n"
        f"TP: <code>${tp_price:,.2f}</code> (+{config.TP_PERCENT}%)\n"
        f"SL: <code>${sl_price:,.2f}</code> (-{config.SL_PERCENT}%)\n"
        f"Size: <code>{size:.4f}</code> @ {config.LEVERAGE}x"
    )
    telegram._send(msg)

    if not config.DRY_RUN:
        try:
            exchange.set_leverage(config.SYMBOL, config.LEVERAGE)
            order_side = "buy" if sig == "LONG" else "sell"
            exchange.place_market_order(config.SYMBOL, order_side, size)
        except Exception as exc:
            logger.error("Order failed: %s", exc)
            telegram.send_error("place_market_order failed", exc)
            return

        # Set native TP/SL on Hyperliquid via perp_modify
        try:
            exchange.set_tp_sl(config.SYMBOL, tp_price, sl_price)
            logger.info("Native TP/SL set: TP=$%.2f SL=$%.2f", tp_price, sl_price)
        except Exception as exc:
            logger.warning("perp_modify TP/SL failed (non-fatal, using software TP/SL): %s", exc)

    state.open(sig, size, price)
    telegram.send_order_placed(sig, size, price, config.LEVERAGE, 0, dry_run=config.DRY_RUN)


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
        try:
            exchange.close_position(config.SYMBOL, snapshot["side"], snapshot["size"])
        except Exception as exc:
            err_str = str(exc)
            if "No open position" in err_str or "REJECTED" in err_str:
                # Native TP/SL already closed the position on Hyperliquid — this is expected
                logger.info("Position already closed by native TP/SL (perp_modify). Recording trade.")
            else:
                logger.error("Close failed: %s", exc)
                telegram.send_error("close_position failed", exc)
                return

    tracker.add_trade(pnl, snapshot["side"], exit_reason)

    if state._redis:
        trade_record = {
            "id": f"{int(time.time())}_{snapshot['side'][:1]}",
            "symbol": config.SYMBOL,
            "side": snapshot["side"],
            "entry": snapshot["entry_price"],
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
    logger.info("Composite Bot v4.1 starting (regime + exhaustion filters)")
    logger.info("Symbol=%s  Interval=%s  Leverage=%dx  Size=$%.0f  DryRun=%s",
                config.SYMBOL, config.CANDLE_INTERVAL, config.LEVERAGE,
                config.POSITION_SIZE_USD, config.DRY_RUN)
    logger.info("TP=%.1f%%  SL=%.1f%%  MinScore=%.1f  HTF_Bonus=%.1f",
                config.TP_PERCENT, config.SL_PERCENT, config.MIN_SCORE, config.HTF_BONUS)
    logger.info("Regime: buy_block>%.0f%% sell_block<%.0f%%  Exhaust: RSI %.0f-%.0f / K %.0f-%.0f",
                config.REGIME_BUY_PCT, config.REGIME_SELL_PCT,
                config.EXHAUST_RSI_LOW, config.EXHAUST_RSI_HIGH,
                config.EXHAUST_STK_LOW, config.EXHAUST_STK_HIGH)
    logger.info("=" * 60)

    config.validate()

    state = PositionState(config.STATE_FILE)
    tracker = WeeklyTracker()

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
                    f"uPnL: {upnl:+.2f}%\n"
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
                # Flat — show how close we are to a signal
                k = last_result["stoch_k"]
                d = last_result["stoch_d"]
                k_above_d = k > d
                macd = last_result["macd"]
                hist = last_result["macd_hist"]
                rsi_val = last_result["rsi"]

                long_ready = "✅" if macd > 0 and hist > 0 else "❌"
                short_ready = "✅" if macd < 0 else "❌"
                cross_status = "K > D (watching for cross DOWN)" if k_above_d else "K < D (watching for cross UP)"

                msg = (
                    f"💓 Heartbeat {'🟡 DRY RUN' if config.DRY_RUN else '🔴 LIVE'}\n\n"
                    f"Symbol: {config.SYMBOL}\n"
                    f"Price: ${last_result['price']:.2f}\n"
                    f"Position: FLAT\n\n"
                    f"📊 Indicators\n"
                    f"StochK: {k:.1f} / D: {d:.1f}\n"
                    f"Stoch: {cross_status}\n"
                    f"MACD: {macd:.3f} | Hist: {hist:.3f}\n"
                    f"RSI: {rsi_val:.1f}\n\n"
                    f"🎯 Signal readiness\n"
                    f"{long_ready} LONG: MACD>0 & hist>0\n"
                    f"{short_ready} SHORT: MACD<0 & RSI<50\n"
                    f"⏳ Waiting for StochK/D cross...\n\n"
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
                        logger.debug("Price check: $%.2f uPnL=%.2f%%", price_now, upnl_pct)
                except Exception as exc:
                    logger.warning("Fast price check error: %s", exc)
        else:
            # FLAT — sleep until 30 seconds after the next hour mark
            now = time.time()
            secs_into_hour = now % 3600
            sleep_secs = 3600 - secs_into_hour + 30  # 30s after the hour
            if sleep_secs > 3600:
                sleep_secs -= 3600
            logger.info("Sleeping %.0fs until next candle close (:%02d:%02d → :00:30)",
                        sleep_secs, int(secs_into_hour // 60), int(secs_into_hour % 60))
            time.sleep(sleep_secs)

    logger.info("Bot stopped after %d loops", loop_count)


if __name__ == "__main__":
    main()
