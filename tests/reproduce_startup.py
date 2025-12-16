import sys
import os
sys.path.append(os.getcwd())

from models.types import Candle, Trade, FlowRegime, Alert
from core.analyzer import Analyzer
from core.indicators import update_indicators
from core.data_processor import DataProcessor
from typing import List, Dict
import time
import pandas as pd
import numpy as np

# Mock Settings if needed, but we import from config
from config.settings import MIN_ATR_PERCENTILE

def create_history_candle(i: int) -> Candle:
    # Use realistic Bitcoin price levels
    base = 90000.0
    # Add some noise for ATR
    noise = (i % 10) * 10
    return Candle(
        symbol="BTCUSDT",
        timestamp=1000000 + (i * 60000),
        open=base,
        high=base + 50 + noise,
        low=base - 50 - noise,
        close=base + 10,
        volume=100.0, 
        spot_cvd=0.0, # NO CVD IN HISTORY
        perp_cvd=0.0,
        closed=True
    )

def main():
    print(">>> Initializing History with 1000 candles (No CVD)...")
    history = [create_history_candle(i) for i in range(1000)]
    
    analyzer = Analyzer()
    
    print(f">>> History initialized. Last candle index: {len(history)}")
    
    # Pre-calc like main.py
    update_indicators(history)
    print(f">>> Indicators updated. Last ATR: {history[-1].atr:.2f}")

    # Simulate Live Trading for 5 minutes
    print(">>> Starting Live Simulation (5 Minutes)...")
    
    for i in range(5):
        minute_index = 1000 + i
        
        # Create a LIVE candle 
        live_candle = Candle(
            symbol="BTCUSDT",
            timestamp=1000000 + (minute_index * 60000),
            open=90000.0,
            high=90200.0, # 200 range (Expansion vs ~100)
            low=90000.0,
            close=90180.0,
            volume=500.0, 
            spot_cvd=500.0, # Strong Buying
            perp_cvd=500.0, 
            closed=True
        )
        
        # This mimics main.py flow:
        # 1. Append
        history.append(live_candle)
        
        # 2. Update Indicators
        # In main.py: update_indicators(history)
        update_indicators(history)
        
        curr = history[-1]
        
        # 3. Analyze
        # analyzer.analyze calls:
        #   determine_regime
        #   check patterns
        
        regime = analyzer._determine_regime(history, history[-1])
        alerts = analyzer.analyze("BTCUSDT", history)
        
        print(f"\n[Minute {i+1}]")
        print(f"  Timestamp: {curr.timestamp}")
        print(f"  Close: {curr.close}, High: {curr.high}, Low: {curr.low}")
        print(f"  SpotCVD: {curr.spot_cvd} (Slope: {curr.spot_cvd_slope:.4f})")
        print(f"  PerpCVD: {curr.perp_cvd} (Slope: {curr.perp_cvd_slope:.4f})")
        print(f"  ATR: {curr.atr:.2f}, ATR% Tile: {curr.atr_percentile:.2f}")
        print(f"  Regime: {regime}")
        print(f"  Alerts: {len(alerts)}")
        
        if not alerts and regime == FlowRegime.NEUTRAL:
             print("  FAIL: Regime is Neutral despite heavy buying?")
             
        for a in alerts:
            print(f"  ALERT: {a.pattern} ({a.score})")

if __name__ == "__main__":
    main()
