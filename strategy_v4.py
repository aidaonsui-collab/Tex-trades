"""
strategy_v4.py — Composite Signal Strategy (BCS-inspired)

4 layers: Gate → Score → Cooldown → HTF Confluence

Backtested 90d SOL 1h, no fees, Mon-Fri (DegenClaw):
  Score ≥4.0, TP 1.5% / SL 1.0%
  114 trades (8.9/week), 50% WR, +28.5% ROI, PF 1.50, Sortino 0.35

Entry:
  LONG:  StochK crosses above D + composite score ≥ threshold + gate passes
  SHORT: StochK crosses below D + composite score ≥ threshold + gate passes

Exit: TP +1.5% / SL -1.0%
"""

import logging
import math
from typing import Literal, Optional, TypedDict

logger = logging.getLogger(__name__)
Signal = Literal["LONG", "SHORT", "NONE"]

class Candle(TypedDict):
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float

class SignalResult(TypedDict):
    signal: Signal
    price: float
    score: float
    stoch_k: float
    stoch_d: float
    macd: float
    macd_hist: float
    rsi: float
    gate_count: int
    htf_bias: int

def _ema(values, period):
    if not values: return []
    out=[values[0]]; m=2.0/(period+1)
    for i in range(1,len(values)): out.append((values[i]-out[-1])*m+out[-1])
    return out

def _sma(values, period):
    out=[]
    for i in range(len(values)):
        if i<period-1: out.append(float('nan'))
        else: out.append(sum(values[i-period+1:i+1])/period)
    return out

def _rsi(closes, period=14):
    if len(closes)<2: return [50.0]*len(closes)
    gains=[]; losses=[]
    for i in range(1,len(closes)):
        d=closes[i]-closes[i-1]; gains.append(max(0,d)); losses.append(max(0,-d))
    if len(gains)<period: return [50.0]*len(closes)
    ag=sum(gains[:period])/period; al=sum(losses[:period])/period
    out=[50.0]*(period+1)
    out[-1]=100-(100/(1+ag/max(al,1e-10)))
    for i in range(period,len(gains)):
        ag=(ag*(period-1)+gains[i])/period; al=(al*(period-1)+losses[i])/period
        out.append(100-(100/(1+ag/max(al,1e-10))))
    return out

def _stochastic(candles, k_period=14, d_period=3):
    n=len(candles); k=[]
    for i in range(n):
        s=max(0,i-k_period+1)
        lo=min(c["low"] for c in candles[s:i+1]); hi=max(c["high"] for c in candles[s:i+1])
        k.append(((candles[i]["close"]-lo)/max(hi-lo,1e-10))*100)
    return k, _sma(k,d_period)

def _macd(closes, fast=12, slow=26, sig=9):
    ef=_ema(closes,fast); es=_ema(closes,slow)
    ml=[f-s for f,s in zip(ef,es)]; sl=_ema(ml,sig)
    return ml, sl, [m-s for m,s in zip(ml,sl)]

def _atr(candles, period=14):
    trs=[candles[0]["high"]-candles[0]["low"]]
    for i in range(1,len(candles)):
        h,l,pc=candles[i]["high"],candles[i]["low"],candles[i-1]["close"]
        trs.append(max(h-l,abs(h-pc),abs(l-pc)))
    return _sma(trs,period)

def _bb_pct(closes, period=20):
    out=[]
    for i in range(len(closes)):
        if i<period-1: out.append(0.5)
        else:
            w=closes[i-period+1:i+1]; m=sum(w)/period
            std=(sum((x-m)**2 for x in w)/period)**0.5
            out.append((closes[i]-(m-2*std))/(4*max(std,1e-10)) if std>1e-10 else 0.5)
    return out

def _mfi(candles, period=14):
    n=len(candles); tp=[(c["high"]+c["low"]+c["close"])/3 for c in candles]
    mf=[tp[i]*candles[i]["volume"] for i in range(n)]
    out=[50.0]*min(period,n)
    for i in range(period,n):
        pos=sum(mf[j] for j in range(i-period+1,i+1) if tp[j]>tp[j-1])
        neg=sum(mf[j] for j in range(i-period+1,i+1) if tp[j]<tp[j-1])
        out.append(100-(100/(1+pos/max(neg,1e-10))))
    return out

def _willr(candles, period=14):
    out=[]
    for i in range(len(candles)):
        s=max(0,i-period+1)
        hi=max(c["high"] for c in candles[s:i+1]); lo=min(c["low"] for c in candles[s:i+1])
        out.append(((hi-candles[i]["close"])/max(hi-lo,1e-10))*-100)
    return out

def _cci(candles, period=20):
    tp=[(c["high"]+c["low"]+c["close"])/3 for c in candles]
    out=[0.0]*min(period-1,len(tp))
    for i in range(period-1,len(tp)):
        w=tp[i-period+1:i+1]; m=sum(w)/period
        md=sum(abs(x-m) for x in w)/period
        out.append((tp[i]-m)/max(0.015*md,1e-10))
    return out

def _score_long(macd,hist,hist_prev,rsi,vol_ratio,bb,is_bull,lw,cci,mfi,willr,e8,e21):
    sc=1.5
    if macd>0: sc+=1.0
    if hist>0: sc+=0.5
    if hist>hist_prev: sc+=0.25
    if 30<rsi<50: sc+=0.75
    elif 50<rsi<65: sc+=0.5
    if vol_ratio>1.2: sc+=0.5
    if vol_ratio>1.5: sc+=0.25
    if bb<0.3: sc+=0.75
    elif bb<0.5: sc+=0.25
    if is_bull: sc+=0.5
    if lw>0.3: sc+=0.25
    if cci<-100: sc+=0.5
    if mfi<30: sc+=0.5
    if willr<-80: sc+=0.5
    if e8>e21: sc+=0.5
    return sc

def _score_short(macd,hist,rsi,vol_ratio,bb,is_bull,uw,cci,mfi,willr,e8,e21):
    sc=1.5
    if macd<0: sc+=1.0
    if hist<0: sc+=0.5
    if 50<rsi<70: sc+=0.75
    elif rsi>70: sc+=0.5
    if vol_ratio>1.2: sc+=0.5
    if vol_ratio>1.5: sc+=0.25
    if bb>0.7: sc+=0.75
    elif bb>0.5: sc+=0.25
    if not is_bull: sc+=0.5
    if uw>0.3: sc+=0.25
    if cci>100: sc+=0.5
    if mfi>70: sc+=0.5
    if willr>-20: sc+=0.5
    if e8<e21: sc+=0.5
    return sc

def compute_htf_bias(candles_4h):
    if not candles_4h or len(candles_4h)<30: return 0
    closes=[c["close"] for c in candles_4h[-30:]]
    e8=_ema(closes,8)[-1]; e21=_ema(closes,21)[-1]
    rsi=_rsi(closes,14)[-1]
    ml,_,_=_macd(closes,12,26,9); macd=ml[-1]
    sc=0
    if e8>e21: sc+=1
    else: sc-=1
    if rsi>55: sc+=1
    elif rsi<45: sc-=1
    if macd>0: sc+=1
    else: sc-=1
    return sc

def compute_signal(candles, candles_4h=None, min_score=4.0, htf_bonus=0.5,
                   k_period=14, d_period=3, macd_fast=12, macd_slow=26,
                   macd_sig_period=9, rsi_period=14, **kwargs) -> SignalResult:
    n=len(candles)
    if n<max(macd_slow,k_period,20)+5:
        return {"signal":"NONE","price":candles[-1]["close"],"score":0,
                "stoch_k":50,"stoch_d":50,"macd":0,"macd_hist":0,
                "rsi":50,"gate_count":0,"htf_bias":0}

    closes=[c["close"] for c in candles]; price=closes[-1]
    k_v,d_v=_stochastic(candles,k_period,d_period)
    ml,sl,hist=_macd(closes,macd_fast,macd_slow,macd_sig_period)
    rsi_v=_rsi(closes,rsi_period); atr_v=_atr(candles)
    bb=_bb_pct(closes); mfi_v=_mfi(candles); wr=_willr(candles); cci_v=_cci(candles)
    e8=_ema(closes,8); e21=_ema(closes,21)
    vol_sma=_sma([c["volume"] for c in candles],20)

    k_now,k_prev=k_v[-1],k_v[-2]; d_now=d_v[-1]; d_prev=d_v[-2] if d_v[-2]==d_v[-2] else d_now
    macd_now=ml[-1]; hist_now=hist[-1]; hist_prev=hist[-2] if len(hist)>1 else 0
    rsi_now=rsi_v[-1]; atr_pct=(atr_v[-1]/price*100) if atr_v[-1]==atr_v[-1] else 0
    bb_now=bb[-1]; vr=candles[-1]["volume"]/vol_sma[-1] if vol_sma[-1] and vol_sma[-1]==vol_sma[-1] and vol_sma[-1]>0 else 1.0
    is_bull=candles[-1]["close"]>candles[-1]["open"]
    lw=(min(candles[-1]["close"],candles[-1]["open"])-candles[-1]["low"])/max(price,1)*100
    uw=(candles[-1]["high"]-max(candles[-1]["close"],candles[-1]["open"]))/max(price,1)*100

    k_cross_up=k_now>d_now and k_prev<=d_prev
    k_cross_dn=k_now<d_now and k_prev>=d_prev
    htf_bias=compute_htf_bias(candles_4h) if candles_4h else 0

    # Regime filter: distance from 30-bar high/low
    highs=[c["high"] for c in candles[-30:]]
    lows=[c["low"] for c in candles[-30:]]
    high_30=max(highs) if highs else price
    low_30=min(lows) if lows else price
    dist_from_high=(high_30-price)/high_30*100 if high_30>0 else 0
    dist_from_low=(price-low_30)/low_30*100 if low_30>0 else 0

    # Get filter params from kwargs
    regime_buy_pct=kwargs.get("regime_buy_pct", 8)
    regime_sell_pct=kwargs.get("regime_sell_pct", 5)
    exhaust_rsi_low=kwargs.get("exhaust_rsi_low", 30)
    exhaust_rsi_high=kwargs.get("exhaust_rsi_high", 70)
    exhaust_stk_low=kwargs.get("exhaust_stk_low", 15)
    exhaust_stk_high=kwargs.get("exhaust_stk_high", 85)

    signal="NONE"; final_score=0.0; gate_count=0; block_reason=""

    if k_cross_up:
        raw=_score_long(macd_now,hist_now,hist_prev,rsi_now,vr,bb_now,is_bull,lw,cci_v[-1],mfi_v[-1],wr[-1],e8[-1],e21[-1])
        gate_count=sum([rsi_now<45,k_now<50,bb_now<0.5,vr>0.8,atr_pct>0.5])
        if gate_count>=2:
            final_score=raw+(htf_bonus if htf_bias>=2 else 0)
            if final_score>=min_score:
                # Regime: block buys if price dropped too far from recent high
                if dist_from_high>regime_buy_pct:
                    block_reason=f"REGIME_BUY: {dist_from_high:.1f}%>{regime_buy_pct}% below 30-bar high"
                    logger.info("LONG BLOCKED %s (sc=%.2f)", block_reason, final_score)
                # Exhaustion: block buys at extreme oversold (catching falling knife)
                elif rsi_now<exhaust_rsi_low and k_now<exhaust_stk_low:
                    block_reason=f"EXHAUSTION: RSI={rsi_now:.0f}<{exhaust_rsi_low} & K={k_now:.0f}<{exhaust_stk_low}"
                    logger.info("LONG BLOCKED %s (sc=%.2f)", block_reason, final_score)
                else:
                    signal="LONG"
                    logger.info("LONG sc=%.2f gate=%d K=%.1f MACD=%.3f RSI=%.1f dist_high=%.1f%%",
                                final_score,gate_count,k_now,macd_now,rsi_now,dist_from_high)

    elif k_cross_dn:
        raw=_score_short(macd_now,hist_now,rsi_now,vr,bb_now,is_bull,uw,cci_v[-1],mfi_v[-1],wr[-1],e8[-1],e21[-1])
        gate_count=sum([rsi_now>55,k_now>50,bb_now>0.5,vr>0.8,atr_pct>0.5])
        if gate_count>=2:
            final_score=raw+(htf_bonus if htf_bias<=-2 else 0)
            if final_score>=min_score:
                # Regime: block shorts if price is too close to recent low
                if dist_from_low<regime_sell_pct:
                    block_reason=f"REGIME_SELL: {dist_from_low:.1f}%<{regime_sell_pct}% above 30-bar low"
                    logger.info("SHORT BLOCKED %s (sc=%.2f)", block_reason, final_score)
                # Exhaustion: block shorts at extreme overbought
                elif rsi_now>exhaust_rsi_high and k_now>exhaust_stk_high:
                    block_reason=f"EXHAUSTION: RSI={rsi_now:.0f}>{exhaust_rsi_high} & K={k_now:.0f}>{exhaust_stk_high}"
                    logger.info("SHORT BLOCKED %s (sc=%.2f)", block_reason, final_score)
                else:
                    signal="SHORT"
                    logger.info("SHORT sc=%.2f gate=%d K=%.1f MACD=%.3f RSI=%.1f dist_low=%.1f%%",
                                final_score,gate_count,k_now,macd_now,rsi_now,dist_from_low)

    return {"signal":signal,"price":price,"score":final_score,"stoch_k":k_now,"stoch_d":d_now,
            "macd":macd_now,"macd_hist":hist_now,"rsi":rsi_now,"gate_count":gate_count,"htf_bias":htf_bias,
            "block_reason":block_reason,"dist_from_high":dist_from_high,"dist_from_low":dist_from_low}

def check_exit(current_candle, entry_side, entry_price, tp_pct=1.5, sl_pct=1.0, **kwargs):
    tp=tp_pct/100; sl=sl_pct/100
    if entry_side=="LONG":
        if current_candle["high"]>=entry_price*(1+tp): return True,"TP_HIT",entry_price*(1+tp)
        if current_candle["low"]<=entry_price*(1-sl): return True,"SL_HIT",entry_price*(1-sl)
    elif entry_side=="SHORT":
        if current_candle["low"]<=entry_price*(1-tp): return True,"TP_HIT",entry_price*(1-tp)
        if current_candle["high"]>=entry_price*(1+sl): return True,"SL_HIT",entry_price*(1+sl)
    return False,None,None

def calculate_size(price, size_usd, leverage):
    raw=(size_usd*leverage)/price
    return math.floor(raw*10_000)/10_000
