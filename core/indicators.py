import pandas as pd
import numpy as np
from typing import List, Optional
from models.types import Candle, TimeframeContext
from config.settings import ATR_WINDOW, ATR_PERCENTILE_WINDOW
from datetime import datetime, timezone

# --- Core Math Helpers ---

def _calculate_slope_tail(series: List[float], period: int = 5) -> float:
    """O(1) Slope calculation for just the tail."""
    if len(series) < 2:
        return 0.0
        
    y = series[-period:] if len(series) >= period else series
    n = len(y)
    if n < 2:
        return 0.0
        
    x = np.arange(n)
    # Fast linear regression: slope = cov(x,y) / var(x)
    # simple polyfit degree 1
    slope = np.polyfit(x, y, 1)[0]
    return float(slope)

# --- Full Calculation (Initialization) ---

def calculate_indicators_full(candles: List[Candle], atr_period: int = ATR_WINDOW, context: Optional["TimeframeContext"] = None):
    """
    Batch update indicators for the whole history (O(N)).
    Used ONLY during initialization or major resets.
    """
    if not candles:
        return

    df = pd.DataFrame([
        {
            'timestamp': c.timestamp,
            'high': c.high, 'low': c.low, 'close': c.close, 'volume': c.volume,
            'spot_cvd': c.spot_cvd, 'perp_cvd': c.perp_cvd
        } 
        for c in candles
    ])
    
    # 1. VWAP (Full)
    df['typical_price'] = (df['high'] + df['low'] + df['close']) / 3.0
    df['pv'] = df['typical_price'] * df['volume']
    
    # Time grouping for VWAP reset (daily)
    df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
    df['date_group'] = df['datetime'].dt.date
    
    # Cumulative Sums for VWAP
    df['cum_pv'] = df.groupby('date_group')['pv'].cumsum()
    df['cum_vol'] = df.groupby('date_group')['volume'].cumsum()
    df['vwap'] = df['cum_pv'] / df['cum_vol'].replace(0, 1) # Avoid div by zero

    # Cumulative Sums for CVD (Full History)
    df['cum_spot_cvd'] = df['spot_cvd'].cumsum()
    df['cum_perp_cvd'] = df['perp_cvd'].cumsum()

    # 2. ATR (Full)
    df['prev_close'] = df['close'].shift(1)
    df['tr1'] = df['high'] - df['low']
    df['tr2'] = (df['high'] - df['prev_close']).abs()
    df['tr3'] = (df['low'] - df['prev_close']).abs()
    df['tr'] = df[['tr1', 'tr2', 'tr3']].max(axis=1)
    df['atr'] = df['tr'].rolling(window=atr_period).mean().fillna(0.0)

    # 3. Slopes & Percentiles (Vectorized)
    # We'll just loop for slopes as it's cleaner for now or use rolling apply
    # Since we need this for init, speed is less critical than correctness
    
    vwaps = df['vwap'].fillna(0).tolist()
    atrs = df['atr'].fillna(0).tolist()
    spot_cum = df['cum_spot_cvd'].tolist()
    perp_cum = df['cum_perp_cvd'].tolist()
    
    # Pre-populate objects
    for i, row in df.iterrows():
        c = candles[i]
        c.vwap = row['vwap']
        c.atr = row['atr']
        c.cum_pv = row['cum_pv']
        c.cum_vol = row['cum_vol']
        c.cum_spot_cvd = row['cum_spot_cvd']
        c.cum_perp_cvd = row['cum_perp_cvd']
    
    # Batch calculate slopes/percentiles
    # This is O(N*W) but acceptable for init
    for i in range(len(candles)):
        # Slope Window
        start = max(0, i - 4) # 5 points including i
        
        c = candles[i]
        c.vwap_slope = _calculate_slope_tail(vwaps[start:i+1])
        c.spot_cvd_slope = _calculate_slope_tail(spot_cum[start:i+1])
        c.perp_cvd_slope = _calculate_slope_tail(perp_cum[start:i+1])
        
        # ATR Percentile Window
        p_start = max(0, i - ATR_PERCENTILE_WINDOW + 1)
        atr_window = atrs[p_start:i+1]
        
        if len(atr_window) >= 2:
             rank = pd.Series(atr_window).rank(pct=True).iloc[-1]
             c.atr_percentile = rank * 100.0
        else:
             c.atr_percentile = 50.0

# --- Incremental Calculation (Fast Path) ---

def update_latest_candle(history: List[Candle], context: Optional["TimeframeContext"] = None, atr_period: int = ATR_WINDOW):
    """
    O(1) Update for the last candle in history using the previous candle's state.
    Assumes history[-2] is valid and fully calculated.
    """
    if not history:
        return
        
    curr = history[-1]
    prev = history[-2] if len(history) > 1 else None
    
    # 1. VWAP (Incremental)
    typical_price = (curr.high + curr.low + curr.close) / 3.0
    pv = typical_price * curr.volume
    
    # Check for Session Reset (Daily)
    reset = False
    if prev:
        # Simple date check
        curr_dt = datetime.fromtimestamp(curr.timestamp / 1000, tz=timezone.utc)
        prev_dt = datetime.fromtimestamp(prev.timestamp / 1000, tz=timezone.utc)
        if curr_dt.date() != prev_dt.date():
            reset = True
    else:
        reset = True # First candle
        
    if reset or not prev:
        curr.cum_pv = pv
        curr.cum_vol = curr.volume
    else:
        curr.cum_pv = prev.cum_pv + pv
        curr.cum_vol = prev.cum_vol + curr.volume
        
    curr.vwap = curr.cum_pv / curr.cum_vol if curr.cum_vol > 0 else 0.0
    
    # 2. CVD (Incremental)
    # These are strictly cumulative sums
    curr.cum_spot_cvd = (prev.cum_spot_cvd if prev else 0.0) + curr.spot_cvd
    curr.cum_perp_cvd = (prev.cum_perp_cvd if prev else 0.0) + curr.perp_cvd
    
    # 3. ATR (Incremental Smoothing - Wilder's or SMA?)
    # Original implementation was Rolling Mean (SMA) of TR
    # SMA requires full window sum. 
    # To keep it strict O(1) without storing window sum, we can peek back.
    # Since we have the history list, accessing history[-14:] is O(1) (fixed size slice)
    
    # TR Calculation
    if prev:
        tr1 = curr.high - curr.low
        tr2 = abs(curr.high - prev.close)
        tr3 = abs(curr.low - prev.close)
        tr = max(tr1, tr2, tr3)
    else:
        tr = curr.high - curr.low
        
    # Standard SMA ATR (compatible with rolling(14).mean())
    # We need to average the TRs of the last N candles.
    # We don't store TRs on the object, so we must compute TRs for the last N candles on the fly.
    # Cost: 14 operations. Fast enough.
    
    tr_sum = tr
    count = 1
    
    # Walk back up to (atr_period - 1) steps
    # We need prev closes, so we iterate
    lookback = min(len(history), atr_period)
    
    # Optimize: If strictly incrementally maintaining SMA is hard without storing TR,
    # we can re-compute TRs for the small window. 14 items is negligible.
    
    # Let's just grab the last N candles to compute ATR.
    # This is effectively O(N) where N=14. Constant time relative to history length.
    window_candidates = history[-lookback:] 
    
    if len(window_candidates) < atr_period:
        curr.atr = 0.0 # Not enough data
    else:
        # Compute TR for the window
        trs = []
        for i in range(len(window_candidates)):
            c = window_candidates[i]
            if i == 0:
                 # If this is the start of the window, we need the candle BEFORE it to get TR
                 # Use history index
                 hist_idx = len(history) - lookback + i
                 p = history[hist_idx - 1] if hist_idx > 0 else None
            else:
                 p = window_candidates[i-1]
            
            if p:
                t1 = c.high - c.low
                t2 = abs(c.high - p.close)
                t3 = abs(c.low - p.close)
                trs.append(max(t1, t2, t3))
            else:
                trs.append(c.high - c.low)
                
        curr.atr = sum(trs) / len(trs)
        
    # 4. Slopes (O(period) = O(5))
    # VWAP Slope
    # Extract last 5 VWAPs
    vwap_window = [c.vwap for c in history[-5:] if c.vwap is not None]
    curr.vwap_slope = _calculate_slope_tail(vwap_window)
    
    # CVD Slopes
    spot_cvd_window = [c.cum_spot_cvd for c in history[-5:]] # usage of CUMULATIVE
    curr.spot_cvd_slope = _calculate_slope_tail(spot_cvd_window)

    perp_cvd_window = [c.cum_perp_cvd for c in history[-5:]]
    curr.perp_cvd_slope = _calculate_slope_tail(perp_cvd_window)
    
    # 5. ATR Percentile (O(100))
    # Extract last 100 ATRs
    atr_window = [c.atr for c in history[-ATR_PERCENTILE_WINDOW:] if c.atr is not None]
    if len(atr_window) >= 2:
        # Percentile of current (last item) relative to window
        curr_atr = atr_window[-1]
        # Count how many are <= current
        # Scipy/Pandas rank is roughly: count(x <= val) / N * 100
        count_lte = sum(1 for x in atr_window if x <= curr_atr)
        curr.atr_percentile = (count_lte / len(atr_window)) * 100.0
    else:
        curr.atr_percentile = 50.0

# Alias for compatibility if needed, but we should switch calls to update_latest_candle
update_indicators = calculate_indicators_full


