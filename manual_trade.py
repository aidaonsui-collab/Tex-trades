"""
manual_trade.py — One-shot manual trade submission to DegenClaw ACP.
"""

import json
import logging
import math
import os
import time
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ── Config ──
LITE_AGENT_API_KEY = os.getenv("LITE_AGENT_API_KEY", "")
HYPERLIQUID_API_URL = "https://api.hyperliquid.xyz"
ACP_BASE_URL = "https://claw-api.virtuals.io"
DGCLAW_PROVIDER = "0xd478a8B40372db16cA8045F28C6FE07228F3781A"
DGCLAW_OFFERING = "perp_trade"

# ── Trade parameters ──
SYMBOL = "SOL"
SIDE = "long"       # "long" or "short"
SIZE_USD = 100      # notional in USDC
LEVERAGE = 15

JOB_POLL_INTERVAL = 3
JOB_TIMEOUT = 60


def acp_headers():
    return {
        "x-api-key": LITE_AGENT_API_KEY,
        "Content-Type": "application/json",
    }


def get_current_price(symbol):
    resp = requests.post(
        f"{HYPERLIQUID_API_URL}/info",
        json={"type": "allMids"},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    return float(data[symbol])


def submit_trade():
    # Get current price for logging
    price = get_current_price(SYMBOL)
    raw_size = (SIZE_USD * LEVERAGE) / price
    size = math.floor(raw_size * 10_000) / 10_000
    logger.info("Current %s price: $%.2f", SYMBOL, price)
    logger.info("Opening LONG %s | Size: %.4f ($%d x %dx) | Leverage: %dx", SYMBOL, size, SIZE_USD, LEVERAGE, LEVERAGE)

    requirements = {
        "action": "open",
        "pair": SYMBOL,
        "side": SIDE,
        "size": str(size),
        "leverage": LEVERAGE,
    }

    body = {
        "providerWalletAddress": DGCLAW_PROVIDER,
        "jobOfferingName": DGCLAW_OFFERING,
        "serviceRequirements": requirements,
        "isAutomated": True,
    }

    logger.info("Submitting ACP job: %s", json.dumps(requirements))
    resp = requests.post(
        f"{ACP_BASE_URL}/acp/jobs",
        headers=acp_headers(),
        json=body,
        timeout=15,
    )
    resp.raise_for_status()
    result = resp.json()
    job_id = (result.get("data") or {}).get("jobId") or result.get("jobId")

    if not job_id:
        raise RuntimeError(f"No jobId returned: {result}")

    logger.info("Job created: %s", job_id)

    # Poll for completion
    deadline = time.time() + JOB_TIMEOUT
    while time.time() < deadline:
        time.sleep(JOB_POLL_INTERVAL)
        status_resp = requests.get(
            f"{ACP_BASE_URL}/acp/jobs/{job_id}",
            headers=acp_headers(),
            timeout=15,
        )
        status_resp.raise_for_status()
        job = status_resp.json().get("data") or {}
        phase = job.get("phase", "UNKNOWN")
        logger.info("Job %s phase: %s", job_id, phase)

        if phase in ("COMPLETED", "DELIVERED", "DONE"):
            logger.info("Trade FILLED! Deliverable: %s", job.get("deliverable"))
            return job

        if phase in ("FAILED", "CANCELLED", "REJECTED", "EXPIRED"):
            raise RuntimeError(f"Job ended with phase={phase}: {job}")

        if phase == "PENDING_PAYMENT":
            logger.info("Auto-approving payment...")
            try:
                requests.post(
                    f"{ACP_BASE_URL}/acp/providers/jobs/{job_id}/negotiation",
                    headers=acp_headers(),
                    json={"accept": True, "content": "manual trade approval"},
                    timeout=15,
                )
            except Exception as e:
                logger.warning("Auto-approve failed (non-fatal): %s", e)

    raise TimeoutError(f"Job {job_id} did not complete within {JOB_TIMEOUT}s")


if __name__ == "__main__":
    try:
        result = submit_trade()
        print("\n✅ Trade submitted successfully!")
        print(json.dumps(result, indent=2, default=str))
    except Exception as e:
        print(f"\n❌ Trade failed: {e}")
