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
            score = self._calculate_score(PatternType.VWAP_RECLAIM, candles, regime)
            if score >= 50:
                alerts.append(self._create_alert(symbol, PatternType.VWAP_RECLAIM, regime, current_candle, score))
            
        # B. Ignition
        if self._check_ignition(candles, regime):
            score = self._calculate_score(PatternType.IGNITION, candles, regime)
            if score >= 50:
                alerts.append(self._create_alert(symbol, PatternType.IGNITION, regime, current_candle, score))

        # C. Post-Impulse Pullback
        if self._check_post_impulse_pullback(candles, regime):
            score = self._calculate_score(PatternType.PULLBACK, candles, regime)
            if score >= 50:
                alerts.append(self._create_alert(symbol, PatternType.PULLBACK, regime, current_candle, score))

        # D. Trap (Top/Bottom)
        if self._check_trap(candles, regime):
            score = self._calculate_score(PatternType.TRAP, candles, regime)
            if score >= 50:
                alerts.append(self._create_alert(symbol, PatternType.TRAP, regime, current_candle, score))
                
        # E. Failed Breakout
        if self._check_failed_breakout(candles, regime):
            # Failed Breakout is often a subset of Trap, but we detect it distinctly if needed
            # For MVP prevent duplicate alerts if Trap already fired? 
            # We'll just append it for now, user can filter.
            score = self._calculate_score(PatternType.FAILED_BREAKOUT, candles, regime)
            if score >= 50:
                alerts.append(self._create_alert(symbol, PatternType.FAILED_BREAKOUT, regime, current_candle, score))
            
        return alerts
        
    def _determine_regime(self, candles: List[Candle]) -> FlowRegime:
        current = candles[-1]
        
        # Volatility Gating
        if current.atr_percentile is not None and current.atr_percentile < MIN_ATR_PERCENTILE:
            return FlowRegime.NEUTRAL
            
        # Use slopes
        spot_slope = current.spot_cvd_slope if current.spot_cvd_slope is not None else 0
        perp_slope = current.perp_cvd_slope if current.perp_cvd_slope is not None else 0
        
        # Thresholds (MVP: arbitary simple check > 0)
        # Real system needs more robust slope threshold, e.g. std dev of slope
        threshold_spot = 0 # Placeholder, slope is often very small or large
        threshold_perp = 0
        
        # Normalize slopes to avoid noise?
        # For MVP we stick to sign agreement
        
        spot_up = spot_slope > 0
        spot_down = spot_slope < 0
        perp_up = perp_slope > 0
        perp_down = perp_slope < 0
        
        if abs(spot_slope) < 1e-9 and abs(perp_slope) < 1e-9:
             return FlowRegime.NEUTRAL

        if spot_up and perp_up:
            return FlowRegime.CONSENSUS
        elif spot_down and perp_down:
            return FlowRegime.CONSENSUS # Bearish consensus
        elif spot_up and (perp_down or not perp_up):
            return FlowRegime.SPOT_DOMINANT
        elif perp_up and (spot_down or not spot_up):
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
        crossed_down = prev.close > prev.vwap and curr.close < curr.vwap
        
        return (crossed_up or crossed_down) and regime != FlowRegime.NEUTRAL

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

    def _check_post_impulse_pullback(self, candles: List[Candle], regime: FlowRegime) -> bool:
        """
        Pattern 2: Post-Impulse Pullback.
        Significant move -> Low vol pullback -> Touch VWAP?
        """
        # Logic: 
        # 1. Impulse: One of the last 5 candles had range > 2 * ATR
        # 2. Pullback: Current candle is small range (low vol)
        # 3. Location: Near VWAP
        
        curr = candles[-1]
        if not curr.atr or not curr.vwap:
            return False
            
        # 1. Impulse in last 10 candles
        has_impulse = False
        for c in candles[-10:-1]:
            rng = c.high - c.low
            if c.atr and rng > (c.atr * 2.0):
                has_impulse = True
                break
        
        if not has_impulse:
            return False
            
        # 2. Current candle low volatility compression
        current_range = curr.high - curr.low
        is_compressed = current_range < (curr.atr * 0.8) # Arbitrary factor
        
        # 3. Proximity to VWAP (e.g. within 0.1% or ATR based distance)
        dist_to_vwap = abs(curr.close - curr.vwap)
        near_vwap = dist_to_vwap < (curr.atr * 0.5)
        
        return has_impulse and is_compressed and near_vwap

    def _check_trap(self, candles: List[Candle], regime: FlowRegime) -> bool:
        """
        Pattern 3: Trap / Stop Run.
        Sweep High/Low -> Reversal -> Divergence
        """
        # MVP Simple Trap:
        # 1. New Session High/Low made in last 3 candles
        # 2. Current Close rejected back into range
        # 3. CVD Divergence (Price Up, CVD Down etc or Flow Conflict)
        
        curr = candles[-1]
        
        # Session High/Low (last 60? candles)
        lookback = 60
        history = candles[-lookback:] if len(candles) > lookback else candles
        
        recent_high = max(c.high for c in history[:-1])
        recent_low = min(c.low for c in history[:-1])
        
        # Bull Trap (Sweep High)
        swept_high = curr.high > recent_high
        closed_below = curr.close < recent_high
        
        # Bear Trap (Sweep Low)
        swept_low = curr.low < recent_low
        closed_above = curr.close > recent_low
        
        is_trap = (swept_high and closed_below) or (swept_low and closed_above)
        
        # Divergence / Conflict validation
        # If price made new high but Flow is spotting selling...
        
        return is_trap and (regime == FlowRegime.CONFLICT or regime == FlowRegime.PERP_DOMINANT or regime == FlowRegime.SPOT_DOMINANT)

    def _check_failed_breakout(self, candles: List[Candle], regime: FlowRegime) -> bool:
        """
        Pattern 5: Failed Breakout.
        Intent: Identify structural breakout failure without explicit stop-run trap characteristics.
        Characteristics:
        - Break beyond key level (Session High/Low)
        - Immediate rejection back into range (Close inside)
        - Weak or absent flow follow-through (Neutral or Weak Consensus, NOT intense conflict)
        """
        curr = candles[-1]
        
        # Session High/Low (last 60 candles)
        lookback = 60
        history = candles[-lookback:] if len(candles) > lookback else candles
        
        recent_high = max(c.high for c in history[:-1])
        recent_low = min(c.low for c in history[:-1])
        
        # Bullish Breakout Failure
        swept_high = curr.high > recent_high
        closed_below = curr.close < recent_high
        
        # Bearish Breakout Failure
        swept_low = curr.low < recent_low
        closed_above = curr.close > recent_low
        
        is_breakout_fail = (swept_high and closed_below) or (swept_low and closed_above)
        
        # Distinction from Trap:
        # Trap has STRONG flow disagreement (Conflict/Perp vs Spot divergence).
        # Failed Breakout has WEAK / NO flow backing.
        # e.g. Price breaks out, but flow remains Neutral or very weak, indicating no conviction.
        
        is_weak_flow = regime == FlowRegime.NEUTRAL or regime == FlowRegime.CONSENSUS # Weak consensus implies just general drift?
        
        # Refinement: If it's CONSENSUS, it might be a real breakout that just wicked.
        # Failed breakout usually lacks the EXPLOSIVE flow of a real breakout.
        # Let's check volume or flow magnitude?
        # For MVP, SPEC says: "Weak or absent flow follow-through".
        # So we look for Neutral or maybe just low slope consensus?
        
        # Let's say if NOT Conflict and NOT Dominant -> Weak?
        # Actually SPEC says "Weak or absent flow follow-through".
        
        return is_breakout_fail and (regime == FlowRegime.NEUTRAL)

    def _calculate_score(self, pattern: PatternType, candles: List[Candle], regime: FlowRegime) -> float:
        """
        Alert Scoring Model.
        """
        # Base Score
        score = 50.0 
        
        # Flow Alignment Bonus
        if regime == FlowRegime.CONSENSUS:
            score += 20
        elif regime == FlowRegime.SPOT_DOMINANT: # Spot leading is gold
            score += 15
        elif regime == FlowRegime.CONFLICT and pattern == PatternType.TRAP:
            score += 20 # Conflict is good for Traps
            
        # Volatility Bonus
        current = candles[-1]
        if current.atr_percentile and current.atr_percentile > 80:
            score += 10
        elif current.atr_percentile and current.atr_percentile < 20:
             # Ignition thrives in low vol
             if pattern == PatternType.IGNITION:
                 score += 10
                 
        return min(100.0, score)

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
