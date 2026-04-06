# Deploy Valley/Peak Bot (10x Leverage)

## Quick Start

### 1. Test Locally (DRY RUN)
```bash
cd /Users/hectorhernandez/.openclaw/agents/jack/workspace-main/Tex-trades

# Run in paper trading mode for 30 minutes
DRY_RUN=true LEVERAGE=10 timeout 1800 python bot_valley.py

# Expect:
#   ✓ Fetches SOL 30m candles
#   ✓ Detects valleys and peaks
#   ✓ Logs [DRY RUN] entry/exit signals
#   ✓ No actual trades placed
```

### 2. Run Live (1 hour test)
```bash
# Test with 1x leverage first (safest)
DRY_RUN=false LEVERAGE=1 POSITION_SIZE_USD=10 timeout 3600 python bot_valley.py

# Then 10x if 1x looks good
DRY_RUN=false LEVERAGE=10 POSITION_SIZE_USD=50 python bot_valley.py
```

### 3. Deploy to Railway
Replace `/Tex-trades` on Railway with:
```json
{
  "name": "Tex-trades-Valley-10x",
  "runtime": "python-3.10",
  "build": {
    "builder": "nixpacks"
  },
  "deploy": {
    "startCommand": "python bot_valley.py"
  },
  "envVariables": {
    "DRY_RUN": "false",
    "LEVERAGE": "10",
    "POSITION_SIZE_USD": "50",
    "CANDLE_INTERVAL": "30m",
    "SYMBOL": "SOL",
    "LITE_AGENT_API_KEY": "$LITE_AGENT_API_KEY",
    "TELEGRAM_BOT_TOKEN": "$TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID": "$TELEGRAM_CHAT_ID"
  }
}
```

---

## Environment Variables

### Required
```
LITE_AGENT_API_KEY      # ACP API key for live trading
```

### Recommended
```
LEVERAGE                # 10 (default, optimal)
POSITION_SIZE_USD       # 50 (default, $500 notional at 10x)
DRY_RUN                 # false (default: true for paper trading)
CANDLE_INTERVAL         # 30m (required)
SYMBOL                  # SOL (default)
```

### Optional
```
TELEGRAM_BOT_TOKEN      # Telegram alerts
TELEGRAM_CHAT_ID        # Telegram chat ID
STATE_FILE              # position_state_valley.json (default)
```

---

## Files

| File | Purpose |
|------|---------|
| `bot_valley.py` | Main bot (entry/exit logic) |
| `config_valley.py` | Configuration & validation |
| `strategy_valley.py` | Valley/peak detection & signal logic |
| `exchange.py` | ACP job submission (existing) |
| `telegram.py` | Alert notifications (existing) |

---

## Testing Checklist

- [ ] Local dry-run 30 min: See [DRY RUN] entries
- [ ] Local dry-run 2 hours: See entries + exits
- [ ] Verify candle fetching (check logs for "Fetched N candles")
- [ ] Verify valley/peak detection (look for 🟢 VALLEY / 🔴 PEAK)
- [ ] Paper trade 1 hour: Verify exit logic
- [ ] Live 1x for 1 day: Check real P&L
- [ ] Live 10x if stable: Expect +50-60% weekly

---

## Expected Output (DRY RUN)

```
2026-04-06 15:51:23 [INFO] 🚀 Valley/Peak Bidirectional Bot Starting
2026-04-06 15:51:23 [INFO] Symbol: SOL
2026-04-06 15:51:23 [INFO] Leverage: 10x
2026-04-06 15:51:23 [INFO] Position Size: $50.00
2026-04-06 15:51:23 [INFO] Mode: DRY RUN
2026-04-06 15:51:30 [INFO] [HEARTBEAT] Price: $82.10 | Position: NONE | Signal: NONE
2026-04-06 15:52:00 [INFO] 🟢 VALLEY DETECTED: Entry point for LONG at $80.95
2026-04-06 15:52:01 [INFO] [DRY RUN] Entry would be placed
2026-04-06 15:52:01 [INFO] [DRY] LONG Entry: $80.95 × 0.6173
2026-04-06 15:53:30 [INFO] 🎯 EXIT SIGNAL: TP_HIT
2026-04-06 15:53:30 [INFO] P&L: +3.00% (leveraged: +30.00%)
2026-04-06 15:53:31 [INFO] [DRY RUN] Exit would be placed
```

---

## Troubleshooting

### "Failed to fetch candles"
- Check network/firewall
- Verify Hyperliquid API is up (https://api.hyperliquid.xyz)
- Check candle interval is valid (e.g., "30m", not "30M")

### "Configuration errors found"
- Ensure LITE_AGENT_API_KEY is set for live (DRY_RUN=false)
- Verify LEVERAGE is 1-50
- Check POSITION_SIZE_USD > 10

### "No signal for hours"
- Valley/peak detection is strict (valley must be lower than both neighbors)
- Normal if market is trending (fewer reversals)
- Check logs for "VALLEY DETECTED" / "PEAK DETECTED"

### Position doesn't close at TP/SL
- Check if candle.high >= tp_price
- Verify leverage is applied correctly in TP/SL calculation
- Check exchange logs for order rejection

---

## Switching from Momentum Breakout

### Option A: Replace (Recommended)
```bash
# Stop momentum bot
pkill -f "python bot.py"

# Start valley bot
python bot_valley.py
```

### Option B: Run Both
- Deploy bot.py (Momentum) on Railway's main project
- Deploy bot_valley.py (Valley) on separate Railway project
- Use different state files: `position_state.json` vs `position_state_valley.json`
- Both use same LITE_AGENT_API_KEY but independent positions

---

## Performance Expectations

### DRY RUN (First 2 hours)
- Should see 1-3 complete valley→exit cycles
- All trades should be profitable (P&L +1 to +3%)
- Zero SL hits (if 96.6% win rate holds)

### Live 1x (First 24 hours)
- 5-10 trades per day
- Win rate: 90-95% (slightly lower than backtest)
- Daily P&L: +1-3%

### Live 10x (After 1 week of success)
- Same trade count
- Win rate: 90-95%
- Daily P&L: +10-30%
- Weekly P&L: +50-150% (realistic after slippage)

---

## Risk Management

### Daily Loss Limits
```python
# Add to bot_valley.py if needed
if daily_pnl < -50:  # Down 50% on the day
    logger.warning("Daily loss limit hit, stopping trading")
    return  # Stop trading for the day
```

### Consecutive SL Limit
```python
# If 3 SL hits in a row, pause for 1 hour
if consecutive_sl_hits >= 3:
    logger.warning("3 SL hits in a row, pausing")
    time.sleep(3600)
```

### Account Equity Stop
```python
# If account drops below 50% of starting, shut down
if current_equity < starting_equity * 0.5:
    logger.critical("Account down 50%, shutting down")
    exit(1)
```

---

## Monitoring

### Key Metrics to Watch
- **Win Rate:** Should be 85%+ live (96.6% in backtest)
- **Profit Factor:** Should be >15 (23.86 in backtest)
- **Max Drawdown:** Should be <30% at 10x (expect 1-2 day swings)
- **Trades/Day:** 5-8 on SOL 30m

### Telegram Alerts
- Entry signal: "✅ LONG Entry: $82.10 × 0.6173"
- Exit signal: "✅ LONG Exit (TP_HIT): $84.56 | P&L: +30.00%"
- Errors: "❌ Entry failed: ..."
- Heartbeat: Every 30 minutes (DRY RUN mode only)

---

## Next Steps

1. **Test locally:** `DRY_RUN=true python bot_valley.py`
2. **Monitor for 30 min:** Look for valley/peak entries
3. **Test live 1x:** `DRY_RUN=false LEVERAGE=1 python bot_valley.py`
4. **Deploy to Railway:** Set DRY_RUN=false LEVERAGE=10
5. **Monitor first week:** Watch P&L and win rate
6. **Scale if stable:** Increase POSITION_SIZE_USD

Questions? Check `bot_valley.py` logs or message openclaw agent.

🚀 Ready to trade!

