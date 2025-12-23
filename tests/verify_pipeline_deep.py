
import sys
import os
import unittest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from models.types import Candle, Trade
from core.data_processor import DataProcessor
from core.indicators import update_indicators_from_index

class TestPipelineDeep(unittest.TestCase):
    def test_reconciliation_data_retention(self):
        """
        Verify that when a candle is reconciled (overwritten by API data),
        the indicators (CVD, Slope) are correctly re-calculated and NOT lost.
        """
        print("\n--- Starting Deep Pipeline Verification ---")
        
        # 1. Setup Processor
        dp = DataProcessor(lambda x: None)
        symbol = "BTCUSDT"
        
        # 2. Simulate History (5 candles)
        # We need enough history to calculate a slope (window=5 usually)
        start_ts = 1000000
        candles = []
        cum_cvd = 0.0
        
        # Create 5 "Live" candles (from trades)
        for i in range(5):
            ts = start_ts + (i * 60000) # 1m intervals
            # Simulate a candle with known CVD
            # Trade-based CVD: +100 per candle
            c = Candle(
                symbol=symbol,
                timestamp=ts,
                open=100+i, high=110+i, low=90+i, close=105+i,
                volume=1000,
                spot_cvd=100.0, # Explicit CVD
            )
            candles.append(c)
            
        # Manually initialize history in processor for test
        dp.spot_history[symbol] = candles
        
        # 3. Initial Indicator Calculation
        # Run update from index 0 to ensure baseline is correct
        update_indicators_from_index(dp.spot_history[symbol], 0)
        
        last_candle = dp.spot_history[symbol][-1]
        print(f"Initial State: Last Candle CVD={last_candle.spot_cvd}, Slope={last_candle.spot_cvd_slope}")
        
        # Check baseline
        self.assertEqual(last_candle.spot_cvd, 100.0)
        self.assertIsNotNone(last_candle.spot_cvd_slope)
        self.assertNotEqual(last_candle.spot_cvd_slope, 0.0) # Should be positive slope
        
        # 4. SIMULATE RECONCILIATION
        # The 'API' returns a candle for the LAST timestamp.
        # Crucially, let's say the API approximation is slightly different (e.g. 90 instead of 100)
        # But NOT zero.
        api_candle = Candle(
            symbol=symbol,
            timestamp=last_candle.timestamp, # Same TS
            open=104, high=114, low=94, close=109, # Slightly diff price
            volume=1050,
            spot_cvd=90.0, # Approximated CVD
        )
        
        print(f"Reconciling with API Candle: CVD={api_candle.spot_cvd} (Approximated)")
        
        # 5. Overwrite in History (Main Loop Logic)
        dp.update_history_candle(symbol, api_candle, 'spot')
        
        # Verify overwrite happened (raw data)
        updated_candle = dp.spot_history[symbol][-1]
        self.assertEqual(updated_candle.spot_cvd, 90.0) # Should match API
        self.assertIsNone(updated_candle.spot_cvd_slope) # Indicators not calc'd yet
        
        # 6. Trigger Re-Calculation (Main Loop Logic)
        # We update from the index of the reconciled candle
        idx = len(dp.spot_history[symbol]) - 1
        print(f"Triggering Indicator Update from index {idx}...")
        update_indicators_from_index(dp.spot_history[symbol], idx)
        
        # 7. FINAL VERIFICATION
        final_candle = dp.spot_history[symbol][-1]
        print(f"Final State: CVD={final_candle.spot_cvd}, Slope={final_candle.spot_cvd_slope}")
        
        # Assertions
        # 1. CVD must be the API value (90.0)
        self.assertEqual(final_candle.spot_cvd, 90.0)
        
        # 2. Cumulative CVD must account for history + new value
        # Previous cum was roughly 400. New should be ~490.
        prev_cum = dp.spot_history[symbol][-2].cum_spot_cvd
        expected_cum = prev_cum + 90.0
        self.assertAlmostEqual(final_candle.cum_spot_cvd, expected_cum)
        
        # 3. SLOPE MUST BE PRESENT AND VALID
        self.assertIsNotNone(final_candle.spot_cvd_slope)
        self.assertNotEqual(final_candle.spot_cvd_slope, 0.0, "Slope should NOT be zero after reconciliation!")
        
        print(">>> SUCCESS: Data retention and re-calculation verified.")

    def test_initialization_slope(self):
        """
        Verify that fetching history and calculating indicators produces valid slopes immediately.
        """
        print("\n--- Testing Initialization Logic ---")
        dp = DataProcessor(lambda x: None)
        symbol = "BTCUSDT"
        
        # Simulate 100 historical candles (fetched from API)
        # We simulate them as having valid CVD data
        candles = []
        for i in range(100):
            ts = 1000000 + (i * 60000)
            c = Candle(
                symbol=symbol,
                timestamp=ts,
                open=100+i, high=110+i, low=90+i, close=105+i,
                volume=1000,
                spot_cvd=100.0, # API calculated this
            )
            candles.append(c)
            
        # Simulate main.py initialization flow
        dp.spot_history[symbol] = candles
        # Run FULL indicator calculation
        update_indicators_from_index(dp.spot_history[symbol], 0)
        
        last_candle = dp.spot_history[symbol][-1]
        print(f"Init Last Candle Slope: {last_candle.spot_cvd_slope}")
        
        self.assertIsNotNone(last_candle.spot_cvd_slope)
        self.assertNotEqual(last_candle.spot_cvd_slope, 0.0)


if __name__ == '__main__':
    unittest.main()
