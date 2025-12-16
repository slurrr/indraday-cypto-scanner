from typing import List, Optional
from models.types import Candle, FlowRegime, PatternType, Alert
from config.settings import (
    MIN_ATR_PERCENTILE,
    FLOW_SLOPE_THRESHOLD,
    IMPULSE_THRESHOLD_ATR,
    IGNITION_EXPANSION_THRESHOLD_ATR,
    PULLBACK_COMPRESSION_THRESHOLD_ATR,
    PULLBACK_VWAP_DISTANCE_ATR,
    SESSION_LOOKBACK_WINDOW,
    MIN_ALERT_SCORE,
    SCORING_WEIGHTS,
)
import pandas as pd
import numpy as np
import time


class Analyzer:
    """
    Production-lean Analyzer:
    - Flow regime classification simplified and de-bugged
    - Each pattern is stricter and directionally consistent
    - Volume and ATR are used to filter out noise
    - Scoring model reinforced to down-weight weak setups
    """

    # --- Local tuning knobs (safe defaults; tweak in code, not config) ---
    VWAP_TOLERANCE = 0.001  # ~0.10% wiggle room around VWAP
    MIN_BODY_TO_RANGE = 0.3  # candle body must be at least 30% of range to be directional
    VOLUME_SPIKE_MULTIPLE = 1.8  # spike vs recent median
    TRAP_WICK_EXCESS = 0.001  # 0.1% beyond prior high/low to count as sweep
    MIN_HISTORY = 30  # minimal candles to trust indicators & patterns
    IGNITION_LOW_VOL_MARGIN = 10.0  # pct points above MIN_ATR_PERCENTILE for pre-ignition cluster

    def analyze(self, symbol: str, candles: List[Candle]) -> List[Alert]:
        if len(candles) < self.MIN_HISTORY:
            return []

        current_candle = candles[-1]
        regime = self._determine_regime(candles)

        alerts: List[Alert] = []

        # --- Pattern A: VWAP Reclaim ---
        if self._check_vwap_reclaim(candles, regime):
            score = self._calculate_score(PatternType.VWAP_RECLAIM, candles, regime)
            if score >= MIN_ALERT_SCORE:
                alerts.append(
                    self._create_alert(
                        symbol, PatternType.VWAP_RECLAIM, regime, current_candle, score
                    )
                )

        # --- Pattern B: Ignition ---
        if self._check_ignition(candles, regime):
            score = self._calculate_score(PatternType.IGNITION, candles, regime)
            if score >= MIN_ALERT_SCORE:
                alerts.append(
                    self._create_alert(
                        symbol, PatternType.IGNITION, regime, current_candle, score
                    )
                )

        # --- Pattern C: Post-Impulse Pullback ---
        if self._check_post_impulse_pullback(candles, regime):
            score = self._calculate_score(PatternType.PULLBACK, candles, regime)
            if score >= MIN_ALERT_SCORE:
                alerts.append(
                    self._create_alert(
                        symbol, PatternType.PULLBACK, regime, current_candle, score
                    )
                )

        # --- Pattern D: Trap (Top/Bottom) ---
        is_trap = self._check_trap(candles, regime)
        if is_trap:
            score = self._calculate_score(PatternType.TRAP, candles, regime)
            if score >= MIN_ALERT_SCORE:
                alerts.append(
                    self._create_alert(
                        symbol, PatternType.TRAP, regime, current_candle, score
                    )
                )

        # --- Pattern E: Failed Breakout (non-trap rejection) ---
        if self._check_failed_breakout(candles, regime, already_trap=is_trap):
            score = self._calculate_score(PatternType.FAILED_BREAKOUT, candles, regime)
            if score >= MIN_ALERT_SCORE:
                alerts.append(
                    self._create_alert(
                        symbol, PatternType.FAILED_BREAKOUT, regime, current_candle, score
                    )
                )

        return alerts

    # ------------------------------------------------------------------
    # Flow Regime
    # ------------------------------------------------------------------
    def _determine_regime(self, candles: List[Candle]) -> FlowRegime:
        current = candles[-1]

        # Volatility gate: ultra-low vol -> don't over-interpret flow
        if (
            current.atr_percentile is not None
            and current.atr_percentile < MIN_ATR_PERCENTILE
        ):
            return FlowRegime.NEUTRAL

        spot_slope = current.spot_cvd_slope or 0.0
        perp_slope = current.perp_cvd_slope or 0.0
        thresh = FLOW_SLOPE_THRESHOLD

        # If both are tiny, it's just noise
        if abs(spot_slope) <= thresh and abs(perp_slope) <= thresh:
            return FlowRegime.NEUTRAL

        spot_up = spot_slope > thresh
        spot_down = spot_slope < -thresh
        perp_up = perp_slope > thresh
        perp_down = perp_slope < -thresh

        # Simple, non-contradictory mapping
        if spot_up and perp_up:
            return FlowRegime.BULLISH_CONSENSUS
        if spot_down and perp_down:
            return FlowRegime.BEARISH_CONSENSUS

        # Dominance: whichever side is "more" directional wins
        if spot_up and not perp_down:
            return FlowRegime.SPOT_DOMINANT
        if spot_down and not perp_up:
            return FlowRegime.SPOT_DOMINANT

        if perp_up and not spot_down:
            return FlowRegime.PERP_DOMINANT
        if perp_down and not spot_up:
            return FlowRegime.PERP_DOMINANT

        # Remaining mismatched cases -> conflict
        return FlowRegime.CONFLICT

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _get_recent_volume_stats(self, candles: List[Candle], window: int = 20):
        recent = candles[-window:] if len(candles) >= window else candles
        vols = [c.volume for c in recent if c.volume is not None]
        if not vols:
            return 0.0, 0.0
        return float(np.median(vols)), float(np.mean(vols))

    def _is_directional_candle(self, c: Candle) -> bool:
        rng = c.high - c.low
        if rng <= 0:
            return False
        body = abs(c.close - c.open)
        return body / rng >= self.MIN_BODY_TO_RANGE

    # ------------------------------------------------------------------
    # Pattern A: VWAP Reclaim
    # ------------------------------------------------------------------
    def _check_vwap_reclaim(self, candles: List[Candle], regime: FlowRegime) -> bool:
        curr = candles[-1]
        prev = candles[-2]

        if not curr.vwap or not prev.vwap or curr.volume <= 0 or prev.volume <= 0:
            return False

        vwap_tol = self.VWAP_TOLERANCE

        median_vol, _ = self._get_recent_volume_stats(candles)
        if median_vol <= 0:
            return False

        # Require a meaningful volume push
        vol_ok = curr.volume >= median_vol * self.VOLUME_SPIKE_MULTIPLE * 0.7
        if not vol_ok:
            return False

        # Bullish reclaim: previously below VWAP, now clearly above, green candle
        bullish_reclaim = (
            prev.close < prev.vwap * (1 - vwap_tol)
            and curr.close > curr.vwap * (1 + vwap_tol / 2.0)
            and curr.close > curr.open
            and self._is_directional_candle(curr)
        )

        # Bearish reclaim (VWAP rejection from above)
        bearish_reclaim = (
            prev.close > prev.vwap * (1 + vwap_tol)
            and curr.close < curr.vwap * (1 - vwap_tol / 2.0)
            and curr.close < curr.open
            and self._is_directional_candle(curr)
        )

        if bullish_reclaim:
            # Only allow when flow is bullish / spot led
            if regime in (FlowRegime.BULLISH_CONSENSUS, FlowRegime.SPOT_DOMINANT):
                return True
            return False

        if bearish_reclaim:
            if regime in (FlowRegime.BEARISH_CONSENSUS, FlowRegime.PERP_DOMINANT):
                return True
            return False

        return False

    # ------------------------------------------------------------------
    # Pattern B: Ignition
    # ------------------------------------------------------------------
    def _check_ignition(self, candles: List[Candle], regime: FlowRegime) -> bool:
        curr = candles[-1]
        prev = candles[-2]

        if not curr.atr or curr.atr <= 0 or curr.volume <= 0:
            return False

        # Pre-condition: prior low-vol cluster (ATR percentile relatively low)
        cluster_len = 5
        if len(candles) < cluster_len + 1:
            return False

        window = candles[-(cluster_len + 1) : -1]
        atr_pcts = [c.atr_percentile for c in window if c.atr_percentile is not None]
        if len(atr_pcts) < cluster_len:
            return False

        mean_pct = float(np.mean(atr_pcts))
        if mean_pct > (MIN_ATR_PERCENTILE + self.IGNITION_LOW_VOL_MARGIN):
            # Not actually emerging from a quiet regime
            return False

        # Expansion: current range vs ATR
        current_range = curr.high - curr.low
        if current_range <= curr.atr * IGNITION_EXPANSION_THRESHOLD_ATR:
            return False

        # Volume spike vs recent median
        median_vol, _ = self._get_recent_volume_stats(candles)
        if median_vol <= 0 or curr.volume < median_vol * self.VOLUME_SPIKE_MULTIPLE:
            return False

        if not self._is_directional_candle(curr):
            return False

        is_bull = curr.close > curr.open
        is_bear = curr.close < curr.open

        if curr.vwap:
            price_vs_vwap_ok = (
                (is_bull and curr.close > curr.vwap)
                or (is_bear and curr.close < curr.vwap)
            )
            if not price_vs_vwap_ok:
                return False

        # Flow alignment
        if is_bull and regime not in (
            FlowRegime.BULLISH_CONSENSUS,
            FlowRegime.SPOT_DOMINANT,
        ):
            return False
        if is_bear and regime not in (
            FlowRegime.BEARISH_CONSENSUS,
            FlowRegime.PERP_DOMINANT,
        ):
            return False

        return True

    # ------------------------------------------------------------------
    # Pattern C: Post-Impulse Pullback
    # ------------------------------------------------------------------
    def _check_post_impulse_pullback(
        self, candles: List[Candle], regime: FlowRegime
    ) -> bool:
        curr = candles[-1]
        if not curr.atr or curr.atr <= 0 or not curr.vwap:
            return False

        # 1. Find recent impulse candle (directional, large range)
        lookback_impulse = min(10, len(candles) - 1)
        if lookback_impulse <= 0:
            return False

        impulse_candle: Optional[Candle] = None
        impulse_dir: Optional[str] = None  # "up" or "down"

        for c in reversed(candles[-(lookback_impulse + 1) : -1]):
            if not c.atr or c.atr <= 0:
                continue
            rng = c.high - c.low
            if rng > c.atr * IMPULSE_THRESHOLD_ATR and self._is_directional_candle(c):
                impulse_candle = c
                impulse_dir = "up" if c.close > c.open else "down"
                break

        if impulse_candle is None or impulse_dir is None:
            return False

        # 2. Current candle = compressed pullback
        current_range = curr.high - curr.low
        if current_range <= 0:
            return False

        is_compressed = current_range < curr.atr * PULLBACK_COMPRESSION_THRESHOLD_ATR

        median_vol, _ = self._get_recent_volume_stats(candles)
        vol_ok = median_vol > 0 and curr.volume <= median_vol * 0.9  # volume contraction

        if not (is_compressed and vol_ok):
            return False

        # 3. Location: pullback into / near VWAP and still within impulse context
        dist_to_vwap = abs(curr.close - curr.vwap)
        near_vwap = dist_to_vwap <= curr.atr * PULLBACK_VWAP_DISTANCE_ATR

        if not near_vwap:
            return False

        # 4. Directional & flow consistency
        if impulse_dir == "up":
            # Price pulled back toward VWAP but not deeply below
            if curr.close < curr.vwap * (1 - self.VWAP_TOLERANCE * 2):
                return False
            if regime not in (FlowRegime.BULLISH_CONSENSUS, FlowRegime.SPOT_DOMINANT):
                return False
        else:  # "down"
            if curr.close > curr.vwap * (1 + self.VWAP_TOLERANCE * 2):
                return False
            if regime not in (FlowRegime.BEARISH_CONSENSUS, FlowRegime.PERP_DOMINANT):
                return False

        return True

    # ------------------------------------------------------------------
    # Pattern D: Trap (Stop Run)
    # ------------------------------------------------------------------
    def _check_trap(self, candles: List[Candle], regime: FlowRegime) -> bool:
        curr = candles[-1]

        lookback = SESSION_LOOKBACK_WINDOW
        history = candles[-lookback:] if len(candles) > lookback else candles
        if len(history) < 10:
            return False

        prior = history[:-1]
        if not prior:
            return False

        prev_high = max(c.high for c in prior)
        prev_low = min(c.low for c in prior)

        median_vol, _ = self._get_recent_volume_stats(history)
        if median_vol <= 0:
            return False

        rng = curr.high - curr.low
        if rng <= 0 or not self._is_directional_candle(curr):
            return False

        # Bull trap: sweep above high then slam back inside with red candle & volume
        swept_high = curr.high > prev_high * (1 + self.TRAP_WICK_EXCESS)
        closed_back_in_range_high = curr.close < prev_high and curr.close < curr.open

        # Bear trap: sweep below low then reclaim with green candle & volume
        swept_low = curr.low < prev_low * (1 - self.TRAP_WICK_EXCESS)
        closed_back_in_range_low = curr.close > prev_low and curr.close > curr.open

        is_trap_like = (swept_high and closed_back_in_range_high) or (
            swept_low and closed_back_in_range_low
        )

        if not is_trap_like:
            return False

        # Require real stop run behavior: big candle + volume spike
        vol_ok = curr.volume >= median_vol * self.VOLUME_SPIKE_MULTIPLE
        if not vol_ok:
            return False

        # Flow disagreement is ideal environment for traps
        if regime not in (
            FlowRegime.CONFLICT,
            FlowRegime.SPOT_DOMINANT,
            FlowRegime.PERP_DOMINANT,
        ):
            # In pure consensus trend this is more likely a pullback/continuation wick
            return False

        return True

    # ------------------------------------------------------------------
    # Pattern E: Failed Breakout (Non-trap)
    # ------------------------------------------------------------------
    def _check_failed_breakout(
        self, candles: List[Candle], regime: FlowRegime, already_trap: bool
    ) -> bool:
        if already_trap:
            # Trap is a stronger pattern; don't double-report as failed breakout
            return False

        curr = candles[-1]

        lookback = SESSION_LOOKBACK_WINDOW
        history = candles[-lookback:] if len(candles) > lookback else candles
        if len(history) < 10:
            return False

        prior = history[:-1]
        if not prior:
            return False

        prev_high = max(c.high for c in prior)
        prev_low = min(c.low for c in prior)

        rng = curr.high - curr.low
        if rng <= 0:
            return False

        swept_high = curr.high > prev_high * (1 + self.TRAP_WICK_EXCESS)
        swept_low = curr.low < prev_low * (1 - self.TRAP_WICK_EXCESS)

        # "Failure": break beyond, close back inside, but not an aggressive trap candle
        close_back_inside_high = curr.close < prev_high
        close_back_inside_low = curr.close > prev_low

        is_rejection = (swept_high and close_back_inside_high) or (
            swept_low and close_back_inside_low
        )
        if not is_rejection:
            return False

        # If it's very aggressive (big body & volume), trap logic should handle it.
        # Here we prefer softer failures: normal volume or only mild increase.
        median_vol, _ = self._get_recent_volume_stats(history)
        if median_vol <= 0:
            return False

        # Reject "explosive" candles (those are more like traps)
        if curr.volume > median_vol * (self.VOLUME_SPIKE_MULTIPLE * 0.9):
            return False

        # Flow should be weak / absent rather than strongly opposing
        if regime not in (FlowRegime.NEUTRAL, FlowRegime.CONFLICT):
            # Strong consensus / dominance usually implies continuation, not failure
            return False

        return True

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------
    def _calculate_score(
        self, pattern: PatternType, candles: List[Candle], regime: FlowRegime
    ) -> float:
        current = candles[-1]
        score = float(SCORING_WEIGHTS.get("BASE_PATTERN", 0.0))

        # Flow Alignment / Context
        if regime in (FlowRegime.BULLISH_CONSENSUS, FlowRegime.BEARISH_CONSENSUS):
            score += SCORING_WEIGHTS.get("FLOW_ALIGNMENT", 0.0)
        elif regime in (FlowRegime.SPOT_DOMINANT, FlowRegime.PERP_DOMINANT):
            score += SCORING_WEIGHTS.get("CONTEXT", 0.0)
        elif regime == FlowRegime.CONFLICT and pattern == PatternType.TRAP:
            score += SCORING_WEIGHTS.get("FLOW_ALIGNMENT", 0.0)
        elif regime == FlowRegime.NEUTRAL and pattern in (
            PatternType.IGNITION,
            PatternType.VWAP_RECLAIM,
        ):
            # Penalize directional patterns in neutral flow
            score -= SCORING_WEIGHTS.get("CONTEXT", 0.0) * 0.5

        # Volatility contribution
        if current.atr_percentile is not None:
            if current.atr_percentile > 80:
                score += SCORING_WEIGHTS.get("VOLATILITY", 0.0)
            elif current.atr_percentile < 20 and pattern == PatternType.IGNITION:
                # Ignition emerging from low vol is extra good
                score += SCORING_WEIGHTS.get("VOLATILITY", 0.0)

        # Magnitude bump for certain patterns
        rng = current.high - current.low
        if current.atr and current.atr > 0 and rng > 0:
            magnitude_ratio = min(rng / current.atr, 3.0)  # cap at 3x ATR
            if pattern in (PatternType.IGNITION, PatternType.TRAP):
                score += magnitude_ratio * 2.0
            elif pattern in (PatternType.VWAP_RECLAIM, PatternType.PULLBACK):
                score += magnitude_ratio

        # Clamp and floor
        score = max(0.0, min(100.0, score))
        return score

    # ------------------------------------------------------------------
    # Alert factory
    # ------------------------------------------------------------------
    def _create_alert(
        self,
        symbol: str,
        pattern: PatternType,
        regime: FlowRegime,
        candle: Candle,
        score: float,
    ) -> Alert:
        return Alert(
            timestamp=int(time.time() * 1000),
            candle_timestamp=candle.timestamp,
            symbol=symbol,
            pattern=pattern,
            score=score,
            flow_regime=regime,
            price=candle.close,
            message=f"{pattern.value} detected in {regime.value}",
        )
