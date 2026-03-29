# Deploy Weekend MACD Bot to Railway

## Quick Deploy Steps

### 1. Open Railway Dashboard
Go to: https://railway.app/dashboard

### 2. Create New Project
1. Click **"New Project"**
2. Select **"Deploy from GitHub repo"**
3. Choose: `aidaonsui-collab/Tex-trades`
4. Name it: `tex-trades-weekend`

### 3. Configure Service

#### Start Command
In **Settings** → **Deploy** → **Start Command**:
```
python bot_weekend.py
```

#### Root Directory (if needed)
Leave blank or set to `/`

#### Watch Paths (optional)
```
bot_weekend.py
strategy_weekend.py
config.py
exchange.py
telegram.py
```

### 4. Environment Variables

Go to **Variables** tab and add these:

**Required:**
```
LITE_AGENT_API_KEY=acp-8588a0776396eddde46a
SYMBOL=SOL
CANDLE_INTERVAL=1h
LEVERAGE=15
POSITION_SIZE_USD=50
DRY_RUN=true
```

**Optional (for Telegram alerts):**
```
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

**Optional (for Redis persistence):**
```
UPSTASH_REDIS_REST_URL=your_redis_url
UPSTASH_REDIS_REST_TOKEN=your_redis_token
```

**Strategy Config (optional - uses defaults if not set):**
```
BREAKOUT_LOOKBACK=12
ROC_THRESHOLD=1.0
VOLUME_MULTIPLIER=1.0
TREND_EMA_PERIOD=50
ATR_MULTIPLIER=2.0
REWARD_RISK_RATIO=2.5
```

### 5. Deploy

1. Click **"Deploy"** button (top right)
2. Wait for build to complete (~2-3 minutes)
3. Check logs to confirm it started

### 6. Verify It's Running

In the **Deployments** tab, you should see:
```
WEEKEND MACD BOT starting
Strategy: MACD Cross (12,26,9) on SOL 1h
Runs ONLY on weekends (Sat-Sun UTC)
```

If it's **not a weekend**, you'll see:
```
Loop 1 — (NOT weekend - sleeping) Saturday 2026-03-28 UTC
```

The bot will check every hour and only trade on Sat-Sun.

---

## Testing (This Weekend!)

Since today is **Saturday March 28, 2026**, the bot should:
1. ✅ Start immediately
2. ✅ Fetch SOL 1h candles
3. ✅ Check MACD histogram
4. ✅ Log: "Loop 1 — (WEEKEND - TRADING ACTIVE)"

**Expected first signal:** Within 1-4 hours if MACD crosses zero.

---

## Monitoring

### Telegram Alerts (if configured)
You'll receive:
- 🚀 Startup message with strategy config
- 🟢/🔴 Signal alerts (LONG/SHORT)
- 📊 Position opened/closed
- 💰 Weekly performance summary

### Railway Logs
Watch for:
```
Signal=LONG price=$82.05 macd_hist=0.0045 trend=BULLISH pos=FLAT
Entry: LONG price=$82.05 size=0.0073 atr=1.85 lev=15x
```

---

## Troubleshooting

### Bot says "NOT weekend"
- Check system time is UTC
- Saturday = weekday 5, Sunday = weekday 6
- Bot should run Sat 00:00 - Sun 23:59 UTC

### "No candles" error
- Check `LITE_AGENT_API_KEY` is correct
- Hyperliquid API may be down (check status)

### "Redis save failed"
- Redis credentials wrong (safe to ignore if using local file)
- State saves to `position_state_weekend.json` as fallback

### No signals firing
- MACD needs 35+ candles to compute
- Signals only fire when histogram crosses zero
- Weekend markets may be quiet (0-2 signals per weekend is normal)

---

## Comparison with Main Bot

| | Main Bot | Weekend Bot |
|---|----------|-------------|
| **Service** | `tex-trades` | `tex-trades-weekend` |
| **Command** | `python bot.py` | `python bot_weekend.py` |
| **Strategy** | Momentum Breakout | MACD Cross |
| **Runs** | 24/7 | Weekends only |
| **State File** | `position_state.json` | `position_state_weekend.json` |

**Both can run simultaneously** — they use different state files.

---

## Expected Performance

Based on backtest (Jan 1 - Mar 28, 2026):

| Metric | Value |
|--------|-------|
| **Win Rate** | 55.6% |
| **Weekend PnL** | +$139.23 |
| **Trades** | 18 over 25 days |
| **Avg/Weekend** | ~$5.50 PnL |
| **Trades/Weekend** | 0.7 (1 every 1-2 weekends) |

---

## Next Steps

1. **This Weekend (Mar 28-29):** Watch logs for first signal
2. **Next Week:** Review performance, switch `DRY_RUN=false` if profitable
3. **Month 2:** Compare weekend bot vs main bot PnL

Good luck! 🚀
