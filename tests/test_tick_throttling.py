
import unittest
from unittest.mock import MagicMock, patch
from core.data_processor import DataProcessor
from models.types import Trade
import time

class TestTickThrottling(unittest.TestCase):
    def test_tick_throttling(self):
        # Mock Status Sink (UI)
        mock_ui = MagicMock()
        
        # 3 minute timeframe
        context = MagicMock()
        context.interval_ms = 3 * 60 * 1000 
        
        processor = DataProcessor(status_sink=mock_ui, context=context)
        
        # T0: Start
        t0 = 1000000000000
        
        # Mock time.time() to control throttling
        with patch('core.data_processor.time') as mock_time:
            # Initial state
            mock_time.return_value = 100.0 
            
            # Trade 1: Should trigger tick (first update)
            # Actually, first update: now (100) - last_tick (0) = 100 >= 1.0 -> Tick!
            trade1 = Trade(symbol="BTCUSDT", price=100, quantity=1, timestamp=t0, is_buyer_maker=False, source='spot')
            processor.process_trade(trade1)
            self.assertEqual(mock_ui.tick.call_count, 1, "First trade should trigger tick")
            
            # Trade 2: 0.5s later. Should NOT trigger tick.
            mock_time.return_value = 100.5
            trade2 = Trade(symbol="BTCUSDT", price=101, quantity=1, timestamp=t0 + 500, is_buyer_maker=False, source='spot')
            processor.process_trade(trade2)
            self.assertEqual(mock_ui.tick.call_count, 1, "Trade within 1s should NOT trigger tick")
            
            # Trade 3: 1.1s later (from start). Should trigger tick.
            mock_time.return_value = 101.1
            trade3 = Trade(symbol="BTCUSDT", price=102, quantity=1, timestamp=t0 + 1100, is_buyer_maker=False, source='spot')
            processor.process_trade(trade3)
            self.assertEqual(mock_ui.tick.call_count, 2, "Trade after 1s should trigger tick")
            
            # Trade 4: Candle Close. Should trigger tick immediately.
            # Assuming trade 4 closes the candle (needs large timestamp jump)
            # T0 + 3 mins = T0 + 180000
            mock_time.return_value = 102.0
            trade4 = Trade(symbol="BTCUSDT", price=103, quantity=1, timestamp=t0 + 180000, is_buyer_maker=False, source='spot')
            processor.process_trade(trade4)
            self.assertEqual(mock_ui.tick.call_count, 3, "Candle close should trigger tick")
            
        print("Test finished: Tick throttling detected correctly.")

if __name__ == '__main__':
    unittest.main()
