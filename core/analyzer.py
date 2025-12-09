from typing import List, Optional
from models.types import Candle, FlowRegime, PatternType, Alert
from config.settings import MIN_ATR_PERCENTILE
import pandas as pd
import numpy as np

class Analyzer:
    def analyze(self, symbol: str, candles: List[Candle]) -> List[Alert]:
        if len(candles) < 20: 
            return []
            
        current_candle = candles[-1]
        
        # 1. Determine Flow Regime
        regime = self._determine_regime(candles)
        
        alerts = []
        
        # 2. Pattern Detection
        
        # A. VWAP Reclaim
        if self._check_vwap_reclaim(candles, regime):
            alerts.append(self._create_alert(symbol, PatternType.VWAP_RECLAIM, regime, current_candle, 80))
            
        # B. Ignition
        if self._check_ignition(candles, regime):
            alerts.append(self._create_alert(symbol, PatternType.IGNITION, regime, current_candle, 70))
            
        return alerts
        
    def _determine_regime(self, candles: List[Candle]) -> FlowRegime:
        # Simple slope calculation over last 5 candles
        lookback = 5
        if len(candles) < lookback:
            return FlowRegime.NEUTRAL
            
        df = pd.DataFrame([vars(c) for c in candles[-lookback:]])
        spot_slope = df['spot_cvd'].iloc[-1] - df['spot_cvd'].iloc[0]
        perp_slope = df['perp_cvd'].iloc[-1] - df['perp_cvd'].iloc[0]
        
        if abs(spot_slope) < 10 and abs(perp_slope) < 10: # Thresholds need calibration
            return FlowRegime.NEUTRAL
            
        if spot_slope > 0 and perp_slope > 0:
            return FlowRegime.CONSENSUS
        elif spot_slope < 0 and perp_slope < 0:
            return FlowRegime.CONSENSUS # bearish consensus
        elif spot_slope > 0 and perp_slope <= 0:
            return FlowRegime.SPOT_DOMINANT
        elif perp_slope > 0 and spot_slope <= 0:
            return FlowRegime.PERP_DOMINANT
            
        return FlowRegime.CONFLICT

    def _check_vwap_reclaim(self, candles: List[Candle], regime: FlowRegime) -> bool:
        """
        Check if price crossed VWAP from below to above (bullish reclaim) 
        and closed above.
        """
        curr = candles[-1]
        prev = candles[-2]
        
        if not curr.vwap or not prev.vwap:
            return False
            
        crossed_up = prev.close < prev.vwap and curr.close > curr.vwap
        
        return crossed_up and regime != FlowRegime.NEUTRAL

    def _check_ignition(self, candles: List[Candle], regime: FlowRegime) -> bool:
        """
        Ignition: Volume expansion + Range expansion after low volatility.
        """
        curr = candles[-1]
        prev = candles[-2]
        
        if not curr.atr or not prev.atr:
            return False
            
        # Check if current range is significantly larger than ATR
        current_range = curr.high - curr.low
        is_expansion = current_range > (curr.atr * 1.5)
        
        # Check volume spike
        vol_spike = curr.volume > (prev.volume * 2)
        
        return is_expansion and vol_spike and regime != FlowRegime.NEUTRAL

    def _create_alert(self, symbol: str, pattern: PatternType, regime: FlowRegime, candle: Candle, score: float) -> Alert:
        return Alert(
            timestamp=candle.timestamp,
            symbol=symbol,
            pattern=pattern,
            score=score,
            flow_regime=regime,
            price=candle.close,
            message=f"{pattern.value} detected in {regime.value}"
        )
