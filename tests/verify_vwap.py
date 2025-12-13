import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pandas as pd
from models.types import Candle
from core.indicators import calculate_vwap

def create_mock_candles():
    base_ts = 1716335400000 # Some start time
    candles = []
    # Create 2 days of data
    # Day 1: Price 100, Volume 10
    # Day 2: Price 200, Volume 10. VWAP should reset to 200 on first bar of Day 2.
    
    # 23:57 Day 1 (May 21 23:50)
    candles.append(Candle(symbol="BTC", timestamp=base_ts, open=100, high=100, low=100, close=100, volume=10))
    
    # + 60 mins -> 00:50 Day 2 (May 22)
    candles.append(Candle(symbol="BTC", timestamp=base_ts + 60*60*1000, open=200, high=200, low=200, close=200, volume=10))
    
    return candles

def test_vwap_reset():
    candles = create_mock_candles()
    vwaps = calculate_vwap(candles)
    
    print(f"Candle 1 (Day 1) Price: {candles[0].close} VWAP: {vwaps[0]}")
    print(f"Candle 2 (Day 2) Price: {candles[1].close} VWAP: {vwaps[1]}")
    
    if vwaps[1] == 200:
        print("SUCCESS: VWAP reset correctly on new day.")
    else:
        print(f"FAILURE: VWAP did not reset. Expected 200, got {vwaps[1]}")

if __name__ == "__main__":
    test_vwap_reset()
