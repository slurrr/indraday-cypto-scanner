from typing import List, Optional, Tuple
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
import numpy as np
import time


class Analyzer:
    """
    Production-lean Analyzer:
    - Flow regime classification cleaned up and used together with actual slope direction
    - Patterns tightened to be directionally consistent
    - Volume / ATR filters guard against noise and data glitches
    """

    # --- Local tuning knobs (safe defaults; tweak in code, not config) ---
    VWAP_TOLERANCE = 0.001  # ~0.10% wiggle room around VWAP
    MIN_BODY_TO_RANGE = 0.3  # candle body must be at least 30% of range to be directional
    VOLUME_SPIKE_MULTIPLE = 1.8  # spike vs recent median
    TRAP_WICK_EXCESS_PCT = 0.001  # fallback 0.1% beyond prior high/low if ATR missing
    MIN_HISTORY = 30  # minimal candles to trust indicators & patterns
    IGNITION_LOW_VOL_MARGIN = 5.0  # pct points above MIN_ATR_PERCENTILE for pre-ignition cluster

    # ------------------------------------------------------------------
    # Public entry
    # ------------------------------------------------------------------
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
    def _get_flow_slopes(self, candles: List[Candle]) -> Tuple[float, float]:
        current = candles[-1]
        spot_slope = current.spot_cvd_slope if current.spot_cvd_slope is not None else 0.0
        perp_slope = current.perp_cvd_slope if current.perp_cvd_slope is not None else 0.0
        return float(spot_slope), float(perp_slope)

    def _determine_regime(self, candles: List[Candle]) -> FlowRegime:
        current = candles[-1]

        # Volatility gate: ultra-low vol -> don't over-interpret flow
        if (
            current.atr_percentile is not None
            and current.atr_percentile < MIN_ATR_PERCENTILE
        ):
            return FlowRegime.NEUTRAL

        spot_slope, perp_slope = self._get_flow_slopes(candles)
        thresh = FLOW_SLOPE_THRESHOLD

        # If both are tiny, it's just noise
        if abs(spot_slope) <= thresh and abs(perp_slope) <= thresh:
            return FlowRegime.NEUTRAL

        spot_up = spot_slope > thresh
        spot_down = spot_slope < -thresh
        perp_up = perp_slope > thresh
        perp_down = perp_slope < -thresh

        # Same-direction consensus
        if spot_up and perp_up:
            return FlowRegime.BULLISH_CONSENSUS
        if spot_down and perp_down:
            return FlowRegime.BEARISH_CONSENSUS

        # Opposite directions -> conflict
        if (spot_up and perp_down) or (spot_down and perp_up):
            return FlowRegime.CONFLICT

        # Dominance: whichever side has larger absolute slope
        if abs(spot_slope) > abs(perp_slope):
            return FlowRegime.SPOT_DOMINANT
        if abs(perp_slope) > abs(spot_slope):
            return FlowRegime.PERP_DOMINANT

        # Fallback
        return FlowRegime.NEUTRAL

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
        if c.high is None or c.low is None or c.open is None or c.close is None:
            return False
        rng = c.high - c.low
        if rng <= 0:
            return False
        body = abs(c.close - c.open)
        return body / rng >= self.MIN_BODY_TO_RANGE

    def _has_min_volume(self, c: Candle) -> bool:
        return c.volume is not None and c.volume > 0

    def _price_fields_ok(self, c: Candle) -> bool:
        return (
            c.open is not None
            and c.close is not None
            and c.high is not None
            and c.low is not None
        )

    def _bullish_flow_ok(self, candles: List[Candle], regime: FlowRegime) -> bool:
        spot_slope, perp_slope = self._get_flow_slopes(candles)
        if regime == FlowRegime.BULLISH_CONSENSUS:
            return True
        if regime == FlowRegime.SPOT_DOMINANT and spot_slope > 0:
            return True
        if regime == FlowRegime.PERP_DOMINANT and perp_slope > 0:
            return True
        return False

    def _bearish_flow_ok(self, candles: List[Candle], regime: FlowRegime) -> bool:
        spot_slope, perp_slope = self._get_flow_slopes(candles)
        if regime == FlowRegime.BEARISH_CONSENSUS:
            return True
        if regime == FlowRegime.SPOT_DOMINANT and spot_slope < 0:
            return True
        if regime == FlowRegime.PERP_DOMINANT and perp_slope < 0:
            return True
        return False

    # ------------------------------------------------------------------
    # Pattern A: VWAP Reclaim
    # ------------------------------------------------------------------
    def _check_vwap_reclaim(self, candles: List[Candle], regime: FlowRegime) -> bool:
        if len(candles) < 2:
            return False

        curr = candles[-1]
        prev = candles[-2]

        if not self._price_fields_ok(curr) or not self._price_fields_ok(prev):
            return False
        if curr.vwap is None or prev.vwap is None:
            return False
        if not self._has_min_volume(curr) or not self._has_min_volume(prev):
            return False

        vwap_tol = self.VWAP_TOLERANCE

        median_vol, _ = self._get_recent_volume_stats(candles)
        if median_vol <= 0:
            return False

        # Require a meaningful volume push
        vol_ok = curr.volume >= median_vol * self.VOLUME_SPIKE_MULTIPLE * 0.7
        if not vol_ok:
            return False

        # Bullish reclaim: previously below VWAP, now clearly above, green directional candle
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

        if bullish_reclaim and self._bullish_flow_ok(candles, regime):
            return True

        if bearish_reclaim and self._bearish_flow_ok(candles, regime):
            return True

        return False

    # ------------------------------------------------------------------
    # Pattern B: Ignition
    # ------------------------------------------------------------------
    def _check_ignition(self, candles: List[Candle], regime: FlowRegime) -> bool:
        if len(candles) < 7:  # cluster + current + a bit of context
            return False

        curr = candles[-1]
        prev = candles[-2]

        if not self._price_fields_ok(curr) or not self._has_min_volume(curr):
            return False
        if curr.atr is None or curr.atr <= 0:
            return False

        # Pre-condition: prior low/moderate-vol cluster
        cluster_len = 5
        window = candles[-(cluster_len + 1) : -1]
        atr_pcts = [c.atr_percentile for c in window if c.atr_percentile is not None]
        if len(atr_pcts) < cluster_len:
            return False

        mean_pct = float(np.mean(atr_pcts))
        # Allow ignition emerging from relatively quieter regime, but not only ultra-compressed
        if mean_pct > (MIN_ATR_PERCENTILE + self.IGNITION_LOW_VOL_MARGIN + 20.0):
            # Too hot already; not an ignition from quiet
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

        if curr.vwap is not None:
            price_vs_vwap_ok = (
                (is_bull and curr.close > curr.vwap)
                or (is_bear and curr.close < curr.vwap)
            )
            if not price_vs_vwap_ok:
                return False

        # Flow alignment with actual direction
        if is_bull and not self._bullish_flow_ok(candles, regime):
            return False
        if is_bear and not self._bearish_flow_ok(candles, regime):
            return False

        return True

    # ------------------------------------------------------------------
    # Pattern C: Post-Impulse Pullback
    # ------------------------------------------------------------------
    def _check_post_impulse_pullback(
        self, candles: List[Candle], regime: FlowRegime
    ) -> bool:
        curr = candles[-1]
        if not self._price_fields_ok(curr) or curr.vwap is None:
            return False
        if curr.atr is None or curr.atr <= 0:
            return False

        # 1. Find recent impulse candle (directional, large range)
        lookback_impulse = min(10, len(candles) - 1)
        if lookback_impulse <= 0:
            return False

        impulse_candle: Optional[Candle] = None
        impulse_dir: Optional[str] = None  # "up" or "down"

        for c in reversed(candles[-(lookback_impulse + 1) : -1]):
            if c.atr is None or c.atr <= 0:
                continue
            if not self._price_fields_ok(c):
                continue
            rng = c.high - c.low
            if rng > c.atr * IMPULSE_THRESHOLD_ATR and self._is_directional_candle(c):
                impulse_candle = c
                impulse_dir = "up" if c.close > c.open else "down"
                break

        if impulse_candle is None or impulse_dir is None:
            return False

        # 2. Current candle = compressed pullback with volume contraction
        current_range = curr.high - curr.low
        if current_range <= 0:
            return False

        is_compressed = current_range < curr.atr * PULLBACK_COMPRESSION_THRESHOLD_ATR

        median_vol, _ = self._get_recent_volume_stats(candles)
        vol_ok = median_vol > 0 and curr.volume is not None and curr.volume <= median_vol * 0.9  # volume contraction

        if not (is_compressed and vol_ok):
            return False

        # 3. Location: pullback into / near VWAP
        dist_to_vwap = abs(curr.close - curr.vwap)
        near_vwap = dist_to_vwap <= curr.atr * PULLBACK_VWAP_DISTANCE_ATR

        if not near_vwap:
            return False

        # 4. Directional & flow consistency
        if impulse_dir == "up":
            # Pullback should not be a hard breakdown below VWAP
            if curr.close < curr.vwap * (1 - self.VWAP_TOLERANCE * 3):
                return False
            if not self._bullish_flow_ok(candles, regime):
                return False
        else:  # "down"
            if curr.close > curr.vwap * (1 + self.VWAP_TOLERANCE * 3):
                return False
            if not self._bearish_flow_ok(candles, regime):
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

        if not self._price_fields_ok(curr) or not self._has_min_volume(curr):
            return False
        if not self._is_directional_candle(curr):
            return False

        prior = history[:-1]
        if not prior:
            return False

        prev_high = max(c.high for c in prior if c.high is not None)
        prev_low = min(c.low for c in prior if c.low is not None)

        median_vol, _ = self._get_recent_volume_stats(history)
        if median_vol <= 0:
            return False

        rng = curr.high - curr.low
        if rng <= 0:
            return False

        atr = curr.atr if curr.atr is not None and curr.atr > 0 else None

        # Use ATR-based sweep where possible, else percentage fallback
        if atr is not None:
            high_sweep_level = prev_high + 0.25 * atr
            low_sweep_level = prev_low - 0.25 * atr
        else:
            high_sweep_level = prev_high * (1 + self.TRAP_WICK_EXCESS_PCT)
            low_sweep_level = prev_low * (1 - self.TRAP_WICK_EXCESS_PCT)

        # Bull trap: sweep above high then slam back inside with red candle
        swept_high = curr.high > high_sweep_level
        closed_back_in_range_high = curr.close < prev_high and curr.close < curr.open

        # Bear trap: sweep below low then reclaim with green candle
        swept_low = curr.low < low_sweep_level
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

        # Flow disagreement / non-consensus is ideal environment for traps
        if regime not in (
            FlowRegime.CONFLICT,
            FlowRegime.SPOT_DOMINANT,
            FlowRegime.PERP_DOMINANT,
        ):
            # In pure consensus trend this is more likely a continuation wick
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

        if not self._price_fields_ok(curr):
            return False
        if not self._is_directional_candle(curr):
            return False

        prior = history[:-1]
        if not prior:
            return False

        prev_high = max(c.high for c in prior if c.high is not None)
        prev_low = min(c.low for c in prior if c.low is not None)

        rng = curr.high - curr.low
        if rng <= 0:
            return False

        atr = curr.atr if curr.atr is not None and curr.atr > 0 else None

        if atr is not None:
            high_sweep_level = prev_high + 0.15 * atr
            low_sweep_level = prev_low - 0.15 * atr
        else:
            high_sweep_level = prev_high * (1 + self.TRAP_WICK_EXCESS_PCT)
            low_sweep_level = prev_low * (1 - self.TRAP_WICK_EXCESS_PCT)

        swept_high = curr.high > high_sweep_level
        swept_low = curr.low < low_sweep_level

        # "Failure": break beyond, close back inside, but not an explosive trap candle
        close_back_inside_high = curr.close < prev_high
        close_back_inside_low = curr.close > prev_low

        is_rejection = (swept_high and close_back_inside_high) or (
            swept_low and close_back_inside_low
        )
        if not is_rejection:
            return False

        median_vol, _ = self._get_recent_volume_stats(history)
        if median_vol <= 0 or curr.volume is None:
            return False

        # Reject "explosive" candles (those are more like traps)
        if curr.volume > median_vol * (self.VOLUME_SPIKE_MULTIPLE * 0.9):
            return False

        # Flow should be weak / messy rather than strongly trending
        if regime not in (FlowRegime.NEUTRAL, FlowRegime.CONFLICT):
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
        if (
            current.atr is not None
            and current.atr > 0
            and current.high is not None
            and current.low is not None
        ):
            rng = current.high - current.low
            if rng > 0:
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

    # ------------------------------------------------------------------
    # DEBUG MODE (Non-intrusive helper)
    # ------------------------------------------------------------------
    def debug_analyze(self, symbol: str, candles: List[Candle]):
        """
        Non-intrusive debug helper.
        Returns a dict explaining WHY each pattern did or did not fire.
        Does NOT impact production logic.
        """

        out = {
            "symbol": symbol,
            "count": len(candles),
            "flow_regime": None,
            "patterns": {
                "VWAP_RECLAIM": {},
                "IGNITION": {},
                "PULLBACK": {},
                "TRAP": {},
                "FAILED_BREAKOUT": {},
            },
        }

        if len(candles) < self.MIN_HISTORY:
            out["flow_regime"] = "N/A - too little history"
            for k in out["patterns"]:
                out["patterns"][k] = {"ok": False, "reason": "MIN_HISTORY not met"}
            return out

        # Determine flow regime with raw numbers included
        regime = self._determine_regime(candles)
        spot_slope, perp_slope = self._get_flow_slopes(candles)
        out["flow_regime"] = {
            "regime": regime.value,
            "spot_slope": spot_slope,
            "perp_slope": perp_slope,
        }

        # Convenience wrappers so we can capture “why it failed”
        def _dbg_wrapper(name: str, check_fn):
            try:
                ok, reason = check_fn()
            except Exception as e:
                return {"ok": False, "reason": f"Exception: {e}"}
            return {"ok": ok, "reason": reason}

        # --- Pattern debug functions -----------------------------------
        def dbg_vwap():
            curr = candles[-1]
            prev = candles[-2]

            if curr.vwap is None or prev.vwap is None:
                return False, "Missing VWAP"

            if curr.volume is None or curr.volume <= 0:
                return False, "No volume"

            median_vol, _ = self._get_recent_volume_stats(candles)
            if curr.volume < median_vol * self.VOLUME_SPIKE_MULTIPLE * 0.7:
                return False, "Volume spike insufficient"

            vwap_tol = self.VWAP_TOLERANCE

            bullish = (
                prev.close < prev.vwap * (1 - vwap_tol)
                and curr.close > curr.vwap * (1 + vwap_tol / 2)
                and curr.close > curr.open
                and self._is_directional_candle(curr)
            )
            bearish = (
                prev.close > prev.vwap * (1 + vwap_tol)
                and curr.close < curr.vwap * (1 - vwap_tol / 2)
                and curr.close < curr.open
                and self._is_directional_candle(curr)
            )

            if bullish and not self._bullish_flow_ok(candles, regime):
                return False, "Bullish reclaim but flow not bullish"

            if bearish and not self._bearish_flow_ok(candles, regime):
                return False, "Bearish reclaim but flow not bearish"

            if bullish or bearish:
                return True, "Reclaim detected"

            return False, "Conditions did not form valid reclaim"

        def dbg_ignition():
            curr = candles[-1]

            if curr.atr is None or curr.atr <= 0:
                return False, "Missing ATR"

            cluster_len = 5
            if len(candles) < cluster_len + 2:
                return False, "Not enough candles for cluster"

            window = candles[-(cluster_len + 1) : -1]
            atr_pcts = [c.atr_percentile for c in window if c.atr_percentile is not None]
            if len(atr_pcts) < cluster_len:
                return False, "Missing ATR percentiles"

            mean_pct = float(np.mean(atr_pcts))
            if mean_pct > (MIN_ATR_PERCENTILE + self.IGNITION_LOW_VOL_MARGIN + 20.0):
                return False, f"ATR cluster too hot (mean={mean_pct:.1f})"

            rng = curr.high - curr.low
            if rng <= curr.atr * IGNITION_EXPANSION_THRESHOLD_ATR:
                return False, "Range not expanded over ATR threshold"

            med_vol, _ = self._get_recent_volume_stats(candles)
            if curr.volume < med_vol * self.VOLUME_SPIKE_MULTIPLE:
                return False, "Volume not spiking enough"

            if not self._is_directional_candle(curr):
                return False, "Not directional candle"

            if curr.vwap is not None:
                if curr.close > curr.open and curr.close < curr.vwap:
                    return False, "Bullish candle but below VWAP"
                if curr.close < curr.open and curr.close > curr.vwap:
                    return False, "Bearish candle but above VWAP"

            if curr.close > curr.open and not self._bullish_flow_ok(candles, regime):
                return False, "Bullish candle but flow not bullish"

            if curr.close < curr.open and not self._bearish_flow_ok(candles, regime):
                return False, "Bearish candle but flow not bearish"

            return True, "Ignition confirmed"

        def dbg_pullback():
            curr = candles[-1]

            if curr.atr is None or curr.atr <= 0:
                return False, "Missing ATR"

            impulse = None
            impulse_dir = None

            for c in reversed(candles[-11:-1]):
                if c.atr and (c.high - c.low) > c.atr * IMPULSE_THRESHOLD_ATR:
                    if self._is_directional_candle(c):
                        impulse = c
                        impulse_dir = "up" if c.close > c.open else "down"
                        break

            if impulse is None:
                return False, "No valid impulse candle found"

            rng = curr.high - curr.low
            if rng >= curr.atr * PULLBACK_COMPRESSION_THRESHOLD_ATR:
                return False, "Pullback not compressed"

            med_vol, _ = self._get_recent_volume_stats(candles)
            if curr.volume > med_vol * 0.9:
                return False, "Volume not contracting"

            if curr.vwap is None:
                return False, "Missing VWAP"

            if abs(curr.close - curr.vwap) > curr.atr * PULLBACK_VWAP_DISTANCE_ATR:
                return False, "Not near VWAP"

            if impulse_dir == "up":
                if not self._bullish_flow_ok(candles, regime):
                    return False, "Flow not bullish"
            else:
                if not self._bearish_flow_ok(candles, regime):
                    return False, "Flow not bearish"

            return True, f"Pullback OK (dir={impulse_dir})"

        def dbg_trap():
            curr = candles[-1]
            if not self._price_fields_ok(curr):
                return False, "Missing candle fields"

            history = candles[-SESSION_LOOKBACK_WINDOW:]
            if len(history) < 10:
                return False, "Not enough history for trap"

            prior = history[:-1]
            prev_high = max(c.high for c in prior)
            prev_low = min(c.low for c in prior)

            atr = curr.atr
            if atr:
                high_sweep = prev_high + 0.25 * atr
                low_sweep = prev_low - 0.25 * atr
            else:
                high_sweep = prev_high * (1 + self.TRAP_WICK_EXCESS_PCT)
                low_sweep = prev_low * (1 - self.TRAP_WICK_EXCESS_PCT)

            swept_high = curr.high > high_sweep
            swept_low = curr.low < low_sweep

            vol_med, _ = self._get_recent_volume_stats(history)
            if curr.volume < vol_med * self.VOLUME_SPIKE_MULTIPLE:
                return False, "Volume spike insufficient"

            if swept_high and curr.close < prev_high and curr.close < curr.open:
                return True, "Bear trap above high"
            if swept_low and curr.close > prev_low and curr.close > curr.open:
                return True, "Bull trap below low"

            return False, "Conditions did not form trap"

        def dbg_failed():
            curr = candles[-1]

            history = candles[-SESSION_LOOKBACK_WINDOW:]
            if len(history) < 10:
                return False, "Not enough history"

            prior = history[:-1]
            prev_high = max(c.high for c in prior)
            prev_low = min(c.low for c in prior)

            atr = curr.atr
            if atr:
                high_sweep = prev_high + 0.15 * atr
                low_sweep = prev_low - 0.15 * atr
            else:
                high_sweep = prev_high * (1 + self.TRAP_WICK_EXCESS_PCT)
                low_sweep = prev_low * (1 - self.TRAP_WICK_EXCESS_PCT)

            swept_high = curr.high > high_sweep
            swept_low = curr.low < low_sweep

            back_in = (swept_high and curr.close < prev_high) or (
                swept_low and curr.close > prev_low
            )
            if not back_in:
                return False, "No sweep + close back inside"

            vol_med, _ = self._get_recent_volume_stats(history)
            if curr.volume > vol_med * (self.VOLUME_SPIKE_MULTIPLE * 0.9):
                return False, "Too explosive; likely trap"

            if regime not in (FlowRegime.NEUTRAL, FlowRegime.CONFLICT):
                return False, f"Flow regime not conducive to failed breakout ({regime.value})"

            return True, "Failed breakout confirmed"

        # Attach results
        out["patterns"]["VWAP_RECLAIM"] = _dbg_wrapper("VWAP_RECLAIM", dbg_vwap)
        out["patterns"]["IGNITION"] = _dbg_wrapper("IGNITION", dbg_ignition)
        out["patterns"]["PULLBACK"] = _dbg_wrapper("PULLBACK", dbg_pullback)
        out["patterns"]["TRAP"] = _dbg_wrapper("TRAP", dbg_trap)
        out["patterns"]["FAILED_BREAKOUT"] = _dbg_wrapper("FAILED_BREAKOUT", dbg_failed)

        return out
