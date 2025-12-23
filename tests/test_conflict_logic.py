
import unittest
from unittest.mock import MagicMock, patch
from models.types import (
    Candle, StateSnapshot, State, PermissionSnapshot, 
    Alert, PatternType, FlowRegime, ExecutionType
)
from core.analyzer import Analyzer

class TestPermissionConflictFixed(unittest.TestCase):
    def setUp(self):
        self.analyzer = Analyzer()

    def create_candle(self, timestamp, close=100.0):
        return Candle(
            symbol="BTCUSDT",
            timestamp=timestamp,
            open=close, high=close, low=close, close=close,
            volume=1000.0,
            closed=True
        )

    def test_promotion_blocked_by_bias(self):
        """
        Test that WATCH -> ACT promotion is blocked if Permission Bias conflicts with Pattern Direction.
        """
        # 1. Setup State: WATCH with BULLISH Permission
        state = StateSnapshot(symbol="BTCUSDT", state=State.WATCH)
        state.permission = PermissionSnapshot(
            symbol="BTCUSDT",
            computed_at=1000,
            bias="BULLISH",
            volatility_regime="NORMAL",
            allowed=True,
            reasons=["Bullish Bias"]
        )
        
        # 2. Mock internals to simulate a BEARISH IGNITION
        # We patch _check_ignition to return True
        # We patch _calculate_score to return 100
        # We patch _bullish/bearish flow checks if needed (but analyze logic generates direction)
        
        with patch.object(self.analyzer, '_check_ignition', return_value=True), \
             patch.object(self.analyzer, '_calculate_score', return_value=100.0), \
             patch.object(self.analyzer, '_determine_regime', return_value=FlowRegime.BEARISH_CONSENSUS), \
             patch.object(self.analyzer, '_check_vwap_reclaim', return_value=False), \
             patch.object(self.analyzer, '_check_post_impulse_pullback', return_value=False), \
             patch.object(self.analyzer, '_check_trap', return_value=False), \
             patch.object(self.analyzer, '_check_failed_breakout', return_value=False):
             
             # Create dummy candles (enough history)
             candles = [self.create_candle(i*60000) for i in range(30)]
             # Make last candle "Red" so Ignition infers SHORT direction
             # Ignition Logic: Green -> LONG, Red -> SHORT
             candles[-1].open = 100.0
             candles[-1].close = 90.0 
             
             # Run Analyze
             alerts = self.analyzer.analyze("BTCUSDT", candles, state=state)
             
             # 3. Verify
             # Logic: Ignition (Bearish) -> SHORT direction.
             # Permission: BULLISH.
             # Conflict! Should stay in WATCH.
             
             print(f"State: {state.state}")
             print(f"Reason: {state.reasons[-1] if state.reasons else 'None'}")
             
             self.assertEqual(state.state, State.WATCH)
             self.assertIn("Blocked ACT promotion", state.reasons[-1])

    def test_demotion_on_bias_flip(self):
        """
        Test that ACT -> WATCH demotion occurs if Permission Bias flips to conflict.
        """
        # 1. Setup State: ACT (LONG)
        state = StateSnapshot(
            symbol="BTCUSDT", 
            state=State.ACT, 
            act_direction="LONG", 
            entered_at=29 * 60000
        )
        # Permission flips to BEARISH
        state.permission = PermissionSnapshot(
            symbol="BTCUSDT",
            computed_at=30 * 60000,
            bias="BEARISH", # CONFLICT with LONG
            volatility_regime="NORMAL",
            allowed=True,
            reasons=["Bearish Flip"]
        )
        
        # 2. Run Analyze (no patterns needed, just state maintenance)
        candles = [self.create_candle(i*60000) for i in range(30)]
        
        self.analyzer.analyze("BTCUSDT", candles, state=state)
        
        print(f"State after flip: {state.state}")
        print(f"Reason: {state.reasons[-1] if state.reasons else 'None'}")
        
        self.assertEqual(state.state, State.WATCH)
        self.assertIn("Bias Conflict", state.reasons[-1])

if __name__ == '__main__':
    unittest.main()
