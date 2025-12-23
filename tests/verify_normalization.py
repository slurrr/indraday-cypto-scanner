import unittest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import sys
import os

# Adjust path to find modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from models.types import Candle, TimeframeContext
from core.indicators import calculate_indicators_full, update_candle_at_index, _calculate_zscore

class TestNormalization(unittest.TestCase):
    def test_zscore_calculation(self):
        # Create a series of numbers
        hist = [10, 12, 10, 12, 10, 12] # Mean=11, Low Std
        val = 15 # Outlier
        z = _calculate_zscore(val, hist)
        print(f"Test Z-Score: {z}")
        self.assertTrue(z > 2.0, "15 should be > 2 sigma from [10,12..]")

    def test_indicators_normalization(self):
        # Create synthetic candles
        candles = []
        base_ts = 1000000
        
        # Phase 1: Flat/Noise Slope (CVD oscillates)
        for i in range(100):
            cvd_val = np.random.normal(0, 10) # Random noise
            c = Candle(
                symbol="TEST", timestamp=base_ts + i*60000,
                open=100, high=101, low=99, close=100, volume=1000,
                closed=True,
                spot_cvd=cvd_val, perp_cvd=cvd_val
            )
            # Accumulate manually for test setup? calculate_indicators_full does it via DataFrame but expects some valid inputs
            # Actually calculate_indicators_full uses 'df['cum_spot_cvd']' which it builds from 'spot_cvd'.
            # wait, calculate_indicators_full BUILDS the DF. Yes.
            candles.append(c)
            
        # Run init
        ctx = TimeframeContext("3m", 180000)
        calculate_indicators_full(candles, context=ctx)
        
        last = candles[-1]
        print(f"Last Candle Z: {last.spot_cvd_slope_z}")
        
        # Check integrity
        self.assertIsNotNone(last.spot_cvd_slope_z)
        self.assertIsNotNone(last.perp_cvd_slope_z)
        
        # Phase 2: Massive Pump (Slope Explosion)
        # Should result in High Z-Score
        for i in range(10):
            # Increasing CVD rapidly
            cvd_val = 1000.0 # Huge positive flow
            c = Candle(
                symbol="TEST", timestamp=base_ts + (100+i)*60000,
                open=100, high=101, low=99, close=100, volume=1000,
                closed=True,
                spot_cvd=cvd_val, perp_cvd=cvd_val
            )
            candles.append(c)
            # Update incrementally
            update_candle_at_index(candles, len(candles)-1, ctx)
            
        pump_candle = candles[-1]
        print(f"Pump Candle Slope: {pump_candle.spot_cvd_slope}")
        print(f"Pump Candle Z: {pump_candle.spot_cvd_slope_z}")
        
        self.assertTrue(pump_candle.spot_cvd_slope > 500)
        self.assertTrue(pump_candle.spot_cvd_slope_z > 2.0, "Pump should trigger high Z-score")

if __name__ == '__main__':
    unittest.main()
