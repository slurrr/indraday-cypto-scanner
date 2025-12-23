import unittest
from core.data_processor import DataProcessor
from core.analyzer import Analyzer
from core.indicators import update_latest_candle, calculate_indicators_full
from models.types import Candle, Trade, TimeframeContext, FlowRegime
from unittest.mock import MagicMock
import copy

class TestRegimeRaceCondition(unittest.TestCase):
    def setUp(self):
        self.mock_sink = MagicMock()
        self.context = TimeframeContext(name="1m", interval_ms=60000)
        self.processor = DataProcessor(self.mock_sink, context=self.context)
        self.analyzer = Analyzer()
        self.processor.tf_ms = 60000

    def test_spot_ahead_of_perp(self):
        """
        Simulate scenario where Spot candle closes (triggering analysis)
        but Perp candle is still active (not yet closed/in history).
        """
        base_ts = 6000000 # Aligned to minute
        
        # 1. Populate some history (synced)
        # Create 40 closed candles for both (Analyzer requires 30+)
        history_spot = []
        history_perp = []
        for i in range(40):
            ts = base_ts + i * 60000
            c_s = Candle(symbol="BTC", timestamp=ts, open=100, high=100, low=100, close=100, volume=100, spot_cvd=10, perp_cvd=0, closed=True)
            c_p = Candle(symbol="BTC", timestamp=ts, open=100, high=100, low=100, close=100, volume=100, spot_cvd=0, perp_cvd=10, closed=True)
            history_spot.append(c_s)
            history_perp.append(c_p)
            
        self.processor.spot_history["BTC"] = history_spot
        self.processor.perp_history["BTC"] = history_perp
        
        # Calculate initial indicators
        calculate_indicators_full(history_spot, context=self.context)
        # Simulate production: Perp history has raw data but NO calculated indicators/slopes
        # calculate_indicators_full(history_perp, context=self.context)
        
        # 2. Start Candle 41 (Timestamp: base_ts + 40*60000)
        target_ts = base_ts + 40 * 60000
        
        # Trade for Spot (starts candle)
        t_s = Trade("BTC", 100, 1, target_ts + 1000, False, "spot")
        self.processor.process_trade(t_s)
        
        # Trade for Perp (starts candle)
        # Perp flow is strongly NEGATIVE (Should be BEARISH)
        t_p = Trade("BTC", 100, 1000, target_ts + 1000, True, "perp") # Sell
        self.processor.process_trade(t_p) 
        
        # Verify both have active candles
        self.assertIn("BTC", self.processor.active_spot_candles)
        self.assertIn("BTC", self.processor.active_perp_candles)
        
        # 3. Close Spot Candle (Triggering Analysis)
        # Trade in NEXT minute for Spot
        t_s_close = Trade("BTC", 100, 1, target_ts + 60000 + 100, False, "spot")
        closed_spot, _ = self.processor.process_trade(t_s_close)
        
        self.assertIsNotNone(closed_spot)
        self.assertEqual(closed_spot.timestamp, target_ts)
        
        # Perp is NOT closed yet (no trade in next minute)
        # So active_perp_candles["BTC"] is still the target candle
        active_perp = self.processor.active_perp_candles["BTC"]
        self.assertEqual(active_perp.timestamp, target_ts)
        
        # 4. Run Analysis (Current Broken Logic)
        # It only grabs closed perp history
        perp_history_closed = self.processor.get_history("BTC", source='perp')
        # Ensure the active candle is NOT in history
        self.assertNotEqual(perp_history_closed[-1].timestamp, target_ts)
        
        # Spot history update (happens in main loop usually)
        # process_trade already added closed_spot to history_spot
        history_spot = self.processor.get_history("BTC", source='spot')
        # Update indicators for Spot
        update_latest_candle(history_spot, context=self.context)
        
        # Analyze
        alerts = self.analyzer.analyze("BTC", history_spot, context=self.context, perp_candles=perp_history_closed)
        
        # Check Regime of the last spot candle
        # It should rely on matched perp candle.
        # Since perp history is lagging, it won't find it.
        # So perp_cvd_slope will be 0 (or default).
        # spot_cvd_slope is small positive (from previous small trades + 1)
        # perp_cvd is -1000 (huge sell). If it saw it, it would be BEARISH/PERP_DOMINANT.
        
        # Capture the result of the "Broken" run
        # We must clone the candle to preserve its state from the first run
        # because the next run will modify the same object reference in the list otherwise.
        last_spot = history_spot[-1]
        last_spot_broken_state = copy.copy(last_spot)
        
        print(f"Broken Logic: Spot Slope: {last_spot_broken_state.spot_cvd_slope}, Perp Slope (Injected): {last_spot_broken_state.perp_cvd_slope}")
        regime_broken = self.analyzer._determine_regime(history_spot, last_spot_broken_state)
        print(f"Broken Regime: {regime_broken}")
        
        # 5. Apply FIX Logic (Simulate main.py change)
        perp_history_fixed = list(perp_history_closed) # shallow copy
        if active_perp.timestamp == target_ts:
             active_perp_fixed = copy.copy(active_perp) 
             print(f"DEBUG: Active Perp CVD before update: {active_perp_fixed.perp_cvd}")
             perp_history_fixed.append(active_perp_fixed)
             
             # FIX: Recalculate indicators for the whole chain to ensure valid cumulative sums
             # update_latest_candle is NOT enough if the history is raw
             calculate_indicators_full(perp_history_fixed, context=self.context)
             
             print(f"DEBUG: Active Perp CUM CVD: {active_perp_fixed.cum_perp_cvd}")
             print(f"DEBUG: Active Perp Slope: {active_perp_fixed.perp_cvd_slope}")
             
        # Run analyze with fixed history
        # IMPORTANT: Use deepcopy of Spot history or just last candle to avoid contaminating previous result?
        # Analyzer writes to 'history[-1]'. 
        
        # We'll just run it. The `last_spot` variable points to the object in `history_spot`.
        # That object will get updated. But `last_spot_broken_state` is a copy we made earlier.
        
        self.analyzer.analyze("BTC", history_spot, context=self.context, perp_candles=perp_history_fixed)
        
        last_spot_fixed = history_spot[-1] 
        # This object IS modified now.
        
        print(f"Fixed Logic: Spot Slope: {last_spot_fixed.spot_cvd_slope}, Perp Slope (Injected): {last_spot_fixed.perp_cvd_slope}")
        
        regime_fixed = self.analyzer._determine_regime(history_spot, last_spot_fixed)
        print(f"Fixed Regime: {regime_fixed}")
        
        # Assertions
        # Expecting Broken (copy) to miss the perp data
        self.assertNotEqual(last_spot_broken_state.perp_cvd_slope, last_spot_fixed.perp_cvd_slope)
        
        # Expecting Fixed to see the huge perp sell
        # Since spot is flat/small buy (+8.2), and perp is huge sell (-192) -> CONFLICT (Opposite signs)
        # The Analyzer prioritizes CONFLICT over DOMINANCE. 
        self.assertEqual(regime_fixed, FlowRegime.CONFLICT)

if __name__ == '__main__':
    unittest.main()
