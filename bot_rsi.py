#!/usr/bin/env python3
"""
RSI Mean Reversion Trading Bot (Weekday Version)
Runs Monday-Friday, uses 15m BTC candles

Usage:
    python bot_rsi.py

Environment Variables:
    DRY_RUN - Set to "false" for live trading
    LITE_AGENT_API_KEY - ACP API key
    SYMBOL - Trading pair (default: BTC)
    LEVERAGE - Trading leverage (default: 20)
    POSITION_SIZE_USD - Position size in USD (default: 50)
    TELEGRAM_BOT_TOKEN - Optional Telegram notifications
    TELEGRAM_CHAT_ID - Optional Telegram chat ID
    STATE_FILE - State file name (default: position_state_rsi.json)
"""

import os
import sys
import time
import json
import requests
from datetime import datetime, timezone

# Import config and telegram
import config
from telegram import send_telegram

# Import RSI strategy
from strategy_rsi import RSIStrategy

# State file
STATE_FILE = os.getenv('STATE_FILE', 'position_state_rsi.json')

class TradingBot:
    def __init__(self):
        self.strategy = RSIStrategy(config)
        self.symbol = config.SYMBOL
        self.dry_run = config.DRY_RUN
        self.api_key = config.LITE_AGENT_API_KEY
        self.position = None
        
        # Load state
        self.load_state()
        
        print(f"🤖 RSI Mean Reversion Bot Started")
        print(f"Symbol: {self.symbol}")
        print(f"Leverage: {config.LEVERAGE}x")
        print(f"Position Size: ${config.POSITION_SIZE_USD}")
        print(f"Mode: {'DRY RUN' if self.dry_run else 'LIVE TRADING'}")
        print(f"Strategy: RSI < 30 (LONG) | RSI > 70 (SHORT)")
        print()
    
    def load_state(self):
        """Load position state from file"""
        try:
            with open(STATE_FILE, 'r') as f:
                state = json.load(f)
                self.position = state.get('position')
                print(f"✅ Loaded state: {self.position['side'] if self.position else 'No position'}")
        except FileNotFoundError:
            print("📝 No existing state file, starting fresh")
            self.position = None
    
    def save_state(self):
        """Save position state to file"""
        state = {'position': self.position}
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2)
    
    def is_weekday(self):
        """Check if current day is Monday-Friday (UTC)"""
        now = datetime.now(timezone.utc)
        return now.weekday() < 5  # 0=Monday, 4=Friday
    
    def fetch_candles(self, limit=100):
        """Fetch recent candles from Hyperliquid"""
        try:
            end_time = int(datetime.now().timestamp() * 1000)
            start_time = end_time - (limit * 15 * 60 * 1000)  # 15m candles
            
            response = requests.post(
                'https://api.hyperliquid.xyz/info',
                headers={'Content-Type': 'application/json'},
                json={
                    'type': 'candleSnapshot',
                    'req': {
                        'coin': self.symbol,
                        'interval': '15m',
                        'startTime': start_time,
                        'endTime': end_time
                    }
                },
                timeout=30
            )
            
            if response.status_code == 200:
                return response.json()
            else:
                print(f"❌ Candle fetch failed: {response.status_code}")
                return None
        
        except Exception as e:
            print(f"❌ Error fetching candles: {e}")
            return None
    
    def place_order(self, side, price, stop_loss, take_profit):
        """Place order via ACP"""
        if self.dry_run:
            print(f"[DRY RUN] Would place {side} order at ${price:,.2f}")
            print(f"          Stop: ${stop_loss:,.2f} | Target: ${take_profit:,.2f}")
            return True
        
        # Use ACP perp_trade offering
        try:
            # Format for ACP - size must be in USD (string format)
            payload = {
                'offering': 'perp_trade',
                'parameters': {
                    'action': 'open',
                    'pair': self.symbol,  # Changed from 'symbol' to 'pair'
                    'side': side.lower(),
                    'size': str(config.POSITION_SIZE_USD),  # Changed from size_usd, must be string
                    'leverage': config.LEVERAGE
                    # Note: stop_loss/take_profit not supported by perp_trade offering
                }
            }
            
            response = requests.post(
                'https://api.virtuals.io/acp/v1/jobs',
                headers={
                    'Authorization': f'Bearer {self.api_key}',
                    'Content-Type': 'application/json'
                },
                json=payload,
                timeout=30
            )
            
            if response.status_code == 200:
                result = response.json()
                print(f"✅ Order placed: {result}")
                return True
            else:
                print(f"❌ Order failed: {response.text}")
                return False
        
        except Exception as e:
            print(f"❌ Order error: {e}")
            return False
    
    def check_position(self):
        """Check if position hit stop/target"""
        if not self.position:
            return
        
        candles = self.fetch_candles(limit=5)
        if not candles:
            return
        
        current_price = float(candles[-1]['c'])
        side = self.position['side']
        stop_loss = self.position['stop_loss']
        take_profit = self.position['take_profit']
        
        # Check exits
        hit_stop = False
        hit_target = False
        
        if side == 'LONG':
            if current_price <= stop_loss:
                hit_stop = True
            elif current_price >= take_profit:
                hit_target = True
        else:  # SHORT
            if current_price >= stop_loss:
                hit_stop = True
            elif current_price <= take_profit:
                hit_target = True
        
        # Handle exit
        if hit_stop or hit_target:
            exit_type = 'stop' if hit_stop else 'target'
            entry_price = self.position['entry_price']
            
            # Calculate P&L
            notional = config.POSITION_SIZE_USD * config.LEVERAGE
            if side == 'LONG':
                pnl = ((current_price - entry_price) / entry_price) * notional
            else:
                pnl = ((entry_price - current_price) / entry_price) * notional
            
            msg = f"🔔 Position closed ({exit_type})\n"
            msg += f"Side: {side}\n"
            msg += f"Entry: ${entry_price:,.2f}\n"
            msg += f"Exit: ${current_price:,.2f}\n"
            msg += f"P&L: ${pnl:+.2f}"
            
            print(msg)
            send_telegram(msg)
            
            # Clear position
            self.position = None
            self.save_state()
    
    def run_once(self):
        """Single bot iteration"""
        # Check if weekday
        if not self.is_weekday():
            return
        
        # Check existing position
        if self.position:
            self.check_position()
            return
        
        # Fetch candles
        candles = self.fetch_candles(limit=100)
        if not candles:
            return
        
        # Get signal
        signal = self.strategy.analyze(candles)
        
        # Log status
        current_price = float(candles[-1]['c'])
        status = self.strategy.get_status()
        rsi = status['last_indicators'].get('rsi', 0)
        atr = status['last_indicators'].get('atr', 0)
        
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] "
              f"{self.symbol} ${current_price:,.2f} | "
              f"RSI: {rsi:.1f} | ATR: ${atr:.2f} | "
              f"{signal['action']}")
        
        # Execute signal
        if signal['action'] in ['LONG', 'SHORT']:
            print(f"📊 Signal: {signal['reason']}")
            
            if self.place_order(
                side=signal['action'],
                price=signal['price'],
                stop_loss=signal['stop_loss'],
                take_profit=signal['take_profit']
            ):
                # Save position
                self.position = {
                    'side': signal['action'],
                    'entry_price': signal['price'],
                    'stop_loss': signal['stop_loss'],
                    'take_profit': signal['take_profit'],
                    'entry_time': datetime.now().isoformat(),
                    'indicators': signal['indicators']
                }
                self.save_state()
                
                # Notify
                msg = f"🚀 Opened {signal['action']} position\n"
                msg += f"Entry: ${signal['price']:,.2f}\n"
                msg += f"Stop: ${signal['stop_loss']:,.2f}\n"
                msg += f"Target: ${signal['take_profit']:,.2f}\n"
                msg += f"RSI: {rsi:.1f}"
                
                print(msg)
                send_telegram(msg)
    
    def run(self):
        """Main bot loop"""
        iteration = 0
        
        while True:
            try:
                self.run_once()
                
                # Hourly heartbeat
                iteration += 1
                if iteration % 4 == 0:  # Every 4 iterations (1 hour for 15m candles)
                    status = self.strategy.get_status()
                    msg = f"💓 RSI Bot Heartbeat\n"
                    msg += f"Time: {datetime.now().strftime('%H:%M UTC')}\n"
                    msg += f"Position: {self.position['side'] if self.position else 'None'}\n"
                    msg += f"RSI: {status['last_indicators'].get('rsi', 0):.1f}\n"
                    msg += f"Mode: {'DRY RUN' if self.dry_run else 'LIVE'}"
                    
                    send_telegram(msg)
                
                # Sleep 15 minutes
                time.sleep(15 * 60)
            
            except KeyboardInterrupt:
                print("\n👋 Bot stopped by user")
                sys.exit(0)
            
            except Exception as e:
                print(f"❌ Error in main loop: {e}")
                time.sleep(60)


if __name__ == '__main__':
    bot = TradingBot()
    bot.run()
