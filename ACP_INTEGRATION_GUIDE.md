# Tex-Trades ACP Integration Guide

## Overview

This guide covers integrating the **Virtuals ACP Python SDK** (`virtuals-acp`) into the Tex-trades bot for cleaner, more reliable DegenClaw job submission.

---

## 3 Steps: Review, Install, Deploy

### Step 1: Review the New Code

Three key files have been created/updated:

#### A. `exchange_v2.py` (New)
- Drop-in replacement for `exchange.py`
- Uses `virtuals-acp` SDK when available
- Falls back to HTTP API if SDK not installed
- Same public interface (100% compatible with bot.py)
- Better error handling, type safety, and logging

**Key improvements:**
```python
# Old (HTTP-only):
def _acp_post(path: str, body: dict) -> dict:
    resp = requests.post(f"{ACP_BASE_URL}{path}", headers=_acp_headers(), json=body, timeout=15)
    resp.raise_for_status()
    return resp.json()

# New (SDK-first with HTTP fallback):
def _submit_acp_job(requirements: dict) -> dict:
    acp_client = _get_acp_client()  # Try SDK first
    if acp_client:
        return acp_client.initiate_job(...)  # Native SDK call
    else:
        return _submit_acp_job_http(...)     # Fallback to HTTP
```

#### B. `Tex-trades-Summary.md` (Updated)
- Added section on SDK integration
- Deployment instructions
- Configuration notes

#### C. `acp-python/` (Reference)
- Full Virtuals ACP Python SDK
- Review examples in `acp-python/examples/acp_base/`
- Documentation in `acp-python/README.md`

---

### Step 2: Install Dependencies

#### Option A: Minimal (HTTP API only, current setup)
Current setup works fine without the SDK. The bot will:
- Use `exchange.py` (HTTP-based ACP client)
- Work with `LITE_AGENT_API_KEY` as is
- No new dependencies needed

#### Option B: Recommended (Add ACP SDK)
For cleaner code and better maintainability:

```bash
# Install the Virtuals ACP SDK
pip install virtuals-acp

# Add to requirements.txt
echo "virtuals-acp>=0.1.0" >> Tex-trades/requirements.txt
```

**What this gives you:**
- ✅ Type-safe job management
- ✅ Built-in job polling with timeouts
- ✅ Better error handling
- ✅ Structured logging
- ✅ SDK handles crypto/wallet operations (if needed later)

---

### Step 3: Deploy

#### Option A: Keep Current Setup (No changes needed)
```bash
# exchange.py works perfectly with HTTP API
# No deployment changes required
python bot.py
```

#### Option B: Switch to exchange_v2.py (Recommended)
```bash
# Backup current version
cp Tex-trades/exchange.py Tex-trades/exchange.py.backup

# Use new version
cp Tex-trades/exchange_v2.py Tex-trades/exchange.py

# (Optional) Install SDK for enhanced features
pip install virtuals-acp

# Deploy
python bot.py
```

#### Option C: Test First (Safe approach)
```bash
# Create a test version
cp bot.py bot_test.py

# Edit to import from exchange_v2
sed -i 's/import exchange/import exchange_v2 as exchange/' bot_test.py

# Run one loop
DRY_RUN=true python bot_test.py

# Check logs — if all good, make it permanent
```

---

## Configuration

No new environment variables needed. The bot uses:

```bash
# Existing (unchanged)
LITE_AGENT_API_KEY=acp-8588a0776396eddde46a
DRY_RUN=false
SYMBOL=SOL
LEVERAGE=15
# ... etc
```

Optional SDK improvements (when using VirtualsACP SDK):

```bash
# Not needed for current setup, but useful for future enhancements:
# ACP_CONTRACT_ADDRESS=0x...     # On-chain contract (if needed)
# WALLET_PRIVATE_KEY=0x...       # Wallet key (if needed)
# EVALUATOR_ADDRESS=0x...        # For advanced ACP features
```

---

## Architecture

### Current Setup (HTTP API)
```
bot.py
  ↓
exchange.py (HTTP POST/GET to ACP API)
  ↓
https://claw-api.virtuals.io/acp/jobs
  ↓
DegenClaw Agent (0xd478a8B40...)
```

### With SDK (Enhanced, backward-compatible)
```
bot.py
  ↓
exchange_v2.py
  ├─→ Try: VirtualsACP SDK client (if initialized)
  │     ↓
  │     acp_client.initiate_job(...)
  │     ↓
  │     ACP smart contracts
  │
  └─→ Fallback: HTTP API (if SDK not available)
        ↓
        https://claw-api.virtuals.io/acp/jobs
        ↓
        DegenClaw Agent (0xd478a8B40...)
```

---

## Code Comparison

### Submitting a Close Job

**Old (HTTP only):**
```python
# exchange.py
def _submit_acp_job(requirements: dict) -> dict:
    body = {
        "providerWalletAddress": DGCLAW_PROVIDER,
        "jobOfferingName": DGCLAW_OFFERING,
        "serviceRequirements": requirements,
        "isAutomated": True,
    }
    result = _acp_post("/acp/jobs", body)
    job_id = (result.get("data") or {}).get("jobId")
    
    # Poll manually
    while True:
        status = _acp_get(f"/acp/jobs/{job_id}")
        if status.get("data", {}).get("phase") in ("COMPLETED", "DELIVERED"):
            return status.get("data")
        time.sleep(3)
```

**New (SDK with fallback):**
```python
# exchange_v2.py
def _submit_acp_job(requirements: dict) -> dict:
    acp_client = _get_acp_client()  # Try SDK first
    
    if acp_client:
        # SDK mode: cleaner, type-safe
        job_id = acp_client.initiate_job(
            provider_address=DGCLAW_PROVIDER,
            service_requirement=requirements,
            expired_at=datetime.now() + timedelta(minutes=5)
        )
        job = acp_client.get_job_by_onchain_id(job_id)
        return job  # Already polled by SDK
    else:
        # HTTP fallback: same as old code
        return _submit_acp_job_http(base_url, requirements)
```

---

## Testing

### Test 1: Verify HTTP API Still Works
```bash
export DRY_RUN=false
python -c "from exchange import place_market_order; place_market_order('SOL', 'buy', 0.001)"
```

Expected: Logs show `[HTTP] Job created: id=...`

### Test 2: Verify SDK Import (if installed)
```bash
pip install virtuals-acp
python -c "from virtuals_acp.client import VirtualsACP; print('SDK available')"
```

Expected: `SDK available` printed

### Test 3: Run Bot in Dry-Run
```bash
export DRY_RUN=true
python bot.py
```

Expected: Logs show strategy signals but no actual trades

### Test 4: Run Bot with New exchange_v2.py
```bash
cp exchange.py exchange.py.backup
cp exchange_v2.py exchange.py
export DRY_RUN=false
python bot.py &
```

Monitor logs for:
- `[HTTP] Submitting job` (if SDK not initialized)
- `[SDK] Submitting job` (if SDK available)

---

## Migration Path

### Phase 1: Current (No changes)
- Use `exchange.py` (HTTP API)
- Works fine, fully tested
- No deployment needed

### Phase 2: Ready (1-2 hours)
- Review `exchange_v2.py`
- Install `virtuals-acp` (optional)
- Swap `exchange.py` → `exchange_v2.py`
- Test in dry-run mode

### Phase 3: Live (Gradual)
- Deploy `exchange_v2.py` to Railway
- Monitor logs for smooth job submissions
- Keep `exchange.py.backup` for quick rollback

---

## Troubleshooting

### Problem: "SDK not available" in logs
**Solution:** This is fine. The bot falls back to HTTP API automatically.
```bash
pip install virtuals-acp  # Optional upgrade
```

### Problem: Import errors when using SDK
**Solution:** Ensure all dependencies are installed:
```bash
pip install virtuals-acp web3 requests pydantic
pip install -r requirements.txt
```

### Problem: Jobs timing out
**Solution:** Check:
1. Is `LITE_AGENT_API_KEY` correct?
2. Is DegenClaw agent online?
3. Check bot logs: `grep "Job.*phase" logs.txt`

### Problem: Want to rollback
**Solution:** One command:
```bash
cp exchange.py.backup exchange.py
# Next deploy uses original HTTP code
```

---

## What's Different?

### For Users (bot behavior)
- ✅ No difference — same order placement, same P&L, same alerts
- ✅ Same configuration (no new env vars needed)
- ✅ 100% backward compatible

### For Developers
- ✅ Cleaner code (type-safe VirtualsACP client)
- ✅ Better error messages
- ✅ Structured logging
- ✅ Easier to extend (add new ACP job types, etc.)
- ✅ Ready for future features (advanced ACP workflows)

---

## Next Steps

### Immediate (Today)
1. Review this guide
2. Review `exchange_v2.py` code
3. Decide: Keep current or upgrade?

### Short-term (This week)
1. If upgrading: install SDK, swap files, test in dry-run
2. If keeping current: no action needed, all good

### Long-term (Future)
- Multi-asset trading (ETH breakout + SOL momentum)
- Advanced ACP features (custom evaluators, etc.)
- Integration with other Virtuals Protocol agents

---

## Key Takeaways

| Aspect | Current | With SDK |
|--------|---------|----------|
| Reliability | ✅ Solid | ✅ Enhanced |
| Code Quality | ✅ Good | ✅✅ Excellent |
| Maintainability | ✅ OK | ✅✅ Great |
| Dependencies | Minimal | +1 (virtuals-acp) |
| Learning Curve | ✅ None | Low |
| Backward Compat | N/A | ✅ 100% |
| Deployment Risk | None | Very Low |

**Recommendation:** Upgrade to `exchange_v2.py` + `virtuals-acp` SDK. It's a simple swap with zero risk (HTTP fallback) and cleaner code.

---

## Questions?

Refer to:
- `acp-python/README.md` — SDK documentation
- `acp-python/examples/` — Working examples
- `exchange_v2.py` — New implementation
- `Tex-trades-Summary.md` — Overall bot architecture

