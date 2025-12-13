import time
import sys
import os
sys.path.append(os.getcwd())

from core.analyzer import Analyzer
from core.indicators import update_indicators
from models.types import Candle, Trade, PatternType, FlowRegime
from typing import List
import random

def create_base_candle(timestamp: int, price: float) -> Candle:
    return Candle(
        symbol="BTCUSDT",
        timestamp=timestamp,
        open=price,
        high=price,
        low=price,
        close=price,
        volume=100.0,
        spot_cvd=0.0,
        perp_cvd=0.0,
        closed=True
    )

def simulate_ignition_pattern():
    print("\n--- Simulating IGNITION Pattern ---")
    analyzer = Analyzer()
    candles = []
    
    # 1. Generate 90 candles of low volatility (compression)
    base_price = 50000.0
    start_time = 1000000
    
    for i in range(90):
        c = create_base_candle(start_time + i*60000, base_price)
        c.high = base_price + 10
        c.low = base_price - 10
        c.volume = 50
        candles.append(c)
        base_price += random.uniform(-5, 5) # Drift
        
    # 2. Add IGNITION candle: Expansion + Vol Spike
    ignition_candle = create_base_candle(start_time + 90*60000, base_price)
    ignition_candle.high = base_price + 200 # Big expansion
    ignition_candle.low = base_price
    ignition_candle.close = base_price + 190
    ignition_candle.volume = 500 # Vol spike
    
    # Flow consensus for extra points
    ignition_candle.spot_cvd_slope = 100
    ignition_candle.perp_cvd_slope = 100
    
    candles.append(ignition_candle)
    
    # Process indicators
    update_indicators(candles)
    
    # OVERWRITE INDICATORS FOR TEST (Mocking the calculated values)
    # We want to test logic, not the math of slopes (covered in unit tests ideally)
    candles[-1].spot_cvd_slope = 100
    candles[-1].perp_cvd_slope = 100
    candles[-1].atr_percentile = 100 # High volatility expansion
    
    # Analyze
    alerts = analyzer.analyze("BTCUSDT", candles)
    
    if alerts:
        for a in alerts:
            print(f"SUCCESS: Alert Triggered: {a}")
    else:
        print("FAILURE: No ignition alert triggered.")
        # Debug
        curr = candles[-1]
        dbg = analyzer.debug_analyze("BTCUSDT", candles)
        print(f"Debug Reason: {dbg['patterns']['IGNITION']}")
        regime = analyzer._determine_regime(candles, candles[-1])
        print(f"Debug: ATR={curr.atr}, Range={curr.high-curr.low}, Vol={curr.volume}, PrevVol={candles[-2].volume}, Regime={regime}")

def simulate_trap_pattern():
    print("\n--- Simulating TRAP Pattern ---")
    analyzer = Analyzer()
    candles = []
    
    # 1. Setup a range
    base_price = 50000.0
    start_time = 1000000
    
    # 60 candles in range 49900 - 50100
    for i in range(60):
        c = create_base_candle(start_time + i*60000, base_price)
        c.high = base_price + 50
        c.low = base_price - 50
        c.volume = 100
        candles.append(c)
        
    # 2. Trap Candle: Sweep High (50150) then close low (50050)
    trap_candle = create_base_candle(start_time + 60*60000, 50100)
    trap_candle.high = 50200 # Sweep previous high (50050 approx from Drift, wait, I hardcoded +50)
    # The max high in range is roughly 50050.
    trap_candle.low = 50050
    trap_candle.close = 50040 # Close back inside range (below recent high 50050)
    
    # Add valid ATR/VWAP
    trap_candle.volume = 200
    
    # Set Flow to CONFLICT to trigger trap confirmation
    trap_candle.spot_cvd = -500 # Net sell
    trap_candle.perp_cvd = 500 # Net buy
    
    candles.append(trap_candle)
    
    update_indicators(candles)
    
    # Manually hack slopes for test
    candles[-1].spot_cvd_slope = -100
    candles[-1].perp_cvd_slope = 100
    candles[-1].atr_percentile = 50 # Mid volatility
    
    alerts = analyzer.analyze("BTCUSDT", candles)
    
    if alerts:
        for a in alerts:
            if a.pattern == PatternType.TRAP:
                print(f"SUCCESS: Alert Triggered: {a}")
                return
    
    # If we get here
    print("FAILURE: No trap alert triggered.")
    regime = analyzer._determine_regime(candles, candles[-1])
    print(f"Debug: Regime={regime}, SpotSlope={candles[-1].spot_cvd_slope}")

def simulate_failed_breakout():
    print("\n--- Simulating FAILED BREAKOUT Pattern ---")
    analyzer = Analyzer()
    candles = []
    
    base_price = 20000.0
    start_time = 1000000
    
    # 1. Range
    for i in range(60):
        c = create_base_candle(start_time + i*60000, base_price)
        c.high = base_price + 20
        c.low = base_price - 20
        candles.append(c)
        
    # 2. Breakout candle
    breakout_candle = create_base_candle(start_time + 60*60000, base_price)
    breakout_candle.high = base_price + 40 # Break high (20020)
    breakout_candle.close = base_price + 10 # Close inside
    
    # WEAK FLOW / NEUTRAL
    breakout_candle.spot_cvd = 0
    breakout_candle.perp_cvd = 0
    candles.append(breakout_candle)
    
    update_indicators(candles)
    
    # Mock Neutral indicators
    candles[-1].spot_cvd_slope = 0 # Flat
    candles[-1].perp_cvd_slope = 0
    candles[-1].atr_percentile = 30 # Not too low to be gated, not super high
    
    alerts = analyzer.analyze("BTCUSDT", candles)
    
    found = False
    for a in alerts:
        if a.pattern == PatternType.FAILED_BREAKOUT:
            print(f"SUCCESS: Alert Triggered: {a}")
            found = True
            
    if not found:
         print("FAILURE: No Failed Breakout alert.")

def simulate_pullback():
    print("\n--- Simulating PULLBACK Pattern ---")
    analyzer = Analyzer()
    candles = []
    
    base_price = 30000.0
    start_time = 1000000
    
    # 1. Impulse Move (Last 10 candles)
    # Pad history first
    for i in range(20):
        c = create_base_candle(start_time - (20-i)*60000, base_price)
        candles.append(c)
        
    for i in range(10):
        c = create_base_candle(start_time + i*60000, base_price + i*10)
        c.high = c.close + 5
        c.low = c.close - 5
        candles.append(c)

    # Make one candle HUGE impulse
    impulse_c = candles[-2]
    impulse_c.high = impulse_c.low + 1000 # Massive range
    impulse_c.close = impulse_c.high
    # Mock ATR to be small so this looks big
    impulse_c.atr = 50.0 
    
    # 2. Pullback Candle
    pb_candle = create_base_candle(start_time + 10*60000, base_price + 100) # Pulled back logic?
    # Logic requires: Low Vol Compression AND Near VWAP
    
    # Let's set VWAP to X, and Close to X
    pb_candle.vwap = 30100
    pb_candle.close = 30105 # Very close
    pb_candle.high = 30110
    pb_candle.low = 30100 # Range 10
    pb_candle.atr = 50 # Range 10 < 0.8 * 50 (40) -> Compressed
    
    candles.append(pb_candle)
    
    # Flow needs to be supportive? Spec: "Flow regime not distributing or conflicting"
    # So Consensus or Spot Dominant
    
    # Mock indicators
    for c in candles:
        c.atr = 50.0
        
    candles[-2].vwap = 30000
    candles[-1].spot_cvd_slope = 10
    candles[-1].perp_cvd_slope = 10 # Consensus
    candles[-1].atr_percentile = 50
    
    # Analyze
    alerts = analyzer.analyze("BTCUSDT", candles)
    found = False
    for a in alerts:
        if a.pattern == PatternType.PULLBACK:
            print(f"SUCCESS: Alert Triggered: {a}")
            found = True
            
    if not found:
        print("FAILURE: No Pullback alert.")

def simulate_vwap_reclaim():
    print("\n--- Simulating VWAP RECLAIM Pattern ---")
    analyzer = Analyzer()
    candles = []
    
    base_price = 40000.0
    start_time = 1000000
    
    # Pad history
    for i in range(20):
        c = create_base_candle(start_time - (20-i)*60000, base_price)
        c.vwap = base_price # Default
        candles.append(c)
    
    # 1. Prev Candle below VWAP
    c1 = create_base_candle(start_time, 40000)
    c1.vwap = 40050
    c1.close = 40000 # Below
    candles.append(c1)
    
    # 2. Curr Candle above VWAP
    c2 = create_base_candle(start_time + 60000, 40100)
    c2.vwap = 40050
    c2.close = 40100 # Above
    candles.append(c2)
    
    # Flow
    c2.spot_cvd_slope = 10
    c2.perp_cvd_slope = 10
    c2.atr_percentile = 50
    
    alerts = analyzer.analyze("BTCUSDT", candles)
    found = False
    for a in alerts:
        if a.pattern == PatternType.VWAP_RECLAIM:
            print(f"SUCCESS: Alert Triggered: {a}")
            found = True
            
    if not found:
        print("FAILURE: No VWAP Reclaim alert.")


if __name__ == "__main__":
    simulate_ignition_pattern()
    simulate_trap_pattern()
    simulate_failed_breakout()
    simulate_pullback()
    simulate_vwap_reclaim()
