
import unittest
from datetime import datetime
from models.types import (
    Candle, StateSnapshot, State, PermissionSnapshot, 
    Alert, PatternType, FlowRegime
)
from core.analyzer import Analyzer

class TestPermissionConflict(unittest.TestCase):
    def setUp(self):
        self.analyzer = Analyzer()

    def create_candle(self, timestamp, open, high, low, close, volume, vwap, atr=10.0):
        # Helper to create a basic candle
        return Candle(
            symbol="BTCUSDT",
            timestamp=timestamp,
            open=open, high=high, low=low, close=close,
            volume=volume,
            vwap=vwap,
            atr=atr,
            atr_percentile=50.0,
            closed=True
        )

    def test_bullish_permission_blocks_short_act(self):
        """
        Reproduce the issue:
        Permission is BULLISH.
        Pattern is BEARISH (e.g. Bearson Ignition or VWAP Rejection).
        
        Expected Behavior (after fix): State remains WATCH.
        Current Behavior (bug): State promotes to ACT (SHORT).
        """
        
        # 1. Setup State: WATCH with BULLISH Permission
        state = StateSnapshot(symbol="BTCUSDT", state=State.WATCH)
        state.permission = PermissionSnapshot(
            symbol="BTCUSDT",
            computed_at=1000,
            bias="BULLISH",
            volatility_regime="NORMAL",
            allowed=True,
            reasons=["Price > VWAP"]
        )
        
        # 2. Setup Candles for a BEARISH Ignition
        # This requires a cluster of low vol then a big breakdown candle
        # MIN_HISTORY is 30, so we need at least 30 candles.
        candles = []
        base_ts = 100000
        
        # 30 candles of setup context (flat, low volatility/volume)
        for i in range(30):
            candles.append(self.create_candle(
                timestamp=base_ts + i*60000,
                open=100.0, high=101.0, low=99.0, close=100.0,
                volume=100.0, vwap=100.0, atr=1.0
            ))
            candles[-1].atr_percentile = 10.0 # Low vol cluster
            
        # 31st candle: BIG DROP (Bearish Ignition)
        # Open 100, Close 90 -> distinct drop
        dt_candle = self.create_candle(
            timestamp=base_ts + 30*60000,
            open=100.0, high=100.5, low=89.0, close=90.0,
            volume=5000.0, # Spike
            vwap=99.0, # Price < VWAP
            atr=1.0
        )
        dt_candle.atr_percentile = 90.0
        dt_candle.spot_cvd_slope = -1.0
        dt_candle.perp_cvd_slope = -1.0 # Bearish flow consensus
        candles.append(dt_candle)
        
        # Verify it triggers ignition internally
        # (We rely on Analyzer internals or just checking the output alert)
        
        # 3. Analyze
        alerts = self.analyzer.analyze("BTCUSDT", candles, state=state)
        
        # 4. Check Result
        print(f"\nState logic result: {state.state}")
        print(f"Act Direction: {state.act_direction}")
        print(f"Active Patterns: {state.active_patterns}")
        
        # DEMONSTRATE BUG:
        # If bug exists, this will likely be ACT and SHORT
        # even though Permission was BULLISH.
        
        if state.state == State.ACT and state.act_direction == "SHORT":
            print("BUG REPRODUCED: Promoted to ACT SHORT despite BULLISH permission.")
        else:
            print("No bug or logic didn't trigger pattern.")

        # For the reproduction test pass/fail strictly:
        # We want to assert that currently it DOES fail (promotes to ACT)
        # But for 'unittest' usually we write what we WANT.
        # So I will assert that it stays in WATCH (which will fail now).
        self.assertEqual(state.state, State.WATCH, "Should stay in WATCH because Permission is BULLISH but Signal is Bearish")

if __name__ == '__main__':
    unittest.main()
