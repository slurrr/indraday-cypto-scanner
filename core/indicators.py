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

def calculate_slope(series: List[float], period: int = 5) -> List[float]:
    """
    Calculate linear regression slope over a rolling window.
    Returns the slope of the line fitted to the data points.
    """
    if len(series) < period:
        return [0.0] * len(series)
    
    slopes = [0.0] * (period - 1)
    
    # x is just 0, 1, 2, ... period-1
    x = np.arange(period)
    
    for i in range(period, len(series) + 1):
        y = series[i-period:i]
        # Polyfit degree 1 is linear regression, returns [slope, intercept]
        slope = np.polyfit(x, y, 1)[0]
        slopes.append(slope)
        
    return slopes

def calculate_atr_percentile(atrs: List[float], period: int = 100) -> List[float]:
    """
    Calculate the percentile rank of the current ATR relative to the last N ATRs.
    """
    if len(atrs) < period:
        return [50.0] * len(atrs) # Default to mid-range
        
    percentiles = [50.0] * (period - 1)
    
    for i in range(period, len(atrs) + 1):
        window = atrs[i-period:i]
        current = atrs[i-1]
        # Percentile rank
        rank = pd.Series(window).rank(pct=True).iloc[-1]
        percentiles.append(rank * 100)
        
    return percentiles

def update_indicators(candles: List[Candle], atr_period: int = 14):
    """
    Batch update indicators for the whole history and attach to objects.
    Optimization: In a real system we would update incrementally.
    """
    vwaps = calculate_vwap(candles)
    atrs = calculate_atr(candles, atr_period)
    
    # Slopes
    vwap_slope = calculate_slope(vwaps, period=5)
    
    # For CVD slopes, we first extract the series
    # Note: Candle.spot_cvd is cumulative in our design? 
    # Wait, check data_processor. It increments candle.spot_cvd += delta.
    # So candle.spot_cvd is the DELTA for that candle, or the cumulative up to that point?
    # Spec says "Spot CVD" which usually implies cumulative line.
    # Data processor implementation: `candle.spot_cvd += delta`.
    # This means `candle.spot_cvd` holds the SUM of deltas for THAT ONE candle.
    # So it is the NET VOLUME DELTA for that minute.
    # The "CVD" line is the cumulative sum of these candle values.
    # We must calculate the CUMULATIVE series first before taking slope.
    
    spot_deltas = [c.spot_cvd for c in candles]
    perp_deltas = [c.perp_cvd for c in candles]
    
    spot_cum = pd.Series(spot_deltas).cumsum().tolist()
    perp_cum = pd.Series(perp_deltas).cumsum().tolist()
    
    spot_slopes = calculate_slope(spot_cum, period=5)
    perp_slopes = calculate_slope(perp_cum, period=5)
    
    # ATR Percentile
    atr_percentiles = calculate_atr_percentile(atrs, period=100)
    
    for i, candle in enumerate(candles):
        candle.vwap = vwaps[i]
        candle.atr = atrs[i]
        candle.vwap_slope = vwap_slope[i]
        candle.spot_cvd_slope = spot_slopes[i]
        candle.perp_cvd_slope = perp_slopes[i]
        candle.atr_percentile = atr_percentiles[i]

