import pytest
from datetime import datetime, timedelta
from typing import List
import sys
import os
sys.path.append(os.getcwd())
from models.types import Candle, FlowRegime, PatternType
from core.analyzer import Analyzer
from config.settings import FLOW_SLOPE_THRESHOLD

# Helper to create candles
def create_candle(
    close: float, 
    high: float, 
    low: float, 
    spot_slope: float = 0.0, 
    perp_slope: float = 0.0,
    timestamp: datetime = None
) -> Candle:
    if timestamp is None:
        timestamp = datetime.now()
    ts_ms = int(timestamp.timestamp() * 1000)
    return Candle(
        symbol="BTCUSDT",
        timestamp=ts_ms,
        open=close, # Simplified
        high=high,
        low=low,
        close=close,
        volume=1000,
        vwap=close,
        atr=10.0,
        atr_percentile=50.0,
        spot_cvd_slope=spot_slope,
        perp_cvd_slope=perp_slope
    )

class TestFailedBreakoutFix:
    def setup_method(self):
        self.analyzer = Analyzer()

    def test_failed_breakout_should_not_trigger_on_consensus(self):
        """
        Verify that a failed breakout pattern does NOT trigger if there is strong consensus 
        (e.g., Bullish Consensus), because that implies strong flow support, not weak flow.
        """
        candles: List[Candle] = []
        base_time = datetime.now() - timedelta(minutes=100)
        
        # 1. Build history establishes a high
        # High at 20000
        for i in range(50):
            candles.append(create_candle(
                close=19000, 
                high=19500, 
                low=18500, 
                timestamp=base_time + timedelta(minutes=i)
            ))
            
        # Set a strictly defined high in recent history
        candles[-1].high = 20000 
        candles[-1].close = 19800
        
        # 2. Current candle sweeps high but closes below (Rejection)
        # High 20050 (> 20000), Close 19900 (< 20000)
        # Flow: BULLISH CONSENSUS (Strong slopes)
        thresh = FLOW_SLOPE_THRESHOLD + 1.0 # Ensure it's above threshold
        
        current_candle = create_candle(
            close=19900,
            high=20050,
            low=19800,
            spot_slope=thresh,  # Strong Buy
            perp_slope=thresh,  # Strong Buy
            timestamp=base_time + timedelta(minutes=100)
        )
        candles.append(current_candle)
        
        # Sanity check: Ensure regime IS Bullish Consensus
        regime = self.analyzer._determine_regime(candles)
        assert regime == FlowRegime.BULLISH_CONSENSUS, "Setup failed: Regime should be BULLISH_CONSENSUS"
        
        # 3. Analyze
        # Should NOT produce FAILED_BREAKOUT because flow is strong (Consensus)
        alerts = self.analyzer.analyze("BTCUSDT", candles)
        
        failed_breakout_alerts = [a for a in alerts if a.pattern == PatternType.FAILED_BREAKOUT]
        
        # Assertion: We expect 0 alerts for Failed Breakout in this condition
        assert len(failed_breakout_alerts) == 0, f"Found unexpected Failed Breakout alert despite Strong Consensus: {failed_breakout_alerts}"

    def test_failed_breakout_should_trigger_on_neutral_flow(self):
        """
        Verify that it DOES trigger when flow is Neutral (Weak).
        """
        candles: List[Candle] = []
        base_time = datetime.now() - timedelta(minutes=100)
        
        # 1. Build history
        for i in range(50):
            candles.append(create_candle(
                close=19000, 
                high=19500, 
                low=18500, 
                timestamp=base_time + timedelta(minutes=i)
            ))
            
        candles[-1].high = 20000 
        candles[-1].close = 19800
        
        # 2. Current candle sweeps high/close below
        # Flow: NEUTRAL (Zero slopes)
        current_candle = create_candle(
            close=19900,
            high=20050,
            low=19800,
            spot_slope=0.0, 
            perp_slope=0.0,
            timestamp=base_time + timedelta(minutes=100)
        )
        candles.append(current_candle)
        
        # Sanity check
        regime = self.analyzer._determine_regime(candles)
        assert regime == FlowRegime.NEUTRAL
        
        # 3. Analyze
        alerts = self.analyzer.analyze("BTCUSDT", candles)
        failed_breakout_alerts = [a for a in alerts if a.pattern == PatternType.FAILED_BREAKOUT]
        
        
        assert len(failed_breakout_alerts) > 0, "Expected Failed Breakout alert on Neutral flow"

if __name__ == "__main__":
    t = TestFailedBreakoutFix()
    t.setup_method()
    t.test_failed_breakout_should_not_trigger_on_consensus()
    t.setup_method()
    t.test_failed_breakout_should_trigger_on_neutral_flow()
    print("SUCCESS: All tests passed in test_failed_breakout_fix.py")
