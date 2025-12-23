import unittest
from unittest.mock import MagicMock
from core.data_processor import DataProcessor
from core.analyzer import Analyzer
from models.types import Trade, Candle, TimeframeContext, FlowRegime

class TestDataProcessorSegregation(unittest.TestCase):
    def setUp(self):
        self.mock_sink = MagicMock()
        self.context = TimeframeContext(name="1m", interval_ms=60000)
        self.processor = DataProcessor(self.mock_sink, context=self.context)
        # Force tf_ms
        self.processor.tf_ms = 60000

    def test_strict_separation(self):
        """Verify Spot and Perp trades go into separate buckets without mixing."""
        base_ts = 1000000
        
        # 1. Process Spot Trade
        t_spot = Trade("BTCUSDT", 40000.0, 1.0, base_ts, False, "spot")
        self.processor.process_trade(t_spot)
        
        # Verify Spot Active, Perp Empty
        self.assertIn("BTCUSDT", self.processor.active_spot_candles)
        self.assertNotIn("BTCUSDT", self.processor.active_perp_candles)
        
        spot_c = self.processor.active_spot_candles["BTCUSDT"]
        self.assertEqual(spot_c.close, 40000.0)
        self.assertEqual(spot_c.volume, 1.0)
        self.assertEqual(spot_c.spot_cvd, 1.0) # Delta +1
        self.assertEqual(spot_c.perp_cvd, 0.0) # Should be 0
        
        # 2. Process Perp Trade
        t_perp = Trade("BTCUSDT", 50000.0, 0.5, base_ts + 100, True, "perp") # BuyerMaker=True -> Sell -> Delta -0.5
        self.processor.process_trade(t_perp)
        
        # Verify Perp Active
        self.assertIn("BTCUSDT", self.processor.active_perp_candles)
        
        perp_c = self.processor.active_perp_candles["BTCUSDT"]
        self.assertEqual(perp_c.close, 50000.0) # Price is Perp Price
        self.assertEqual(perp_c.volume, 0.5)
        self.assertEqual(perp_c.perp_cvd, -0.5)
        self.assertEqual(perp_c.spot_cvd, 0.0)
        
        # Verify Spot Candle UNTOUCHED by Perp trade
        self.assertEqual(spot_c.close, 40000.0)
        self.assertEqual(spot_c.volume, 1.0) 

    def test_dual_close(self):
        """Verify that candles close into correct histories."""
        base_ts = 1000000
        
        # Minute 0 Trades
        t_spot = Trade("BTCUSDT", 40000.0, 1.0, base_ts + 1000, False, "spot")
        t_perp = Trade("BTCUSDT", 50000.0, 1.0, base_ts + 2000, False, "perp")
        
        self.processor.process_trade(t_spot)
        self.processor.process_trade(t_perp)
        
        # Minute 1 Trades (Trigger Close)
        t_spot_new = Trade("BTCUSDT", 40100.0, 1.0, base_ts + 60001, False, "spot")
        t_perp_new = Trade("BTCUSDT", 50100.0, 1.0, base_ts + 60001, False, "perp")
        
        # 1. Close Spot
        closed_s, closed_p = self.processor.process_trade(t_spot_new)
        self.assertIsNotNone(closed_s)
        self.assertIsNone(closed_p) # Only Spot processed
        
        hist_spot = self.processor.get_history("BTCUSDT", source='spot')
        self.assertEqual(len(hist_spot), 1)
        self.assertEqual(hist_spot[0].close, 40000.0)
        
        # 2. Close Perp
        closed_s, closed_p = self.processor.process_trade(t_perp_new)
        self.assertIsNone(closed_s)
        self.assertIsNotNone(closed_p)
        
        hist_perp = self.processor.get_history("BTCUSDT", source='perp')
        self.assertEqual(len(hist_perp), 1)
        self.assertEqual(hist_perp[0].close, 50000.0)

class TestAnalyzerFlowSegregation(unittest.TestCase):
    def setUp(self):
        self.analyzer = Analyzer()
        
    def test_flow_regime_integration(self):
        """Verify Analyzer correctly combines Spot and Perp logic via injection."""
        base_ts = 1000
        # Setup: Spot and Perp candles
        c_spot = Candle(
            symbol="BTCUSDT", timestamp=base_ts, 
            open=100, high=110, low=90, close=105, volume=1000,
            spot_cvd_slope=2.0, perp_cvd_slope=None # Spot initially has only spot slope
        )
        c_spot.atr_percentile = 50 
        
        c_perp = Candle(
            symbol="BTCUSDT", timestamp=base_ts,
            open=101, high=111, low=91, close=106, volume=500,
            spot_cvd_slope=None, perp_cvd_slope=2.0 # Perp has perp slope
        )
        
        # Test 1: Injection Logic via analyze()
        # Passing perp_candles should inject the slope into c_spot
        # We need efficient list for candles
        candles = [c_spot] * 35 # Min history
        candles[-1] = c_spot # Ensure object identity
        
        perp_candles = [c_perp] * 35
        perp_candles[-1] = c_perp
        
        # Call analyze (which triggers injection)
        self.analyzer.analyze("BTCUSDT", candles, perp_candles=perp_candles)
        
        # Verify INJECTION happened on the spot candle
        self.assertEqual(c_spot.perp_cvd_slope, 2.0)
        
        # Test 2: Regime Determination with Injected Data
        # Now c_spot has both slopes (2.0 and 2.0)
        regime = self.analyzer._determine_regime(candles, c_spot)
        self.assertEqual(regime, FlowRegime.BULLISH_CONSENSUS)
        
        # Test 3: Verify without injection (Clean object)
        c_spot_clean = Candle(
            symbol="BTCUSDT", timestamp=base_ts, 
            open=100, high=110, low=90, close=105, volume=1000,
            spot_cvd_slope=2.0, perp_cvd_slope=None
        )
        c_spot_clean.atr_percentile = 50
        
        regime_clean = self.analyzer._determine_regime([c_spot_clean], c_spot_clean)
        # Spot=2.0, Perp=0.0 (default) -> Spot Dominant
        self.assertEqual(regime_clean, FlowRegime.SPOT_DOMINANT)

if __name__ == '__main__':
    unittest.main()
