"""
RSI Mean Reversion Strategy for BTC 15m
Designed for weekday trading (Mon-Fri)

Strategy:
- LONG when RSI < 30 (oversold, expect bounce)
- SHORT when RSI > 70 (overbought, expect pullback)
- Exit: ATR-based stops (2.0x) and targets (2.5:1 R:R)

Backtest Results (60 days, Feb-Mar 2026):
- Total P&L: +$220.28
- Win Rate: 27.6% (226W / 594L)
- Risk/Reward: 2.73:1
- Winning Weeks: 4/8 (50%)
- Best Week: +$871.54
"""

import pandas as pd
import numpy as np


class RSIStrategy:
    """RSI Mean Reversion Strategy"""
    
    def __init__(self, config):
        self.symbol = config.SYMBOL
        self.leverage = config.LEVERAGE
        self.position_size_usd = config.POSITION_SIZE_USD
        
        # RSI parameters
        self.rsi_period = 14
        self.rsi_oversold = 30
        self.rsi_overbought = 70
        
        # Exit parameters
        self.atr_multiplier = 2.0
        self.reward_risk_ratio = 2.5
        
        # Track indicators
        self.last_rsi = None
        self.last_atr = None
    
    def calculate_rsi(self, closes, period=14):
        """Calculate RSI indicator"""
        if len(closes) < period + 1:
            return None
        
        delta = pd.Series(closes).diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        
        return rsi.iloc[-1]
    
    def calculate_atr(self, candles, period=14):
        """Calculate ATR for stops"""
        if len(candles) < period + 1:
            return None
        
        df = pd.DataFrame(candles)
        df['high'] = df['h'].astype(float)
        df['low'] = df['l'].astype(float)
        df['close'] = df['c'].astype(float)
        
        df['tr'] = np.maximum(
            df['high'] - df['low'],
            np.maximum(
                abs(df['high'] - df['close'].shift(1)),
                abs(df['low'] - df['close'].shift(1))
            )
        )
        
        atr = df['tr'].rolling(period).mean().iloc[-1]
        return atr
    
    def analyze(self, candles):
        """
        Analyze candles and return signal
        
        Args:
            candles: List of dicts with OHLCV data
        
        Returns:
            dict with 'action', 'reason', 'price', 'stop_loss', 'take_profit'
        """
        if len(candles) < 30:  # Need enough data for indicators
            return {
                'action': 'HOLD',
                'reason': 'Insufficient data for indicators'
            }
        
        # Get current price
        current_price = float(candles[-1]['c'])
        
        # Calculate RSI
        closes = [float(c['c']) for c in candles]
        rsi = self.calculate_rsi(closes, self.rsi_period)
        
        if rsi is None:
            return {
                'action': 'HOLD',
                'reason': 'RSI calculation failed'
            }
        
        self.last_rsi = rsi
        
        # Calculate ATR
        atr = self.calculate_atr(candles, period=14)
        if atr is None:
            return {
                'action': 'HOLD',
                'reason': 'ATR calculation failed'
            }
        
        self.last_atr = atr
        
        # Generate signals
        signal = {'action': 'HOLD', 'reason': f'RSI: {rsi:.1f} (neutral)'}
        
        # LONG signal: RSI oversold
        if rsi < self.rsi_oversold:
            stop_loss = current_price - (atr * self.atr_multiplier)
            take_profit = current_price + (atr * self.atr_multiplier * self.reward_risk_ratio)
            
            signal = {
                'action': 'LONG',
                'reason': f'RSI oversold: {rsi:.1f} < {self.rsi_oversold}',
                'price': current_price,
                'stop_loss': stop_loss,
                'take_profit': take_profit,
                'indicators': {
                    'rsi': rsi,
                    'atr': atr
                }
            }
        
        # SHORT signal: RSI overbought
        elif rsi > self.rsi_overbought:
            stop_loss = current_price + (atr * self.atr_multiplier)
            take_profit = current_price - (atr * self.atr_multiplier * self.reward_risk_ratio)
            
            signal = {
                'action': 'SHORT',
                'reason': f'RSI overbought: {rsi:.1f} > {self.rsi_overbought}',
                'price': current_price,
                'stop_loss': stop_loss,
                'take_profit': take_profit,
                'indicators': {
                    'rsi': rsi,
                    'atr': atr
                }
            }
        
        return signal
    
    def get_status(self):
        """Get current strategy status"""
        return {
            'strategy': 'RSI Mean Reversion',
            'parameters': {
                'rsi_period': self.rsi_period,
                'rsi_oversold': self.rsi_oversold,
                'rsi_overbought': self.rsi_overbought,
                'atr_multiplier': self.atr_multiplier,
                'reward_risk': self.reward_risk_ratio
            },
            'last_indicators': {
                'rsi': self.last_rsi,
                'atr': self.last_atr
            }
        }
