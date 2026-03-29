# RSI Mean Reversion Strategy - Weekday Bot

## Strategy Overview

**Type:** Mean Reversion  
**Timeframe:** 15m candles  
**Asset:** BTC  
**Leverage:** 20x  
**Position Size:** $50 USD

### Entry Rules
- **LONG:** RSI < 30 (oversold, expect bounce)
- **SHORT:** RSI > 70 (overbought, expect pullback)

### Exit Rules
- **Stop Loss:** Entry ± (ATR × 2.0)
- **Take Profit:** Entry ± (ATR × 2.0 × 2.5) = 2.5:1 R:R

### Schedule
- **Active:** Monday-Friday (UTC)
- **Idle:** Saturday-Sunday

## Backtest Results (60 Days: Feb-Mar 2026)

| Metric | Value |
|--------|-------|
| **Total P&L** | +$220.28 |
| **Total Trades** | 820 |
| **Win Rate** | 27.6% (226W / 594L) |
| **Risk/Reward** | 2.73:1 |
| **Winning Weeks** | 4/8 (50%) |
| **Avg Weekly Return** | $27.54 |
| **Best Week** | +$871.54 |
| **Worst Week** | -$560.83 |

### Side Breakdown
- **LONGS (RSI < 30):** 436 trades, +$640.55
- **SHORTS (RSI > 70):** 384 trades, -$420.27

**Conclusion:** Buying dips works better than shorting rallies in this period.

## Deployment

### Railway Environment Variables

```bash
DRY_RUN=false
LITE_AGENT_API_KEY=acp-8588a0776396eddde46a
SYMBOL=BTC
LEVERAGE=20
POSITION_SIZE_USD=50
TELEGRAM_BOT_TOKEN=8695654300:AAHwMYkTRATBOhQDyKO_HcLvIvZfHFV9RPM
TELEGRAM_CHAT_ID=5006865849
STATE_FILE=position_state_rsi.json
```

### Railway Deploy

1. Create new Railway service from GitHub repo
2. Set `railway-rsi.json` as config
3. Add environment variables above
4. Deploy!

## Files

- `bot_rsi.py` - Main bot loop (15m candles, weekday check)
- `strategy_rsi.py` - RSI strategy implementation
- `railway-rsi.json` - Railway deployment config
- `config.py` - Shared configuration (imports)
- `telegram.py` - Telegram notifications

## Comparison to Other Strategies

| Strategy | P&L (60d) | Win Rate | Notes |
|----------|-----------|----------|-------|
| **RSI Mean Reversion** | **+$220.28** | **27.6%** | ✅ Winner |
| Momentum Following | -$69.39 | 28.0% | ❌ Loses in chop |
| Momentum Fading | +$45.91 | 27.7% | ✅ OK but weaker |
| MACD Cross (Weekend) | +$139.23 | 55.6% | ✅ Weekend only |

## Live Status

**Railway Project:** TBD  
**Service ID:** TBD  
**Status:** Ready to deploy  
**Last Updated:** March 29, 2026
