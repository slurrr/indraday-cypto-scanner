import unittest
from core.indicators import calculate_indicators_full, update_latest_candle
from models.types import Candle, TimeframeContext
import copy

class TestIncrementalIndicators(unittest.TestCase):
    def setUp(self):
        self.context = TimeframeContext(name="3m", interval_ms=180000)

    def test_incremental_vs_full(self):
        # Create a synthetic history of candles
        candles = []
        base_ts = 1000000000000
        
        # Scenario: 50 candles
        for i in range(50):
            c = Candle(
                symbol="BTCUSDT",
                timestamp=base_ts + (i * 180000),
                open=100.0 + i,
                high=105.0 + i,
                low=95.0 + i,
                close=102.0 + i,
                volume=1000.0 + (i * 10),
                spot_cvd=50.0,
                perp_cvd=-20.0,
                closed=True 
            )
            candles.append(c)

        # 1. Run FULL calculation on copy A
        history_full = copy.deepcopy(candles)
        calculate_indicators_full(history_full, context=self.context)
        
        # 2. Run INCREMENTAL calculation on copy B
        # We start by initializing the first candle fully (or manually) 
        # because incremental needs a "prev".
        # Actually `update_latest_candle` handles the first candle too.
        
        history_inc = []
        for i, c in enumerate(candles):
            # simulate adding one by one
            # We need deepcopies so we don't mutate the original list used for full
            c_copy = copy.deepcopy(c)
            history_inc.append(c_copy)
            update_latest_candle(history_inc, context=self.context)
            
        # 3. Compare Results
        for i in range(50):
            c_full = history_full[i]
            c_inc = history_inc[i]
            
            # VWAP
            self.assertAlmostEqual(c_full.vwap, c_inc.vwap, places=5, msg=f"VWAP mismatch at {i}")
            
            # ATR
            # Note: SMA ATR needs a warmup.
            if i >= 13: 
                self.assertAlmostEqual(c_full.atr, c_inc.atr, places=5, msg=f"ATR mismatch at {i}")
                
            # Slopes
            self.assertAlmostEqual(c_full.vwap_slope, c_inc.vwap_slope, places=5, msg=f"VWAP Slope mismatch at {i}")
            
            # Cumulative State
            self.assertAlmostEqual(c_full.cum_pv, c_inc.cum_pv, places=5, msg=f"Cum PV mismatch at {i}")
            
    def test_chain_repair(self):
        """Verify logic for repairing the chain after a retrospective update (reconciliation)"""
        candles = []
        base_ts = 1000000000000
        for i in range(10):
            c = Candle(
                symbol="BTCUSDT",
                timestamp=base_ts + (i * 180000),
                open=100.0, high=110.0, low=90.0, close=100.0, volume=100.0,
                spot_cvd=10, perp_cvd=10
            ) 
            candles.append(c)
            
        # Initial incremental build
        for i in range(len(candles)):
            update_latest_candle(candles[:i+1])
            
        # Initial state checks
        original_vwap = candles[-1].vwap
        
        # Modification: Retrospectively change candle [-2] (second to last)
        # This simulates `reconcile_candle` updating history[-1] (since get_history returns closed only)
        # Let's say we modify index 8 (9th candle), and rebuild index 8 and 9
        
        target_idx = 8
        candles[target_idx].close = 200.0 # drastic change
        candles[target_idx].volume = 5000.0
        
        # Manual Repair Flow (as in main.py)
        # 1. Update the modified candle itself
        update_latest_candle(candles[:target_idx+1])
        
        # 2. Update the subsequent candle (chain reaction)
        update_latest_candle(candles[:target_idx+2])
        
        new_vwap = candles[-1].vwap
        self.assertNotEqual(original_vwap, new_vwap, "VWAP should have updated after chain repair")
        
        # Verify against ground truth (full recalc)
        truth_candles = copy.deepcopy(candles)
        calculate_indicators_full(truth_candles)
        
        self.assertAlmostEqual(candles[-1].vwap, truth_candles[-1].vwap, places=5, msg="Repaired chain should match full recalc")

if __name__ == '__main__':
    unittest.main()
