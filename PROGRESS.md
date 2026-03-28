# Tex-trades — Strategy Progress

## Current Strategy: Momentum Breakout (v2)
**Deployed:** 2026-03-28 | **Status:** Live on Railway

### Overview
Complete strategy overhaul from VWAP Cross to Momentum Breakout, optimised for the Degen Claw weekly trading competition.

| Parameter | Old (v1) | New (v2) |
|-----------|----------|----------|
| Strategy | VWAP Cross + RSI | Momentum Breakout + EMA Trend |
| Symbol | BTC | SOL |
| Timeframe | 15m | 1h |
| Leverage | 10x | 15x |
| Win rate | 10.7% | 33% |
| R:R ratio | ~3.6x | 2.8x |

### Signal Logic
- **LONG:** Close breaks above 12-bar high + ROC > 1% + volume > 1x avg + price > EMA(50)
- **SHORT:** Close breaks below 12-bar low + ROC < -1% + volume > 1x avg + price < EMA(50)
- **Stop Loss:** ATR(14) × 2.0
- **Take Profit:** ATR(14) × 2.0 × 2.5 (2.5:1 R:R)

### Configuration
```
SYMBOL=SOL
CANDLE_INTERVAL=1h
LEVERAGE=15
POSITION_SIZE_USD=50
BREAKOUT_LOOKBACK=12
ROC_THRESHOLD=1.0
VOLUME_MULTIPLIER=1.0
TREND_EMA_PERIOD=50
ATR_MULTIPLIER=2.0
REWARD_RISK_RATIO=2.5
```

### Backtest Results (17 weekly windows, SOL 1h, 120 days)

**With EMA50 trend filter (deployed config):**
- Winning weeks: 12/17 (71%)
- Avg weekly return: +1.9%
- Avg Sortino: 3.62
- Avg trades/week: ~5
- Best week: +14.6%
- Worst week: -5.1%

**Trade breakdown (93 trades):**
- Win rate: 33% (31W / 62L)
- Avg win: $23.33 | Avg loss: $8.31
- R:R ratio: 2.8x
- Stop outs: 52 | Take profits: 28 | Signal exits: 13

### Leverage Analysis
Sortino stays constant (3.60) across all leverage levels. Return% scales linearly.
15x selected as optimal — +2.6% avg weekly return with -7.7% worst week (survivable).

| Leverage | Avg Return/wk | Avg Win | Avg Loss | Worst Week |
|----------|---------------|---------|----------|------------|
| 5x | +0.9% | $11.75 | -$4.19 | -2.6% |
| 10x | +1.7% | $23.50 | -$8.37 | -5.1% |
| **15x** | **+2.6%** | **$35.25** | **-$12.56** | **-7.7%** |
| 20x | +3.5% | $47.00 | -$16.74 | -10.3% |

### Competition: Degen Claw
- **URL:** https://degen.virtuals.io
- **Format:** Weekly seasons, leaderboard resets every Monday
- **Scoring:** Sortino Ratio (40%) + Return % (35%) + Profit Factor (25%)
- **Prize:** Top 3 agents get $100K USDC backing from Virtuals Protocol
- **ACP Wallet:** 0x033fcd4eB0D5a171b2435E39495Fba2b94be21E5

### Infrastructure
- **Deployment:** Railway (worker: python bot.py)
- **State persistence:** Upstash Redis (promoted-cockatoo-66665.upstash.io)
- **Redis key:** mombreak_bot:position
- **Alerts:** Telegram bot — hourly heartbeats, signal/order/close alerts, weekly summaries
- **Order execution:** DegenClaw ACP (agent 8654)

### Strategy Selection Process
Tested 7 strategy families across 3 assets (BTC, ETH, SOL) in weekly rolling windows:

1. **Hybrid VWAP Cross** — original strategy, improved with EMA trend + ATR stops
2. **Donchian Channel Breakout** — classic trend following
3. **MACD Momentum** — histogram flip with trend confirmation
4. **Bollinger Band Mean Reversion** — buy lower band, sell upper
5. **EMA Crossover** — with volume confirmation
6. **RSI Divergence** — momentum exhaustion reversal
7. **Momentum Breakout** — N-bar high/low break with ROC + volume ← WINNER

SOL + Momentum Breakout won due to SOL's higher volatility producing bigger breakout moves,
and the strategy's ability to stay out of choppy sideways markets.

### Future Considerations
- Add ETH as second asset for ~10 trades/week (EMA 9/21 ETH showed 5 strong weeks)
- Volume filter tuning if signals are too infrequent
- Lookback period adjustment for different market regimes
- Position sizing based on account equity rather than fixed $50

### Changelog
- **2026-03-28:** Strategy overhaul deployed (VWAP → MomBreak, BTC → SOL, 15m → 1h)
- **2026-03-28:** Leverage bumped from 10x to 15x
- **2026-03-28:** Heartbeat frequency changed from 4h to 1h
- **2026-03-28:** Closed legacy BTC long position via ACP ($2.04 profit)
