
import unittest
from unittest.mock import MagicMock
from core.data_processor import DataProcessor
from models.types import Trade
import time

class TestTickLatency(unittest.TestCase):
    def test_tick_called_only_on_candle_close(self):
        # Mock Status Sink (UI)
        mock_ui = MagicMock()
        
        # 3 minute timeframe
        context = MagicMock()
        context.interval_ms = 3 * 60 * 1000 
        
        processor = DataProcessor(status_sink=mock_ui, context=context)
        
        # T0: Start of minute 0
        t0 = 1000000000000 # arbitrary base time
        
        # Trade 1: Timestamp T0 (Start of 3m bar)
        trade1 = Trade(symbol="BTCUSDT", price=100, quantity=1, timestamp=t0, is_buyer_maker=False, source='spot')
        processor.process_trade(trade1)
        
        # Expectation: First trade creates candle, but DOES NOT close it. 
        # Check if tick() called.
        # Based on code analysis, it should NOT be called.
        self.assertEqual(mock_ui.tick.call_count, 0, "tick() should not be called on first trade (candle open)")
        
        # Trade 2: T0 + 1 minute (Still inside 3m bar)
        trade2 = Trade(symbol="BTCUSDT", price=101, quantity=1, timestamp=t0 + 60000, is_buyer_maker=False, source='spot')
        processor.process_trade(trade2)
        
        self.assertEqual(mock_ui.tick.call_count, 0, "tick() should not be called on trade within bar")
        
        # Trade 3: T0 + 3 minutes (New bar start -> closes old bar)
        trade3 = Trade(symbol="BTCUSDT", price=102, quantity=1, timestamp=t0 + 180000, is_buyer_maker=False, source='spot')
        processor.process_trade(trade3)
        
        # Expectation: Candle closed, tick() called.
        self.assertEqual(mock_ui.tick.call_count, 1, "tick() SHOULD be called when candle closes")
        
        print("Test finished: tick() count matched expectations (0, 0, 1)")

if __name__ == '__main__':
    unittest.main()
