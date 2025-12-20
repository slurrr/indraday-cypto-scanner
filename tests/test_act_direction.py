
import pytest
from datetime import datetime
from models.types import Candle, FlowRegime, PatternType, StateSnapshot, State, PermissionSnapshot
from core.analyzer import Analyzer
from config.settings import ACT_ELIGIBLE_PATTERNS

def create_candle(
           open_price=100.0, 
           high=105.0, 
           low=95.0, 
           close=102.0, 
           volume=1000.0, 
           atr=2.0, 
           atr_percentile=50.0,
           vwap=100.0,
           spot_slope=1.0,
           perp_slope=1.0,
           timestamp=None):
    if timestamp is None:
        timestamp = datetime.now().timestamp()
    return Candle(
        symbol="BTCUSDT",
        timestamp=int(timestamp * 1000),
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=volume,
        vwap=vwap,
        atr=atr,
        atr_percentile=atr_percentile,
        spot_cvd_slope=spot_slope,
        perp_cvd_slope=perp_slope
    )

def test_act_direction_assignment():
    analyzer = Analyzer()
    
    # 1. Setup Ignition Pattern Conditions
    # Previous candle: Low Volatility (Low ATR Percentile), Low Volume
    prev_candle = create_candle(
        open_price=100, high=101, low=99, close=100,
        volume=500, # Low volume
        atr=2.0,
        atr_percentile=10.0, # Low percentile (good)
        spot_slope=0, perp_slope=0
    )
    
    # Current candle: Expansion, Volume Spike, Bullish
    curr_candle = create_candle(
        open_price=100, high=110, low=100, close=108, # Big bullish move (LONG)
        volume=1500, # 3x volume (Spike)
        atr=2.0, 
        atr_percentile=80.0, # Increasing volatility
        vwap=102.0, # Price moving away from VWAP (108 > 102)
        spot_slope=2.0, perp_slope=2.0 # Bullish Consensus
    )
    
    candles = [prev_candle] * 40
    candles.append(prev_candle)
    candles.append(curr_candle)
    
    # 2. Setup State: WATCH -> ACT requires permission
    state = StateSnapshot(
        symbol="BTCUSDT",
        state=State.WATCH,
        permission=PermissionSnapshot(
            symbol="BTCUSDT", computed_at=0, bias="BULLISH", volatility_regime="NORMAL", allowed=True
        )
    )
    
    # 3. Analyze
    alerts = analyzer.analyze("BTCUSDT", candles, state=state)
    
    print(f"State after analyze: {state.state}")
    print(f"Act Reason: {state.act_reason}")
    print(f"Act Direction: {state.act_direction}")
    
    # 4. Verify
    assert state.state == State.ACT, "Should promote to ACT"
    assert state.act_reason == PatternType.IGNITION.value, "Reason should be IGNITION"
    assert state.act_direction == "LONG", f"Direction should be LONG, got {state.act_direction}"
    
    print("SUCCESS: Act direction assigned correctly.")

if __name__ == "__main__":
    test_act_direction_assignment()
