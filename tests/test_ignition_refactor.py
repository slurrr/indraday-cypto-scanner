
import pytest
from datetime import datetime
from models.types import Candle, FlowRegime, PatternType
from core.analyzer import Analyzer
from config.settings import IGNITION_EXPANSION_THRESHOLD_ATR

class TestIgnitionLogic:
    def _create_candle(self, 
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

    def test_ignition_requires_all_conditions(self):
        analyzer = Analyzer()
        
        # 1. Setup Base Conditions
        # Previous candle: Low Volatility (Low ATR Percentile), Low Volume
        prev_candle = self._create_candle(
            open_price=100, high=101, low=99, close=100,
            volume=500, # Low volume
            atr=2.0,
            atr_percentile=10.0, # Low percentile (good)
            spot_slope=0, perp_slope=0
        )
        
        # Current candle: Expansion, Volume Spike, Bullish
        curr_candle = self._create_candle(
            open_price=100, high=110, low=100, close=108, # Big bullish move
            volume=1500, # 3x volume (Spike)
            atr=2.0, 
            atr_percentile=80.0, # Increasing volatility
            vwap=102.0, # Price moving away from VWAP (108 > 102)
            spot_slope=1.0, perp_slope=1.0 # Bullish Consensus
        )
        
        candles = [prev_candle] * 20 # Padding
        candles.append(prev_candle)
        candles.append(curr_candle)
        
        # Verify Positive Case
        alerts = analyzer.analyze("BTCUSDT", candles)
        ignition = [a for a in alerts if a.pattern == PatternType.IGNITION]
        assert len(ignition) == 1, "Should detect valid bullish ignition"
        
    def test_ignition_rejects_conflict_regime(self):
        analyzer = Analyzer()
        prev_candle = self._create_candle(atr_percentile=10, volume=500)
        
        curr_candle = self._create_candle(
            open_price=100, high=110, low=100, close=108,
            volume=1500,
            atr=2.0,
            vwap=102.0,
            spot_slope=-1.0, # Selling
            perp_slope=1.0   # Buying
        )
        # Verify regime is CONFLICT
        # spot=-1 (< -0.5), perp=1 (> 0.5)
        # Analyzer logic: spot_down and perp_up -> CONFLICT (Wait, check analyzer logic)
        # Line 101: elif spot_down and (perp_up or not perp_down): -> SPOT_DOMINANT
        # Wait, if spot is selling (-1) and perp is buying (1), that is SPOT_DOMINANT in current logic?
        # Let's check `_determine_regime` logic again.
        
        # 93: spot_up and perp_up -> BULLISH_CONSENSUS
        # 95: spot_down and perp_down -> BEARISH_CONSENSUS
        # 97: spot_up and (perp_down or not perp_up) -> SPOT_DOMINANT (Spot Buying, Perp Selling/Neutral)
        # 99: perp_up and (spot_down or not spot_up) -> PERP_DOMINANT (Perp Buying, Spot Selling/Neutral)
        
        # Wait, Line 99 catches Perp Up + Spot Down. So it returns PERP_DOMINANT.
        # Line 101: spot_down ... this is unreachable if 99 caught it?
        # 99: `perp_up` is True. `spot_down` is True. So `perp_up and ...` is True. Returns PERP_DOMINANT.
        
        # User requirement for BEARISH Ignition: "regime in {BEARISH_CONSENSUS, PERP_DOMINANT}"
        # User requirement for BULLISH Ignition: "regime in {BULLISH_CONSENSUS, SPOT_DOMINANT}"
        
        # My test case: Bullish Candle (100 -> 108).
        # Flow: Spot Selling, Perp Buying -> PERP_DOMINANT.
        # Is PERP_DOMINANT valid for BULLISH Ignition?
        # "Bullish ignition requires ... regime in {BULLISH_CONSENSUS, SPOT_DOMINANT}"
        # So PERP_DOMINANT should be REJECTED for Bullish Ignition.
        
        candles = [prev_candle] * 20 + [prev_candle, curr_candle]
        alerts = analyzer.analyze("BTCUSDT", candles)
        ignition = [a for a in alerts if a.pattern == PatternType.IGNITION]
        
        assert len(ignition) == 0, "Should reject Bullish Ignition in PERP_DOMINANT regime (need Spot support)"

    def test_ignition_requires_low_prior_volatility(self):
        analyzer = Analyzer()
        
        # Previous candle has HIGH volatility already
        prev_candle = self._create_candle(
            atr_percentile=80.0, # High volatility
            volume=500
        )
        
        curr_candle = self._create_candle(
            open_price=100, high=110, low=100, close=108,
            volume=1500,
            atr=2.0,
            spot_slope=1.0, perp_slope=1.0
        )
        
        candles = [prev_candle] * 20 + [prev_candle, curr_candle]
        
        alerts = analyzer.analyze("BTCUSDT", candles)
        ignition = [a for a in alerts if a.pattern == PatternType.IGNITION]
        
        assert len(ignition) == 0, "Should reject ignition if prior volatility was high"

    def test_ignition_enforces_direction_alignment(self):
        analyzer = Analyzer()
        prev_candle = self._create_candle(atr_percentile=10, volume=500)
        
        # Bearish Candle (Close < Open) but Bullish Consensus
        curr_candle = self._create_candle(
            open_price=108, high=110, low=100, close=100, # Bearish candle
            volume=1500,
            atr=2.0,
            spot_slope=1.0, perp_slope=1.0 # Bullish Consensus
        )
        
        candles = [prev_candle] * 20 + [prev_candle, curr_candle]
        
        alerts = analyzer.analyze("BTCUSDT", candles)
        ignition = [a for a in alerts if a.pattern == PatternType.IGNITION]
        
        assert len(ignition) == 0, "Should reject Bearish Candle in Bullish Regime"
