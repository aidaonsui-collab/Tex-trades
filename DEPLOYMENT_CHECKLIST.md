# Tex-Trades SDK Deployment Checklist

## Pre-Deployment (5 min)

- [ ] Review `exchange_v2.py` code
  ```bash
  diff exchange.py exchange_v2.py | head -50
  ```

- [ ] Verify current bot is healthy
  ```bash
  tail -100 /path/to/bot/logs
  grep "ERROR\|FAILED" logs.txt
  ```

- [ ] Backup current exchange.py
  ```bash
  cp exchange.py exchange.py.backup
  cp bot.py bot.py.backup
  ```

---

## Installation (5 min)

### Option A: HTTP API Only (Current, No Changes)
```bash
# Everything already works
# No action needed
```

### Option B: Add SDK Support (Recommended)
```bash
# Install the SDK
pip install virtuals-acp

# Verify installation
python -c "from virtuals_acp import __version__; print(f'SDK v{__version__}')"

# Add to requirements.txt
echo "virtuals-acp>=0.1.0" >> requirements.txt

# Verify dependencies
pip list | grep -E "virtuals|web3|requests|pydantic"
```

---

## Deployment (10 min)

### Step 1: Switch to New Exchange Module
```bash
# Use the new implementation
cp exchange_v2.py exchange.py

# Verify it imports correctly
python -c "import exchange; print('✓ exchange module loads')"
```

### Step 2: Test in Dry-Run Mode
```bash
# Set dry-run mode
export DRY_RUN=true
export LOG_LEVEL=DEBUG

# Run one bot cycle
python bot.py

# Watch for successful completion:
# - ✓ Candles fetched
# - ✓ Signal computed
# - ✓ DRY RUN order logged (no actual trade)
# - ✓ Telegram alert sent

# Exit (Ctrl+C)
```

### Step 3: Test with Live Trading (Optional)
```bash
# Set live mode (requires LITE_AGENT_API_KEY)
export DRY_RUN=false

# Run single loop with verbose logging
export LOOP_INTERVAL_SECONDS=1  # Run fast for testing
timeout 120 python bot.py

# Watch logs for:
# - [HTTP] Submitting job (or [SDK] if SDK initialized)
# - Job created: id=...
# - Job completed / phase=COMPLETED

# Verify trade on Hyperliquid dashboard
```

### Step 4: Deploy to Railway
```bash
# Push to GitHub
git add exchange.py exchange_v2.py ACP_INTEGRATION_GUIDE.md
git commit -m "feat: integrate virtuals-acp SDK with HTTP fallback"
git push origin main

# Railway auto-deploys on push
# Monitor at: https://railway.app/project/[project-id]

# Check logs
railway logs
```

---

## Post-Deployment (5 min)

### Verify Bot is Running
```bash
# Check Railway deployment status
railway status

# Check live logs
railway logs -f

# Look for:
# ✓ "Loop 1", "Loop 2", etc. (bot ticking)
# ✓ Signal computations
# ✓ Order submissions
# ✗ No ERROR or FAILED messages
```

### Verify Trades are Executing
```bash
# Check Telegram alerts
# - Hourly heartbeat: ✓ Position FLAT or LONG/SHORT
# - Entry alerts: ✓ Shows price, ATR, SL/TP
# - Close alerts: ✓ Shows P&L, weekly stats

# Check DegenClaw/Hyperliquid dashboard
# - Any new trades this hour?
# - Check wallet 0x033fcd4eB0D5a171b2435E39495Fba2b94be21E5
```

### Rollback if Issues
```bash
# Revert to original
cp exchange.py.backup exchange.py
git commit -am "revert: restore original exchange.py"
git push origin main

# Railway auto-deploys
# Bot resumes with HTTP API in ~1-2 minutes
```

---

## Checklist Summary

| Phase | Task | Status |
|-------|------|--------|
| **Pre-Deployment** | Review code | [ ] |
| | Backup files | [ ] |
| | Check bot health | [ ] |
| **Installation** | Install SDK (optional) | [ ] |
| | Verify imports | [ ] |
| **Deployment** | Swap exchange.py | [ ] |
| | Test dry-run | [ ] |
| | Test live (optional) | [ ] |
| | Push to GitHub | [ ] |
| **Post-Deployment** | Monitor logs | [ ] |
| | Verify trades | [ ] |
| | Check Telegram alerts | [ ] |

---

## Quick Commands Reference

```bash
# View new module
cat exchange_v2.py | head -100

# Diff changes
diff -u exchange.py exchange_v2.py | less

# Install SDK
pip install virtuals-acp

# Test import
python -c "import exchange; import virtuals_acp; print('✓ All imports OK')"

# Run bot once (dry-run)
DRY_RUN=true python bot.py

# Run bot once (live)
DRY_RUN=false timeout 120 python bot.py

# Check Railway logs
railway logs -f --tail 50

# Rollback
cp exchange.py.backup exchange.py && git commit -am "rollback" && git push
```

---

## Timeline

- **Pre-Deployment:** 5 min
- **Installation:** 5 min (or 0 min if skipping SDK)
- **Deployment:** 10 min
- **Post-Deployment:** 5 min
- **Total:** ~25 min

---

## Risk Assessment

| Scenario | Likelihood | Impact | Mitigation |
|----------|------------|--------|-----------|
| Code fails to import | Very Low | Critical | Fallback to .backup, test dry-run first |
| Bot doesn't start | Very Low | Critical | HTTP API fallback works without SDK |
| Trades fail | Very Low | Medium | Rollback in 2 min with git push |
| SDK initialization error | Low | None | Falls back to HTTP API automatically |
| Hyperliquid API error | Medium | Low | Same retry/backoff logic as before |

**Overall Risk:** Very Low (100% backward compatible, HTTP fallback, easy rollback)

---

## Success Criteria

After deployment, verify:
- [ ] Bot starts without errors (`Loop 1`, `Loop 2`, etc. in logs)
- [ ] Signals computed every hour
- [ ] Hourly Telegram heartbeats received
- [ ] No ERROR or FAILED in logs (warnings OK)
- [ ] Trades execute if signal fires
- [ ] Position state persists across restarts

---

## Support

If issues arise:

1. **Check logs first**
   ```bash
   railway logs --tail 200 | grep -E "ERROR|Job|ACP"
   ```

2. **Rollback immediately**
   ```bash
   cp exchange.py.backup exchange.py
   git commit -am "rollback" && git push
   ```

3. **Review guide**
   - `ACP_INTEGRATION_GUIDE.md` — Full details
   - `exchange_v2.py` — Implementation
   - `acp-python/README.md` — SDK docs

4. **Test locally first**
   ```bash
   DRY_RUN=true python bot.py
   ```

---

## Notes

- **All changes are backward compatible** — original `exchange.py` logic is preserved in HTTP fallback
- **No new dependencies required** — SDK is optional, HTTP API works without it
- **Deployment is zero-risk** — Easy to rollback with git revert
- **Same order placement logic** — No changes to trading behavior, only implementation

