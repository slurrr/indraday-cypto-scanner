import pandas as pd
import numpy as np
from typing import List, Optional
from models.types import Candle, TimeframeContext
from config.settings import ATR_WINDOW, ATR_PERCENTILE_WINDOW, SLOPE_Z_SCORE_WINDOW
from datetime import datetime, timezone
from utils.logger import setup_logger

logger = setup_logger("Indicators")

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
    
    # [MATH PROOF] - Log the inputs for verification
    # Only Log for BTCUSDT to avoid flood? We don't have symbol context here easily.
    # We will log it if the values are large (likely BTC/ETH) or just sample it.
    # Actually, main.py logs the 'CumWindow' now. 
    # But let's verify the LINEAR REGRESSION output specifically.
    return float(slope)

def _calculate_zscore(last_val: float, history: List[float]) -> float:
    """Calculate Z-Score of last_val against history distribution."""
    if not history or len(history) < 2:
        return 0.0
    
    # Simple perf optimization: use numpy
    mean = np.mean(history)
    std = np.std(history)
    
    if std == 0:
        return 0.0
        
    z = (last_val - mean) / std
    # [MATH PROOF]
    # logger.debug(f"[MATH_PROOF] Z-Score: Val={last_val:.4f} Mean={mean:.4f} Std={std:.4f} -> Z={z:.4f}")
    return float(z)

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
    # Use per-candle CVD deltas for slope calculation (deterministic across instances)
    spot_cvd_deltas = df['spot_cvd'].fillna(0).tolist()
    perp_cvd_deltas = df['perp_cvd'].fillna(0).tolist()
    
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
    
    # Track slopes for Z-Score
    spot_slopes_history = []
    perp_slopes_history = []
    
    for i in range(len(candles)):
        # Slope Window
        start = max(0, i - 4) # 5 points including i
        
        c = candles[i]
        c.vwap_slope = _calculate_slope_tail(vwaps[start:i+1])
        # Use per-candle CVD deltas for deterministic slope calculation
        c.spot_cvd_slope = _calculate_slope_tail(spot_cvd_deltas[start:i+1])
        c.perp_cvd_slope = _calculate_slope_tail(perp_cvd_deltas[start:i+1])
        
        spot_slopes_history.append(c.spot_cvd_slope)
        perp_slopes_history.append(c.perp_cvd_slope)
        
        # Z-Score Normalization
        # Lookback window for Z-Score statistics
        z_start = max(0, len(spot_slopes_history) - SLOPE_Z_SCORE_WINDOW)
        
        c.spot_cvd_slope_z = _calculate_zscore(
            c.spot_cvd_slope, 
            spot_slopes_history[z_start:]
        )
        c.perp_cvd_slope_z = _calculate_zscore(
            c.perp_cvd_slope, 
            perp_slopes_history[z_start:]
        )
        
        # ATR Percentile Window
        p_start = max(0, i - ATR_PERCENTILE_WINDOW + 1)
        atr_window = atrs[p_start:i+1]
        
        if len(atr_window) >= 2:
             rank = pd.Series(atr_window).rank(pct=True).iloc[-1]
             c.atr_percentile = rank * 100.0
        else:
             c.atr_percentile = 50.0

# --- Incremental Calculation (Fast Path) ---

def update_indicators_from_index(history: List[Candle], start_index: int, context: Optional["TimeframeContext"] = None):
    """
    Repair the indicator chain starting from a specific index `start_index` to the end.
    Crucial for fixing 'broken chains' after backfilling or reconciliation.
    """
    if not history:
        return
        
    start = max(0, start_index)
    for i in range(start, len(history)):
        update_candle_at_index(history, i, context)

def update_candle_at_index(history: List[Candle], index: int, context: Optional["TimeframeContext"] = None, atr_period: int = ATR_WINDOW):
    """
    O(1) Update for a specific candle index using the previous candle's state.
    Calculates VWAP, CVD, ATR, Slopes, and Percentiles.
    """
    if not history or index < 0 or index >= len(history):
        return
        
    curr = history[index]
    prev = history[index-1] if index > 0 else None
    
    # Defensive: Ensure prev has cumulative sums initialized (might be 0.0 or None)
    prev_cum_pv = (prev.cum_pv if prev and prev.cum_pv is not None else 0.0)
    prev_cum_vol = (prev.cum_vol if prev and prev.cum_vol is not None else 0.0)
    prev_cum_spot_cvd = (prev.cum_spot_cvd if prev and prev.cum_spot_cvd is not None else 0.0)
    prev_cum_perp_cvd = (prev.cum_perp_cvd if prev and prev.cum_perp_cvd is not None else 0.0)
    
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
        curr.cum_pv = prev_cum_pv + pv
        curr.cum_vol = prev_cum_vol + curr.volume
        
    curr.vwap = curr.cum_pv / curr.cum_vol if curr.cum_vol > 0 else 0.0
    
    # 2. CVD (Incremental)
    # These are strictly cumulative sums
    curr.cum_spot_cvd = prev_cum_spot_cvd + (curr.spot_cvd if curr.spot_cvd is not None else 0.0)
    curr.cum_perp_cvd = prev_cum_perp_cvd + (curr.perp_cvd if curr.perp_cvd is not None else 0.0)
    
    # 3. ATR (Incremental Calculation on the fly)
    
    # We need the last N candles ending at `index`
    # lookback length
    window_len = min(index + 1, atr_period)
    
    # Extract window: ending at index (inclusive)
    # Start index for slice: index + 1 - window_len
    start_idx = index + 1 - window_len
    window_candidates = history[start_idx : index + 1]
    
    if len(window_candidates) < atr_period:
        curr.atr = 0.0 
    else:
        # Compute TR for the window
        trs = []
        for i in range(len(window_candidates)):
            c = window_candidates[i]
            # Actual index in history
            hist_idx = start_idx + i
            
            if i == 0:
                 # Start of window, look at previous candle in history
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
    # Window ending at index
    slope_window_len = 5
    s_start = max(0, index + 1 - slope_window_len)
    
    # VWAP Slope
    vwap_window = [c.vwap for c in history[s_start : index+1] if c.vwap is not None]
    curr.vwap_slope = _calculate_slope_tail(vwap_window)
    
    # CVD Slopes - Use per-candle deltas for deterministic calculation
    # This ensures identical candle data produces identical slopes regardless of startup timing
    spot_cvd_window = [c.spot_cvd if c.spot_cvd is not None else 0.0 for c in history[s_start : index+1]] 
    curr.spot_cvd_slope = _calculate_slope_tail(spot_cvd_window)

    perp_cvd_window = [c.perp_cvd if c.perp_cvd is not None else 0.0 for c in history[s_start : index+1]]
    curr.perp_cvd_slope = _calculate_slope_tail(perp_cvd_window)
    
    # 5. Z-Score Normalization (Incremental)
    # We need the last N calculated slopes.
    z_start = max(0, index + 1 - SLOPE_Z_SCORE_WINDOW)
    
    # Spot Z
    # Filter for None to be safe, though update_indicators_from_index should ensure continuity
    spot_slope_hist = [c.spot_cvd_slope for c in history[z_start : index+1] if c.spot_cvd_slope is not None]
    curr.spot_cvd_slope_z = _calculate_zscore(curr.spot_cvd_slope, spot_slope_hist)
    
    # Perp Z
    perp_slope_hist = [c.perp_cvd_slope for c in history[z_start : index+1] if c.perp_cvd_slope is not None]
    curr.perp_cvd_slope_z = _calculate_zscore(curr.perp_cvd_slope, perp_slope_hist)
    
    # 6. ATR Percentile
    pct_start = max(0, index + 1 - ATR_PERCENTILE_WINDOW)
    atr_window = [c.atr for c in history[pct_start : index+1] if c.atr is not None]
    
    if len(atr_window) >= 2:
        curr_atr = atr_window[-1]
        count_lte = sum(1 for x in atr_window if x <= curr_atr)
        curr.atr_percentile = (count_lte / len(atr_window)) * 100.0
    else:
        curr.atr_percentile = 50.0

    # TRACE LOGGING: Indicators Calculated
    # logger.debug(f"[TRACE][{curr.symbol}] Indicators: VWAP={curr.vwap:.2f} ATR={curr.atr:.4f} "
    #              f"SpotSlope={curr.spot_cvd_slope:.2f} (Z={curr.spot_cvd_slope_z:.2f}) "
    #              f"PerpSlope={curr.perp_cvd_slope:.2f} (Z={curr.perp_cvd_slope_z:.2f})")

def update_latest_candle(history: List[Candle], context: Optional["TimeframeContext"] = None, atr_period: int = ATR_WINDOW):
    """
    Update only the last candle (convenience wrapper).
    """
    if not history:
        return
    update_candle_at_index(history, len(history) - 1, context, atr_period)


