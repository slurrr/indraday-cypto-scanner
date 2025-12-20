
import sys
import os
import time
from typing import List

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.analyzer import Analyzer
from models.types import Candle, StateSnapshot, State
from config.settings import TIMEFRAME_1M

def create_mock_candle(
    timestamp: int,
    open_p: float,
    high: float,
    low: float,
    close: float,
    vwap: float,
    atr: float = 1.0,
    spot_slope: float = 0.0,
    perp_slope: float = 0.0
) -> Candle:
    return Candle(
        symbol="BTCUSDT",
        timestamp=timestamp,
        open=open_p,
        high=high,
        low=low,
        close=close,
        volume=1000.0,
        spot_cvd=0.0,
        perp_cvd=0.0,
        closed=True,
        vwap=vwap,
        atr=atr,
        vwap_slope=0.0,
        atr_percentile=50.0,
        spot_cvd_slope=spot_slope,
        perp_cvd_slope=perp_slope
    )

def test_long_execution():
    print("\n--- Testing LONG Execution ---")
    analyzer = Analyzer()
    
    # Setup State: ACT with LONG direction
    state = StateSnapshot(
        symbol="BTCUSDT",
        state=State.ACT,
        act_direction="LONG",
        entered_at=int(time.time() * 1000)
    )
    
    # 1. Test Failure Case: Price < VWAP (should fail)
    candles_fail = []
    base_ts = int(time.time() * 1000)
    for i in range(5):
        candles_fail.append(create_mock_candle(
            timestamp=base_ts + i*60000,
            open_p=100.0, high=102.0, low=99.0, close=99.5, # Bearish/Below VWAP
            vwap=100.0
        ))
    
    signals = analyzer.analyze_execution("BTCUSDT", candles_fail, state)
    print(f"Case 1 (Bad Conditions): Got {len(signals)} signals (Expected 0)")
    
    # 2. Test Success Case: Price > VWAP, Green, Directional
    candles_pass = []
    base_ts = int(time.time() * 1000)
    for i in range(4):
         # Context candles (don't matter much for this logic, just need length)
         candles_pass.append(create_mock_candle(
            timestamp=base_ts + i*60000,
            open_p=100.0, high=101.0, low=99.0, close=100.0,
            vwap=100.0
        ))
    
    # Trigger Candle
    # Open=100, Close=101 (Green, +1), High=102, Low=99 (Range=3). Body/Range = 1/3 = 0.33 > 0.3 (OK)
    # Close > VWAP(100.5) -> 101 > 100.5 (OK)
    candles_pass.append(create_mock_candle(
        timestamp=base_ts + 4*60000,
        open_p=100.0, 
        high=102.0, 
        low=99.0, 
        close=101.0, 
        vwap=100.5,
        spot_slope=0.1, # Neutral/Bullish
        perp_slope=0.1
    ))
    
    signals = analyzer.analyze_execution("BTCUSDT", candles_pass, state)
    print(f"Case 2 (Good Conditions): Got {len(signals)} signals (Expected 1)")
    if signals:
        print(f"  Signal: {signals[0]}")

def test_short_execution():
    print("\n--- Testing SHORT Execution ---")
    analyzer = Analyzer()
    
    # Setup State: ACT with SHORT direction
    state = StateSnapshot(
        symbol="BTCUSDT",
        state=State.ACT,
        act_direction="SHORT",
        entered_at=int(time.time() * 1000)
    )

    # Success Case: Price < VWAP, Red, Directional
    candles_pass = []
    base_ts = int(time.time() * 1000)
    for i in range(4):
         candles_pass.append(create_mock_candle(
            timestamp=base_ts + i*60000,
            open_p=100.0, high=101.0, low=99.0, close=100.0,
            vwap=100.0
        ))
    
    # Trigger Candle
    # Open=100, Close=99 (Red, -1), High=100.5, Low=98 (Range=2.5). Body/Range = 1/2.5 = 0.4 > 0.3 (OK)
    # Close < VWAP(99.5) -> 99 < 99.5 (OK)
    candles_pass.append(create_mock_candle(
        timestamp=base_ts + 4*60000,
        open_p=100.0, 
        high=100.5, 
        low=98.0, 
        close=99.0, 
        vwap=99.5,
        spot_slope=-0.1, 
        perp_slope=-0.1
    ))
    
    signals = analyzer.analyze_execution("BTCUSDT", candles_pass, state)
    print(f"Case 1 (Good Conditions): Got {len(signals)} signals (Expected 1)")
    if signals:
        print(f"  Signal: {signals[0]}")


if __name__ == "__main__":
    test_long_execution()
    test_short_execution()
