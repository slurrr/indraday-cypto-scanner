import unittest
from unittest.mock import MagicMock
from core.data_processor import DataProcessor
from models.types import Trade, Candle, TimeframeContext
import time

class TestDataProcessorReliability(unittest.TestCase):
    def setUp(self):
        self.mock_sink = MagicMock()
        self.context = TimeframeContext(name="1m", interval_ms=60000)
        self.processor = DataProcessor(self.mock_sink, context=self.context)
        # Override tf_ms to sure match 1m
        self.processor.tf_ms = 60000

    def test_segregation_logic(self):
        """Verify that Spot trades don't update Price, but do update Volume/CVD"""
        base_ts = 1000000
        
        # 1. Start with a Perp trade (sets price)
        t1 = Trade("BTCUSDT", 50000.0, 1.0, base_ts, False, "perp")
        c = self.processor.process_trade(t1)
        
        candle = self.processor.active_candles["BTCUSDT"]
        self.assertEqual(candle.close, 50000.0)
        self.assertEqual(candle.volume, 1.0)
        self.assertEqual(candle.perp_cvd, 1.0)
        
        # 2. Add Spot trade at different price
        t2 = Trade("BTCUSDT", 40000.0, 2.0, base_ts + 100, False, "spot")
        self.processor.process_trade(t2)
        
        # Verify Price ignored, Vol updated
        self.assertEqual(candle.close, 50000.0) # Should NOT be 40000
        self.assertEqual(candle.high, 50000.0)
        self.assertEqual(candle.low, 50000.0)
        self.assertEqual(candle.volume, 3.0) # 1 + 2
        self.assertEqual(candle.spot_cvd, 2.0)
        
        # 3. Add Perp trade updates Price
        t3 = Trade("BTCUSDT", 50100.0, 0.5, base_ts + 200, False, "perp")
        self.processor.process_trade(t3)
        
        self.assertEqual(candle.close, 50100.0)
        self.assertEqual(candle.high, 50100.0)
        self.assertEqual(candle.low, 50000.0)

    def test_fill_gap_from_trades(self):
        """Verify that replaying trades produces expected candles"""
        symbol = "BTCUSDT"
        start_ts = 0
        
        trades = []
        # Minute 0: 3 trades
        trades.append(Trade(symbol, 100, 1, start_ts + 1000, False, "perp"))
        trades.append(Trade(symbol, 101, 1, start_ts + 2000, False, "perp"))
        
        # Minute 1: 2 trades (Gap of 1 minute)
        trades.append(Trade(symbol, 102, 1, start_ts + 60001, False, "perp"))
        trades.append(Trade(symbol, 103, 1, start_ts + 60005, False, "perp"))
        
        self.processor.fill_gap_from_trades(symbol, trades)
        
        # Verify History
        # Minute 0 should be in history (closed by Minute 1 trades)
        hist = self.processor.get_history(symbol)
        self.assertEqual(len(hist), 1)
        
        c0 = hist[0]
        self.assertEqual(c0.timestamp, 0)
        self.assertEqual(c0.close, 101.0)
        self.assertEqual(c0.volume, 2.0)
        
        # Minute 1 should be active
        active = self.processor.active_candles.get(symbol)
        self.assertIsNotNone(active)
        self.assertEqual(active.timestamp, 60000)
        self.assertEqual(active.close, 103.0)
        self.assertEqual(active.volume, 2.0)

if __name__ == '__main__':
    unittest.main()
