
import sys
import os
import copy
from typing import List

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from models.types import Candle, TimeframeContext
from core.indicators import calculate_indicators_full, update_latest_candle

def create_mock_candles(count: int, start_ts: int = 1000000) -> List[Candle]:
    candles = []
    base_price = 1000.0
    for i in range(count):
        candles.append(Candle(
            symbol="BTCUSDT",
            timestamp=start_ts + (i * 60000),
            open=base_price,
            high=base_price + 10,
            low=base_price - 10,
            close=base_price,
            volume=100.0,
            spot_cvd=5.0,  # Positive flow
            perp_cvd=10.0, # Stronger positive flow
            closed=True
        ))
        base_price += 1.0 # Slight uptrend
    return candles

def test_perp_injection_logic():
    print("--- Testing Perp Injection Logic ---")
    
    # 1. Setup History
    history_len = 50
    perp_history = create_mock_candles(history_len)
    # Arguments: name, duration_ms, lookback (optional)
    # Based on models/types.py, it likely takes (name, duration_ms) and maybe lookback is inferred or property.
    # The error said "takes 3 positional arguments but 4 were given" (self + 3 args?)
    # Wait, TimeframeContext("3m", 180000, 20) is 3 args + self = 4. 
    # If init is __init__(self, name: str, duration_ms: int):
    # Then passing 3 args fails.
    context = TimeframeContext(name="3m", interval_ms=180000)
    
    # 2. Initial Calculation (Simulating what analyze_3m calls initially)
    # Note: main.py calls calculate_indicators_full on reference
    calculate_indicators_full(perp_history, context=context)
    
    # Verify baseline slope
    last_slope = perp_history[-1].perp_cvd_slope
    print(f"Baseline Slope (idx -1): {last_slope}")
    if last_slope is None:
        print("FAIL: Baseline slope is None")
        return

    # 3. Simulate Active Candle Injection (The exact logic in main.py)
    # Create an 'active' candle that has NOT been processed yet (no cumulative fields, no slope)
    active_ts = perp_history[-1].timestamp + 60000
    active_perp = Candle(
        symbol="BTCUSDT",
        timestamp=active_ts,
        open=1050.0,
        high=1060.0,
        low=1040.0,
        close=1055.0,
        volume=50.0,
        spot_cvd=0.0,
        perp_cvd=20.0, # Big jump
        closed=False
        # Note: cumulative fields and slopes are None by default
    )
    
    print(f"Active Perp before injection - Slope: {active_perp.perp_cvd_slope}")
    assert active_perp.perp_cvd_slope is None, "Active perp should have None slope initially"

    # 4. Perform Injection (Copy-Paste Logic from main.py)
    # We need a list we can mutate safely
    perp_history_injected = list(perp_history) # Shallow copy of list
    
    # Clone active perp to avoid race conditions
    active_perp_snap = copy.copy(active_perp)
    perp_history_injected.append(active_perp_snap)
    
    # Calculate slope for this new tail (O(1))
    # This edits the object at perp_history_injected[-1] which is active_perp_snap
    update_latest_candle(perp_history_injected, context=context)
    
    # 5. Verify Results
    
    # A. Check the injected object inside the list
    injected_candle = perp_history_injected[-1]
    slope = injected_candle.perp_cvd_slope
    print(f"Injected Candle Slope (in list): {slope}")
    
    # B. Check the local snapshot variable (should also be updated because it's the *same object*)
    snap_slope = active_perp_snap.perp_cvd_slope
    print(f"Snapshot Variable Slope: {snap_slope}")

    # C. Check the ORIGINAL active_perp (should be UNTOUCHED/None)
    orig_slope = active_perp.perp_cvd_slope
    print(f"Original Active Perp Slope: {orig_slope}")
    
    # D. Check logic integrity
    if slope is None:
        print("FAIL: Injected slope resulted in None")
    elif slope == 0.0 and active_perp.perp_cvd != 0:
        # It's possible to be 0.0 if not enough data, but with 50 items it should definitely calculate something.
        # Although update_latest_candle uses a window.
        print(f"WARNING: Slope is 0.0. Prep CVD was {active_perp.perp_cvd}")
    else:
        print("SUCCESS: Valid slope calculated.")

    # E. Prove the logic bug in main.py logging
    # main.py logged: active_perp.timestamp ... active_perp.perp_cvd_slope
    # Does active_perp possess the slope?
    if active_perp.perp_cvd_slope is None:
        print("CONFIRMED: The original 'active_perp' variable remains None. This explains the logs.")
    else:
        print("unexpected: original active_perp was modified?")

if __name__ == "__main__":
    test_perp_injection_logic()
