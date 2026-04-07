"""
exchange.py — Trading execution via DegenClaw ACP job system.

Responsibilities:
  - Fetch live OHLCV candle data via Hyperliquid public /info endpoint
  - Submit perp trade jobs via the DegenClaw ACP API
  - Poll job status until filled or timeout
  - Close open positions via the same ACP job channel
  - Exponential backoff on transient failures

All order-side logic respects DRY_RUN mode: in dry-run, order calls are
logged but never sent.

ACP API:
  Base URL : https://claw-api.virtuals.io
  Auth     : x-api-key: <LITE_AGENT_API_KEY>
  Provider : 0xd478a8B40372db16cA8045F28C6FE07228F3781A  (DegenClaw)
  Offering : perp_trade
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
# DegenClaw ACP constants
# ─────────────────────────────────────────────
ACP_BASE_URL       = "https://claw-api.virtuals.io"
DGCLAW_PROVIDER    = "0xd478a8B40372db16cA8045F28C6FE07228F3781A"
DGCLAW_OFFERING    = "perp_trade"
JOB_POLL_INTERVAL  = 3      # seconds between status polls
JOB_TIMEOUT        = 60     # seconds to wait for job completion


# ─────────────────────────────────────────────
# Retry / backoff helper
# ─────────────────────────────────────────────

def _with_backoff(fn, *args, label: str = "", **kwargs):
    """Call fn(*args, **kwargs) with exponential backoff on exception."""
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
# ACP HTTP helpers
# ─────────────────────────────────────────────

def _acp_headers() -> dict:
    return {
        "x-api-key": config.LITE_AGENT_API_KEY,
        "Content-Type": "application/json",
    }


def _acp_post(path: str, body: dict) -> dict:
    resp = requests.post(
        f"{ACP_BASE_URL}{path}",
        headers=_acp_headers(),
        json=body,
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def _acp_get(path: str) -> dict:
    resp = requests.get(
        f"{ACP_BASE_URL}{path}",
        headers=_acp_headers(),
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


# ─────────────────────────────────────────────
# Candle data (public Hyperliquid endpoint)
# ─────────────────────────────────────────────

def get_candles(symbol: str, interval: str, count: int) -> list[Candle]:
    """
    Fetch the most recent `count` OHLCV candles for `symbol` at `interval`.
    Uses the Hyperliquid public REST endpoint (no auth required).
    Returns candles ordered oldest → newest.
    """
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
    mapping = {"m": 60, "h": 3600, "d": 86400}
    unit = interval[-1]
    value = int(interval[:-1])
    return value * mapping.get(unit, 60)


# ─────────────────────────────────────────────
# Price / sizing helpers
# ─────────────────────────────────────────────

def get_current_price(symbol: str) -> float:
    """Fetch the latest mid price for `symbol` from the Hyperliquid info endpoint."""
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
    """Calculate position size in base asset units (floored to 4 d.p.)."""
    raw = (size_usd * leverage) / price
    return math.floor(raw * 10_000) / 10_000


def set_leverage(symbol: str, leverage: int) -> None:
    """No-op — leverage is passed in the ACP job requirements."""
    logger.info("[ACP] Leverage %dx for %s will be sent with the job request", leverage, symbol)


# ─────────────────────────────────────────────
# ACP job submission
# ─────────────────────────────────────────────

def _submit_acp_job(requirements: dict) -> dict:
    """
    Submit a perp_trade job to DegenClaw and wait for completion.
    Returns the final job status dict, or raises on timeout / failure.
    """
    # Create job
    body = {
        "providerWalletAddress": DGCLAW_PROVIDER,
        "jobOfferingName": DGCLAW_OFFERING,
        "serviceRequirements": requirements,
        "isAutomated": True,
    }
    logger.info("[ACP] Submitting job: %s", json.dumps(requirements))

    def _create():
        return _acp_post("/acp/jobs", body)

    result = _with_backoff(_create, label="acp_job_create")
    job_id = (result.get("data") or {}).get("jobId") or result.get("jobId")

    if not job_id:
        raise RuntimeError(f"ACP job create returned no jobId: {result}")

    logger.info("[ACP] Job created: id=%s", job_id)

    # Poll for completion
    deadline = time.time() + JOB_TIMEOUT
    while time.time() < deadline:
        time.sleep(JOB_POLL_INTERVAL)
        status_resp = _acp_get(f"/acp/jobs/{job_id}")
        job = (status_resp.get("data") or {})
        phase = job.get("phase", "UNKNOWN")
        logger.debug("[ACP] Job %s phase: %s", job_id, phase)

        if phase in ("COMPLETED", "DELIVERED", "DONE"):
            logger.info("[ACP] Job %s completed. Deliverable: %s", job_id, job.get("deliverable"))
            return job

        if phase in ("FAILED", "CANCELLED", "REJECTED", "EXPIRED"):
            raise RuntimeError(f"ACP job {job_id} ended with phase={phase}: {job}")

        if phase == "PENDING_PAYMENT":
            # Auto-approve payment
            logger.info("[ACP] Job %s awaiting payment approval — auto-approving", job_id)
            try:
                _acp_post(
                    f"/acp/providers/jobs/{job_id}/negotiation",
                    {"accept": True, "content": "auto-approved by trading bot"},
                )
            except Exception as e:
                logger.warning("[ACP] Auto-approve failed (non-fatal): %s", e)

    raise TimeoutError(f"ACP job {job_id} did not complete within {JOB_TIMEOUT}s")


# ─────────────────────────────────────────────
# Order interface (same API as before)
# ─────────────────────────────────────────────

def place_market_order(
    symbol: str,
    side: str,          # "buy" (long) or "sell" (short)
    size: float,
    reduce_only: bool = False,
) -> dict:
    """
    Place a market order via the DegenClaw ACP job system.

    In DRY_RUN mode logs the intended order and returns a synthetic response.
    In live mode, submits an ACP perp_trade job and waits for confirmation.
    """
    action = "close" if reduce_only else "open"
    acp_side = "long" if side.lower() == "buy" else "short"

    label = f"{'[DRY RUN] ' if config.DRY_RUN else '[ACP] '}place_market_order"
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

    requirements = {
        "action": action,
        "pair": symbol,
        "side": acp_side,
        "size": str(size),
        "leverage": config.LEVERAGE,
    }

    job = _submit_acp_job(requirements)
    return {"status": "ok", "acp_job": job}


def close_position(symbol: str, side: str, size: float) -> dict:
    """
    Close an existing position by submitting an ACP close job.
    `side` is the side of the OPEN position ("LONG" or "SHORT").
    """
    close_side = "sell" if side == "LONG" else "buy"
    logger.info("Closing %s position: %s %.4f %s", side, close_side, size, symbol)
    return place_market_order(symbol, close_side, size, reduce_only=True)


def set_tp_sl(symbol: str, take_profit: float, stop_loss: float) -> dict:
    """
    Set TP/SL on an open position via the DegenClaw perp_modify offering.
    This places native TP/SL orders on Hyperliquid — they execute instantly
    when price hits the level, no polling needed.
    """
    label = f"{'[DRY RUN] ' if config.DRY_RUN else '[ACP] '}set_tp_sl"
    logger.info("%s: %s TP=$%.2f SL=$%.2f", label, symbol, take_profit, stop_loss)

    if config.DRY_RUN:
        return {"status": "ok", "dry_run": True, "tp": take_profit, "sl": stop_loss}

    requirements = {
        "pair": symbol,
        "takeProfit": str(take_profit),
        "stopLoss": str(stop_loss),
    }

    # Use perp_modify offering (different from perp_trade)
    body = {
        "providerWalletAddress": DGCLAW_PROVIDER,
        "jobOfferingName": "perp_modify",
        "serviceRequirements": requirements,
        "isAutomated": True,
    }
    logger.info("[ACP] Submitting perp_modify: %s", json.dumps(requirements))

    def _create():
        return _acp_post("/acp/jobs", body)

    result = _with_backoff(_create, label="acp_perp_modify")
    job_id = (result.get("data") or {}).get("jobId") or result.get("jobId")
    if not job_id:
        raise RuntimeError(f"perp_modify job create returned no jobId: {result}")
    logger.info("[ACP] perp_modify job created: id=%s", job_id)

    # Poll for completion
    deadline = time.time() + JOB_TIMEOUT
    while time.time() < deadline:
        time.sleep(JOB_POLL_INTERVAL)
        status_resp = _acp_get(f"/acp/jobs/{job_id}")
        job = (status_resp.get("data") or {})
        phase = job.get("phase", "UNKNOWN")
        if phase in ("COMPLETED", "DELIVERED", "DONE"):
            logger.info("[ACP] perp_modify completed: TP=$%.2f SL=$%.2f", take_profit, stop_loss)
            return {"status": "ok", "acp_job": job}
        if phase in ("FAILED", "CANCELLED", "REJECTED", "EXPIRED"):
            raise RuntimeError(f"perp_modify job {job_id} failed: phase={phase}")
        if phase == "PENDING_PAYMENT":
            try:
                _acp_post(f"/acp/providers/jobs/{job_id}/negotiation",
                          {"accept": True, "content": "auto-approved"})
            except Exception:
                pass
    raise TimeoutError(f"perp_modify job {job_id} timed out")


def get_open_position(symbol: str) -> Optional[dict]:
    """
    Query the exchange for any currently open position.
    ACP does not expose a direct position query — returns None.
    State is managed by bot.py's persistent state store.
    """
    logger.debug("get_open_position: state managed locally, returning None")
    return None
