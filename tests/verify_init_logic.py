
import sys
import os
import unittest
from unittest.mock import MagicMock
import pandas as pd
import time

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.data_processor import DataProcessor
from models.types import Candle, TimeframeContext
from core.indicators import calculate_indicators_full
from data.binance_client import BinanceClient

class TestInitLogic(unittest.TestCase):
    def test_init_indicators_persistence(self):
        """
        Verify that init_hybrid style logic persists indicators on the DataProcessor's history.
        """
        print("\n--- Testing Init Logic Persistence ---")
        
        # 1. Setup
        dp = DataProcessor(lambda x: None)
        symbol = "BTCUSDT"
        ctx = TimeframeContext("3m", 180000)
        
        # 2. Simulate Fetched History (Raw)
        candles = []
        start_ts = 1000000
        for i in range(150): # > 120
            ts = start_ts + (i * 60000 * 3)
            c = Candle(
                symbol=symbol,
                timestamp=ts,
                open=100.0+i, high=110.0+i, low=90.0+i, close=105.0+i,
                volume=1000.0,
                spot_cvd=100.0,
                perp_cvd=100.0,
                closed=True 
            )
            # Default indicators are None
            self.assertIsNone(c.atr_percentile)
            candles.append(c)
            
        # 3. Simulate "init_history"
        dp.init_history({symbol: candles}, source='spot')
        
        # Verify stored
        stored = dp.spot_history[symbol]
        self.assertEqual(len(stored), 150)
        
        # 4. Simulate Loop from main.py
        print("Running calculate_indicators_full...")
        for sym, hist in dp.spot_history.items():
            if hist:
                calculate_indicators_full(hist, context=ctx)
                
        # 5. Verify Results
        last_candle = dp.spot_history[symbol][-1]
        print(f"Last Candle Slope: {last_candle.spot_cvd_slope}")
        print(f"Last Candle ATR%: {last_candle.atr_percentile}")
        
        self.assertIsNotNone(last_candle.spot_cvd_slope)
        self.assertNotEqual(last_candle.spot_cvd_slope, 0.0)
        self.assertIsNotNone(last_candle.atr_percentile)
        
        print(">>> SUCCESS: Indicators persisted.")

if __name__ == '__main__':
    unittest.main()
