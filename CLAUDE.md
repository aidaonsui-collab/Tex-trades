# Tex-Trades — Claude Code Memory

## Project Overview
SOL trading bot for the **DegenClaw competition** hosted by Virtuals.
Trades SOL/USDT on Hyperliquid via the Lite Agent ACP API.

## Active Strategies

### 1. Pivot Point Strategy (`strategy_pivot.py`) — PRIMARY
- **Timeframe**: 30m primary, 1h confirmation
- **Target**: 3% TP / 1.5% SL / No leverage / 2:1 R:R
- **Days**: Monday-Friday only
- **Signal**: Multi-signal confidence scoring (threshold >= 5/9)
- **Components**:
  - Triple EMA (8/21/55) — Weight 3 (best performer: 100% WR on 30m)
  - EMA Cross (9/21) — Weight 2 (71.4% WR, Sortino 5.33 on 1h)
  - RSI(7) Momentum Filter — Weight 2
  - ADX(14) Trend Strength — Weight 1 (bonus)
  - VWAP Alignment — Weight 1 (bonus)

### Backtest Results (March 20 - April 5, 2026, Mon-Fri)
| Strategy | TF | Win% | PnL% | Trades | Sortino |
|---|---|---|---|---|---|
| Triple EMA (8/21/55) | 30m | 100% | +12.00% | 4 | 3.00 |
| Triple EMA (9/21/50) | 30m | 100% | +12.00% | 4 | 3.00 |
| EMA Cross (9/21) | 1h | 71.4% | +12.64% | 7 | 5.33 |
| EMA Cross (8/21) | 1h | 71.4% | +13.22% | 7 | 2.18 |
| EMA+RSI (5/13, RSI7) | 1h | 58.3% | +15.42% | 12 | 1.96 |
| ADX Trend (14, 12/26) | 1h | 100% | +6.00% | 2 | 3.00 |

### 2. Momentum Breakout (`strategy.py`) — LEGACY
- 1h timeframe, EMA50 trend filter, ATR-based stops
- 33% win rate, 2.8x R:R, 71% winning weeks

### 3. Weekend MACD (`strategy_weekend.py`) — WEEKENDS
- MACD(12,26,9) histogram cross, 55.6% win rate

### 4. RSI Mean Reversion (`strategy_rsi.py`) — BTC 15m
- RSI(14) oversold/overbought, ATR stops

## Key SOL Technical Levels (April 2026)
- **Resistance**: $92.34, $95, $100, $105 (R1), $125 (R2)
- **Support**: $86.66, $86 (pivot), $80 (psychological), $66 (S1)
- **Current trend**: Bearish (H&S breakdown confirmed March 27)
- **ATR daily**: ~$3-5

## Architecture
- `bot.py` — Main trading loop (Hyperliquid)
- `config.py` — Centralized configuration
- `exchange.py` / `exchange_v2.py` — Hyperliquid API wrapper
- `strategy_pivot.py` — NEW pivot strategy (primary)
- `backtest_sol_pivot.py` — Backtesting engine (200+ combos)
- `generate_sol_data.py` — Data generation from known anchors
- `telegram.py` — Telegram notifications

## Backtesting
Run `python backtest_sol_pivot.py` to test all strategies.
- Tests 18 strategies x multiple params x 3 timeframes = 200+ combinations
- Falls back to generated data if Binance API is unavailable
- Outputs ranked results with score = 2*PnL + 0.5*WinRate + 3*PF + 4*Sortino - 1.5*MaxDD

## Competition Notes
- DegenClaw scores: Sortino + Return% + Profit Factor
- Weekday-only trading (Mon-Fri)
- No leverage for the pivot strategy (pure swing)
- SOL preferred over BTC/ETH for breakout/trend strategies
