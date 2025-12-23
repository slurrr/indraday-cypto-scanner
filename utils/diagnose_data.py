
import sys
import os
import time

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from data.binance_client import BinanceClient
from models.types import TimeframeContext, Candle
from core.indicators import calculate_indicators_full

def diagnose():
    print("--- DIAGNOSTIC: Checking Binance Data & Slopes ---")
    client = BinanceClient(["BTCUSDT"], lambda x: None)
    
    symbol = "BTCUSDT"
    ctx = TimeframeContext("3m", 3 * 60 * 1000)
    
    # 1. Fetch History (Spot)
    print(f"Fetching {symbol} SPOT history...")
    spot_map = client.fetch_historical_candles(lookback_bars=50, context=ctx, source='spot')
    if not spot_map or symbol not in spot_map:
        print("ERROR: Could not fetch SPOT history.")
        return

    candles = spot_map[symbol]
    print(f"Fetched {len(candles)} candles.")
    
    # 2. Calculate Indicators
    print("Calculating indicators...")
    calculate_indicators_full(candles, context=ctx)
    
    # 3. Inspect Slopes
    zero_slopes = 0
    none_slopes = 0
    valid_slopes = 0
    
    for c in candles:
        if c.spot_cvd_slope is None:
            none_slopes += 1
        elif c.spot_cvd_slope == 0.0:
            zero_slopes += 1
            print(f"WARNING: Zero Slope at {c.timestamp} | CVD={c.spot_cvd} Vol={c.volume}")
        else:
            valid_slopes += 1
            
    print(f"\nStats:")
    print(f"Total: {len(candles)}")
    print(f"None Slopes (expected for first few): {none_slopes}")
    print(f"Zero Slopes (PROBLEM): {zero_slopes}")
    print(f"Valid Slopes: {valid_slopes}")
    
    last = candles[-1]
    print(f"\nLatest Candle: {last.timestamp}")
    print(f"CVD: {last.spot_cvd}")
    print(f"Slope: {last.spot_cvd_slope}")
    
    if zero_slopes == 0 and valid_slopes > 0:
        print("\n>>> DIAGNOSIS: Data is VALID. Slopes are forming correctly.")
    else:
        print("\n>>> DIAGNOSIS: ISSUES FOUND.")

if __name__ == "__main__":
    diagnose()
