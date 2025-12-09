import pandas as pd
import numpy as np
from typing import List
from models.types import Candle

def calculate_vwap(candles: List[Candle]) -> List[float]:
    """
    Calculate Session VWAP.
    VWAP = Cumulative(Volume * TypicalPrice) / Cumulative(Volume)
    Typical Price = (High + Low + Close) / 3
    
    Returns a list of VWAP values matching the length of candles.
    """
    if not candles:
        return []
    
    df = pd.DataFrame([vars(c) for c in candles])
    df['typical_price'] = (df['high'] + df['low'] + df['close']) / 3
    df['pv'] = df['typical_price'] * df['volume']
    
    # Ideally we reset VWAP at session start, but for MVP we run it rolling from start of history
    df['cum_pv'] = df['pv'].cumsum()
    df['cum_vol'] = df['volume'].cumsum()
    
    df['vwap'] = df['cum_pv'] / df['cum_vol']
    return df['vwap'].fillna(0).tolist()

def calculate_atr(candles: List[Candle], period: int = 14) -> List[float]:
    """
    Calculate Average True Range (ATR).
    """
    if len(candles) < period:
        return [0.0] * len(candles)

    df = pd.DataFrame([vars(c) for c in candles])
    df['prev_close'] = df['close'].shift(1)
    df['tr1'] = df['high'] - df['low']
    df['tr2'] = (df['high'] - df['prev_close']).abs()
    df['tr3'] = (df['low'] - df['prev_close']).abs()
    
    df['tr'] = df[['tr1', 'tr2', 'tr3']].max(axis=1)
    df['atr'] = df['tr'].rolling(window=period).mean() # Simple Moving Average of TR for MVP
    
    return df['atr'].fillna(0).tolist()

def update_indicators(candles: List[Candle], atr_period: int = 14):
    """
    Batch update indicators for the whole history and attach to objects.
    Optimization: In a real system we would update incrementally.
    """
    vwaps = calculate_vwap(candles)
    atrs = calculate_atr(candles, atr_period)
    
    for i, candle in enumerate(candles):
        candle.vwap = vwaps[i]
        candle.atr = atrs[i]
