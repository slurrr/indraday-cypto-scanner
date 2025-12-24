from typing import List, Optional, Tuple, Dict
from models.types import Candle, FlowRegime, PatternType, ExecutionType, Alert, StateSnapshot, TimeframeContext, State, ExecutionSignal
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
    WATCH_ELIGIBLE_PATTERNS,
    ACT_ELIGIBLE_PATTERNS,
    ACT_DEMOTION_PATTERNS,
    MAX_ACT_DURATION_MS,
    MAX_WATCH_DURATION_MS,
)
import numpy as np
import time
from utils.snapshot_logger import write_snapshot
from utils.event_snapshot import build_snapshot
from utils.logger import setup_logger

logger = setup_logger("Analyzer")


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
    def analyze(
        self, 
        symbol: str, 
        candles: List[Candle], 
        context: Optional["TimeframeContext"] = None,
        state: Optional["StateSnapshot"] = None,
        perp_candles: Optional[List[Candle]] = None
    ) -> List[Alert]:
        if len(candles) < self.MIN_HISTORY:
            return []

        current_candle = candles[-1]
        
        # Update State (placeholder logic for now)
        if state:
            state.last_updated_at = current_candle.timestamp

        # --- FLOW REGIME FIX (Segregation) ---
        # If we have separate perp candles, inject the Perp CVD slope into the Spot Candle
        # so that _determine_regime and patterns can see the full picture.
        if perp_candles:
            # Try to match the last candle (fastest)
            if perp_candles[-1].timestamp == current_candle.timestamp:
                pc = perp_candles[-1]
                current_candle.perp_cvd_slope = pc.perp_cvd_slope
                current_candle.perp_cvd = pc.perp_cvd
            else:
                 # Search backwards
                 for pc in reversed(perp_candles):
                     if pc.timestamp == current_candle.timestamp:
                         current_candle.perp_cvd_slope = pc.perp_cvd_slope
                         current_candle.perp_cvd = pc.perp_cvd
                         break

        regime = self._determine_regime(candles, current_candle)
        
        # Populate Visual Flow Strength in State
        if state:
            state.flow_regime = regime.name
            # Get Max Z-Score for strength
            spot_z = current_candle.spot_cvd_slope_z if current_candle.spot_cvd_slope_z else 0.0
            perp_z = current_candle.perp_cvd_slope_z if current_candle.perp_cvd_slope_z else 0.0
            state.flow_score = max(abs(spot_z), abs(perp_z))

        alerts: List[Alert] = []
        
        # --- Pattern A: VWAP Reclaim ---
        if self._check_vwap_reclaim(candles, current_candle, regime):
            score = self._calculate_score(PatternType.VWAP_RECLAIM, candles, current_candle, regime)
            snapshot = build_snapshot(
                symbol=symbol,
                pattern=PatternType.VWAP_RECLAIM,
                candle=current_candle,
                regime=regime,
                score=score,
                passed=True,
                debug_data=None,
            )
            write_snapshot(snapshot)
            if score >= MIN_ALERT_SCORE:
                alerts.append(
                    self._create_alert(
                        symbol, PatternType.VWAP_RECLAIM, regime, current_candle, score, context
                    )
                )

        # --- Pattern B: Ignition ---
        if self._check_ignition(candles, current_candle, regime):
            score = self._calculate_score(PatternType.IGNITION, candles, current_candle, regime)
            snapshot = build_snapshot(
                symbol=symbol,
                pattern=PatternType.IGNITION,
                candle=current_candle,
                regime=regime,
                score=score,
                passed=True,
                debug_data=None,
            )
            write_snapshot(snapshot)
            if score >= MIN_ALERT_SCORE:
                alerts.append(
                    self._create_alert(
                        symbol, PatternType.IGNITION, regime, current_candle, score, context
                    )
                )

        # --- Pattern C: Post-Impulse Pullback ---
        if self._check_post_impulse_pullback(candles, current_candle, regime):
            score = self._calculate_score(PatternType.PULLBACK, candles, current_candle, regime)
            snapshot = build_snapshot(
                symbol=symbol,
                pattern=PatternType.PULLBACK,
                candle=current_candle,
                regime=regime,
                score=score,
                passed=True,
                debug_data=None,
            )
            write_snapshot(snapshot)
            if score >= MIN_ALERT_SCORE:
                alerts.append(
                    self._create_alert(
                        symbol, PatternType.PULLBACK, regime, current_candle, score, context
                    )
                )

        # --- Pattern D: Trap (Top/Bottom) ---
        is_trap = self._check_trap(candles, current_candle, regime)
        if is_trap:
            score = self._calculate_score(PatternType.TRAP, candles, current_candle, regime)
            snapshot = build_snapshot(
                symbol=symbol,
                pattern=PatternType.TRAP,
                candle=current_candle,
                regime=regime,
                score=score,
                passed=True,
                debug_data=None,
            )
            write_snapshot(snapshot)
            if score >= MIN_ALERT_SCORE:
                alerts.append(
                    self._create_alert(
                        symbol, PatternType.TRAP, regime, current_candle, score, context
                    )
                )

        # --- Pattern E: Failed Breakout (non-trap rejection) ---
        if self._check_failed_breakout(candles, current_candle, regime, already_trap=is_trap):
            score = self._calculate_score(PatternType.FAILED_BREAKOUT, candles, current_candle, regime)
            snapshot = build_snapshot(
                symbol=symbol,
                pattern=PatternType.FAILED_BREAKOUT,
                candle=current_candle,
                regime=regime,
                score=score,
                passed=True,
                debug_data=None,
            )
            write_snapshot(snapshot)
            if score >= MIN_ALERT_SCORE:
                alerts.append(
                    self._create_alert(
                        symbol, PatternType.FAILED_BREAKOUT, regime, current_candle, score, context
                    )
                )


        # --- State Promotion Logic ---
        if state and state.state == State.IGNORE:
            # Gather all qualifying patterns from generated alerts
            # We use strict matching against WATCH_ELIGIBLE_PATTERNS
            qualifying_patterns = [
                a.pattern.value for a in alerts 
                if a.pattern.value in WATCH_ELIGIBLE_PATTERNS
            ]
            
            if qualifying_patterns:
                state.state = State.WATCH
                state.entered_at = current_candle.timestamp
                # last_updated_at was already updated at top of method
                state.watch_reason = qualifying_patterns[0]
                state.active_patterns = list(qualifying_patterns)
                logger.info(f"PROMOTION: {symbol} IGNORE->WATCH (Reason: {state.watch_reason}) At={state.entered_at}")

        # --- State Promotion Logic: WATCH -> ACT ---
        elif state and state.state == State.WATCH:
            # Check permission first
            if state.permission and state.permission.allowed:
                 # Gather qualifying patterns
                 qualifying_patterns = [
                    a.pattern.value for a in alerts 
                    if a.pattern.value in ACT_ELIGIBLE_PATTERNS
                 ]
                 if qualifying_patterns:
                     state.state = State.ACT
                     state.entered_at = current_candle.timestamp
                     state.act_reason = qualifying_patterns[0]
                     state.active_patterns.extend(qualifying_patterns)

                     # Fix: Infer act_direction locally based on the triggering pattern
                     direction = None
                     trigger = PatternType(state.act_reason)
                     
                     if trigger in (PatternType.IGNITION, PatternType.TRAP):
                         # Green -> LONG, Red -> SHORT
                         direction = "LONG" if current_candle.close > current_candle.open else "SHORT"
                     elif trigger == PatternType.VWAP_RECLAIM:
                         # Above VWAP -> LONG, Below -> SHORT
                         vwap = current_candle.vwap or 0.0
                         direction = "LONG" if current_candle.close > vwap else "SHORT"
                     elif trigger == PatternType.PULLBACK:
                         # Use flow check to confirm direction
                         if self._bullish_flow_ok(candles, current_candle, regime):
                             direction = "LONG"
                         else:
                             direction = "SHORT"
                     elif trigger == PatternType.FAILED_BREAKOUT:
                         # If rejecting high -> SHORT, rejecting low -> LONG
                         # Heuristic: check if current high > recent high
                         hist = candles[-SESSION_LOOKBACK_WINDOW:-1]
                         if hist:
                             prev_high = max((c.high for c in hist if c.high is not None), default=0.0)
                             if current_candle.high is not None and current_candle.high > prev_high:
                                 direction = "SHORT"
                             else:
                                 direction = "LONG"
                         else:
                             direction = "LONG" # Fallback

                     state.act_direction = direction

                     # Fix: Validate direction against 15m Permission Bias
                     # If conflicting, abort promotion (revert to WATCH)
                     if state.permission and state.permission.bias:
                         bias = state.permission.bias
                         if (bias == "BULLISH" and direction == "SHORT") or \
                            (bias == "BEARISH" and direction == "LONG"):
                             # Conflict detected - Abort Promotion
                             state.state = State.WATCH
                             state.act_reason = None
                             state.act_direction = None
                             state.active_patterns = [state.watch_reason] if state.watch_reason else []
                             state.reasons.append(f"Blocked ACT promotion: Bias {bias} conflicts with {direction}")

        # --- Demotion Logic: ACT -> WATCH ---
        if state and state.state == State.ACT:
            duration = current_candle.timestamp - state.entered_at
            
            # 1. Timeout
            timeout = duration > MAX_ACT_DURATION_MS
            # 2. Permission Revoked
            perm_revoked = state.permission and not state.permission.allowed
            
            # 3. Bias Conflict (New)
            bias_conflict = False
            if state.act_direction and state.permission and state.permission.bias:
                bias = state.permission.bias
                if (bias == "BULLISH" and state.act_direction == "SHORT") or \
                   (bias == "BEARISH" and state.act_direction == "LONG"):
                    bias_conflict = True

            # 4. Disqualifying Pattern
            disqualified = any(
                a.pattern.value in ACT_DEMOTION_PATTERNS 
                for a in alerts
            )
            
            if timeout or perm_revoked or disqualified or bias_conflict:
                old_reason = state.act_reason
                state.state = State.WATCH
                state.entered_at = current_candle.timestamp
                state.act_reason = None
                
                reason_msg = "ACT Timeout" if timeout else ("Permission Revoked" if perm_revoked else ("Bias Conflict" if bias_conflict else "Disqualifying Pattern"))
                state.reasons.append(f"Demoted ACT->WATCH: {reason_msg} (was {old_reason})")
                
                # Reset patterns and direction
                state.active_patterns = [state.watch_reason] if state.watch_reason else []
                state.act_direction = None

        # --- Demotion Logic: WATCH -> IGNORE ---
        if state and state.state == State.WATCH:
             duration = current_candle.timestamp - state.entered_at
             # DEBUG: Trace WATCH timer to debug "stuck" states
             # logger.debug(f"DEBUG_WATCH_TIMER: {symbol} Duration={duration/1000:.1f}s Max={MAX_WATCH_DURATION_MS/1000}s EnteredAt={state.entered_at}")
             
             if duration > MAX_WATCH_DURATION_MS:
                 logger.info(f"DEMOTION: {symbol} WATCH->IGNORE (Timeout: {duration/1000:.0f}s > {MAX_WATCH_DURATION_MS/1000}s)")
                 old_reason = state.watch_reason
                 state.state = State.IGNORE
                 state.watch_reason = None
                 state.reasons.append(f"Demoted WATCH->IGNORE: Timeout (was {old_reason})")
                 
                 # Reset patterns and direction
                 state.active_patterns = []
                 state.act_direction = None
        
        # --- Alert Gating & Direction Injection ---
        # Suppress alerts unless state is ACT
        if state and state.state != State.ACT:
            return []
        
        # Inject the confirmed direction into the alerts so UI can render it
        if state and state.act_direction:
            for a in alerts:
                a.direction = state.act_direction

        return alerts

    def analyze_permission(self, symbol: str, candles: List[Candle], context: Optional["TimeframeContext"] = None) -> "PermissionSnapshot":
        from models.types import PermissionSnapshot
        if not candles:
             return PermissionSnapshot(symbol, 0, "NEUTRAL", "NORMAL", False, ["No candles"])
        
        current = candles[-1]
        
        # 1. Bias Check (Price vs VWAP)
        # In 15m, if price > VWAP -> Bullish, else Bearish
        bias = "NEUTRAL"
        vwap = current.vwap
        price = current.close
        
        if vwap:
             if price > vwap:
                 bias = "BULLISH"
             elif price < vwap:
                 bias = "BEARISH"
        
        # 2. Volatility Check
        vol_regime = "NORMAL"
        atr_pct = current.atr_percentile if current.atr_percentile is not None else 50.0
        
        if atr_pct < 20:
             vol_regime = "LOW"
        elif atr_pct > 80:
             vol_regime = "HIGH"
        
        # 3. Allowed?
        allowed = True
        reasons = []
        
        if not vwap:
             allowed = False
             reasons.append("Missing VWAP")
             
        # [DECISION_PROOF]
        # logger.info(f"[DECISION_PROOF][{symbol}] Permission: Price={price:.4f} VWAP={vwap if vwap else 0:.4f} ATR%={atr_pct:.1f} -> Bias={bias} Allowed={allowed}")
        
        return PermissionSnapshot(
             symbol=symbol,
             computed_at=current.timestamp,
             bias=bias,
             volatility_regime=vol_regime,
             allowed=allowed,
             reasons=reasons
        )


    # ------------------------------------------------------------------
    # 1m Execution Logic
    # ------------------------------------------------------------------
    def analyze_execution(
        self, 
        symbol: str, 
        candles_1m: List[Candle], 
        state: Optional["StateSnapshot"]
    ) -> List[ExecutionSignal]:
        
        # 1. Gate: Must be in ACT state
        if not state or state.state != State.ACT:
             return []
        
        # 2. Gate: Must have explicit upstream direction
        if not state.act_direction or state.act_direction not in ("LONG", "SHORT"):
             return []
             
        if len(candles_1m) < 5:
             return []
             
        curr = candles_1m[-1]
        
        # 3. Timing Logic (Confirmation Only)
        spot_slope, perp_slope = self._get_flow_slopes(curr)
        
        signal_valid = False
        reason = ""
        strength = 0.0
        
        if state.act_direction == "LONG":
            # REJECT if flow is actively bearish
            # tune this in the future - use or instead of and, increase the thresholds...
            if spot_slope < -0.5 and perp_slope < -0.5:
                return []
            
            # CONFIRM if bullish structure + price ok
            if curr.vwap and curr.close > curr.vwap:
                body = curr.close - curr.open
                if body > 0 and self._is_directional_candle(curr):
                    signal_valid = True
                    reason = "1m Timing: Price > VWAP + Green Candle"
                    strength = min(body / (curr.atr if curr.atr else 1.0), 10.0)

        elif state.act_direction == "SHORT":
            # REJECT if flow is actively bullish
            # tune this in the future - use or instead of and, increase the thresholds...
            if spot_slope > 0.5 and perp_slope > 0.5:
                return []
                
            # CONFIRM if bearish structure + price ok
            if curr.vwap and curr.close < curr.vwap:
                body = curr.open - curr.close
                if body > 0 and self._is_directional_candle(curr):
                    signal_valid = True
                    reason = "1m Timing: Price < VWAP + Red Candle"
                    strength = min(body / (curr.atr if curr.atr else 1.0), 10.0)

        if signal_valid:
             # Calculate proper EXEC score (IGNITION-like scale)
             regime = self._determine_regime(candles_1m, curr)
             exec_score = self._calculate_exec_score(
                 direction=state.act_direction,
                 current=curr,
                 regime=regime,
                 strength=strength
             )
             
             return [
                 ExecutionSignal(
                     symbol=symbol,
                     timestamp=curr.timestamp,
                     price=curr.close,
                     direction=state.act_direction, # Strictly inferred from state
                     reason=reason,
                     strength=strength,
                     score=exec_score
                 )
             ]
             
        return []




    # ------------------------------------------------------------------
    # Flow Regime
    # ------------------------------------------------------------------
    def _get_flow_slopes(self, current: Candle) -> Tuple[float, float]:
        # USE Z-SCORES for Logic Normalization
        # Fallback to 0 if Not Calculated Yet
        spot_z = current.spot_cvd_slope_z if current.spot_cvd_slope_z is not None else 0.0
        perp_z = current.perp_cvd_slope_z if current.perp_cvd_slope_z is not None else 0.0
        return float(spot_z), float(perp_z)

    def _determine_regime(self, candles: List[Candle], current: Candle) -> FlowRegime:

        # Volatility gate: ultra-low vol -> don't over-interpret flow
        if (
            current.atr_percentile is not None
            and current.atr_percentile < MIN_ATR_PERCENTILE
        ):
            return FlowRegime.NEUTRAL

        # 1. Get Z-Scores for Significance/Consensus Logic
        spot_z, perp_z = self._get_flow_slopes(current)
        thresh = FLOW_SLOPE_THRESHOLD # 0.5

        # If both Z-Scores are tiny (below threshold), it's noise
        if abs(spot_z) <= thresh and abs(perp_z) <= thresh:
            return FlowRegime.NEUTRAL

        spot_up = spot_z > thresh
        spot_down = spot_z < -thresh
        perp_up = perp_z > thresh
        perp_down = perp_z < -thresh

        # 2. Consensus Logic (Uses Z-Scores: "Are both significantly moving?")
        if spot_up and perp_up:
            return FlowRegime.BULLISH_CONSENSUS
        if spot_down and perp_down:
            return FlowRegime.BEARISH_CONSENSUS

        # 3. Conflict Logic (Uses Z-Scores: "Are both significantly fighting?")
        if (spot_up and perp_down) or (spot_down and perp_up):
            return FlowRegime.CONFLICT

        # 4. Dominance Logic (Hybrid)
        # We are here because at least one side is Active (Z > 0.5), but they are not in Consensus/Conflict.
        # This implies either:
        # a) One is Active, One is Passive (Z < 0.5)
        # b) Both are Active but in different directions? (No, caught by Conflict)
        # c) Both Active same direction? (No, caught by Consensus)
        
        # So it strictly means: One is Active (Z>0.5), One is Passive (Z<0.5).
        
        # User Feedback: "Coinglass says Perp Led" implies Raw Value is king for labeling.
        # Just because Perp Z is low (due to high vol history) doesn't mean it's not dominating the price action.
        
        raw_spot = current.spot_cvd_slope if current.spot_cvd_slope is not None else 0.0
        raw_perp = current.perp_cvd_slope if current.perp_cvd_slope is not None else 0.0
        
        # Compare Relative Raw Force
        # Caution: Spot/Perp scales might inherently differ? 
        # Usually they are both Quote Volume Delta (USDT). So they should be comparable.
        
        if abs(raw_spot) > abs(raw_perp):
            return FlowRegime.SPOT_DOMINANT
        elif abs(raw_perp) > abs(raw_spot):
            return FlowRegime.PERP_DOMINANT
            
        # Fallback (Equal?)
        result = FlowRegime.SPOT_DOMINANT if abs(spot_z) > abs(perp_z) else FlowRegime.PERP_DOMINANT

        # TRACE LOGGING: Mean Regime Logic
        # Throttled to prevent spam
        current_time = int(time.time())
        # Use a static-like dict on the method or class to track last log?
        # A simple hack is logging active patterns: 
        if not hasattr(self, "_last_log_time"):
             self._last_log_time = {}
        
        last_log = self._last_log_time.get(current.symbol, 0)
        if current_time > last_log:
             logger.debug(f"[TRACE][{current.symbol}] Regime: {result.name} (SpotZ={spot_z:.2f} PerpZ={perp_z:.2f})")
             self._last_log_time[current.symbol] = current_time
        
        return result
            
        # DEBUG: Log to console to show user life signs
        # Import logger at top or use printed logic if logger not available? 
        # Analyzer has 'import logging' typically.
        # Let's assume 'logger' is available globally or I need to get it.
        # Actually, self.logger might not exist.
        import logging
        logger = logging.getLogger("scanner")
        logger.info(f"REGIME_DEBUG: {current.symbol} Spot={spot_slope:.3f} Perp={perp_slope:.3f} ATR%={current.atr_percentile} -> {result}")
        
        return result

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

    def _bullish_flow_ok(self, candles: List[Candle], current: Candle, regime: FlowRegime) -> bool:
        spot_slope, perp_slope = self._get_flow_slopes(current)
        if regime == FlowRegime.BULLISH_CONSENSUS:
            return True
        if regime == FlowRegime.SPOT_DOMINANT and spot_slope > 0:
            return True
        if regime == FlowRegime.PERP_DOMINANT and perp_slope > 0:
            return True
        return False

    def _bearish_flow_ok(self, candles: List[Candle], current: Candle, regime: FlowRegime) -> bool:
        spot_slope, perp_slope = self._get_flow_slopes(current)
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
    def _check_vwap_reclaim(self, candles: List[Candle], curr: Candle, regime: FlowRegime) -> bool:
        if len(candles) < 2:
            return False

        # curr is passed in
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

        if bullish_reclaim and self._bullish_flow_ok(candles, curr, regime):
            return True

        if bearish_reclaim and self._bearish_flow_ok(candles, curr, regime):
            return True

        return False

    # ------------------------------------------------------------------
    # Pattern B: Ignition
    # ------------------------------------------------------------------
    def _check_ignition(self, candles: List[Candle], curr: Candle, regime: FlowRegime) -> bool:
        if len(candles) < 7:  # cluster + current + a bit of context
            return False

        # curr is passed in
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
        if is_bull and not self._bullish_flow_ok(candles, curr, regime):
            return False
        if is_bear and not self._bearish_flow_ok(candles, curr, regime):
            return False

        return True

    # ------------------------------------------------------------------
    # Pattern C: Post-Impulse Pullback
    # ------------------------------------------------------------------
    def _check_post_impulse_pullback(
        self, candles: List[Candle], curr: Candle, regime: FlowRegime
    ) -> bool:
        # curr is passed in
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
            if not self._bullish_flow_ok(candles, curr, regime):
                return False
        else:  # "down"
            if curr.close > curr.vwap * (1 + self.VWAP_TOLERANCE * 3):
                return False
            if not self._bearish_flow_ok(candles, curr, regime):
                return False

        return True

    # ------------------------------------------------------------------
    # Pattern D: Trap (Stop Run)
    # ------------------------------------------------------------------
    def _check_trap(self, candles: List[Candle], curr: Candle, regime: FlowRegime) -> bool:
        # curr is passed in

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
        self, candles: List[Candle], curr: Candle, regime: FlowRegime, already_trap: bool
    ) -> bool:
        if already_trap:
            # Trap is a stronger pattern; don't double-report as failed breakout
            return False

        # curr is passed in

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
        self, pattern: PatternType, candles: List[Candle], current: Candle, regime: FlowRegime
    ) -> float:
        # current is passed in
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

    def _calculate_exec_score(
        self, direction: str, current: Candle, regime: FlowRegime, strength: float
    ) -> float:
        """
        Calculate score for EXEC (1m execution) signals using the same scale as IGNITION.
        
        Args:
            direction: "LONG" or "SHORT"
            current: The 1m candle being analyzed
            regime: Current flow regime
            strength: Body/ATR ratio (0-10 range from analyze_execution)
        
        Returns:
            Score in 0-100 range, comparable to IGNITION scores (typically 60-90+)
        """
        # Start with BASE_PATTERN (same as IGNITION)
        score = float(SCORING_WEIGHTS.get("BASE_PATTERN", 0.0))  # 50
        
        # Flow Alignment Bonus
        # Check if the regime supports the direction
        spot_z, perp_z = self._get_flow_slopes(current)
        
        if direction == "LONG":
            flow_aligned = (
                regime == FlowRegime.BULLISH_CONSENSUS or
                (regime == FlowRegime.SPOT_DOMINANT and spot_z > 0) or
                (regime == FlowRegime.PERP_DOMINANT and perp_z > 0)
            )
            # Extra bonus for strong aligned z-scores
            z_strength = max(spot_z, perp_z)
        else:  # SHORT
            flow_aligned = (
                regime == FlowRegime.BEARISH_CONSENSUS or
                (regime == FlowRegime.SPOT_DOMINANT and spot_z < 0) or
                (regime == FlowRegime.PERP_DOMINANT and perp_z < 0)
            )
            # Extra bonus for strong aligned z-scores (magnitude)
            z_strength = abs(min(spot_z, perp_z))
        
        if flow_aligned:
            score += SCORING_WEIGHTS.get("FLOW_ALIGNMENT", 0.0)  # +20
            # Additional z-score strength bonus (0-5 points for strong z > 1.0)
            if z_strength > 1.0:
                score += min(z_strength - 1.0, 1.0) * 5.0
        elif regime in (FlowRegime.SPOT_DOMINANT, FlowRegime.PERP_DOMINANT):
            # Partial context bonus even without perfect alignment
            score += SCORING_WEIGHTS.get("CONTEXT", 0.0) * 0.5  # +7.5
        elif regime == FlowRegime.NEUTRAL:
            # Penalize neutral flow
            score -= 5.0
        elif regime == FlowRegime.CONFLICT:
            # Conflict is risky but not disqualifying if we passed flow gates
            pass  # No bonus, no penalty
        
        # Volatility Contribution
        if current.atr_percentile is not None:
            if current.atr_percentile > 80:
                score += SCORING_WEIGHTS.get("VOLATILITY", 0.0)  # +15
            elif current.atr_percentile > 50:
                score += SCORING_WEIGHTS.get("VOLATILITY", 0.0) * 0.5  # +7.5
        
        # Magnitude/Strength Contribution (the original body/ATR ratio)
        # strength is 0-10 range, we'll give up to 10 points for strong moves
        if strength > 0:
            magnitude_bonus = min(strength, 5.0) * 2.0  # 0-10 points
            score += magnitude_bonus
        
        # Clamp and floor
        score = max(0.0, min(100.0, score))
        return score

    # ------------------------------------------------------------------
    # Alert factory
    # ------------------------------------------------------------------
    def _create_alert(
        self,
        symbol: str,
        pattern: PatternType | ExecutionType,
        regime: FlowRegime,
        candle: Candle,
        score: float,
        context: Optional["TimeframeContext"] = None,
        direction: Optional[str] = None
    ) -> Alert:
        tf_name = context.name if context else "3m"
        # USE RAW SLOPES for UI Display (Matches Chart Intuition)
        # Logic uses Z-Scores, but Humans check charts.
        spot_slope = candle.spot_cvd_slope if candle.spot_cvd_slope is not None else 0.0
        perp_slope = candle.perp_cvd_slope if candle.perp_cvd_slope is not None else 0.0
        
        return Alert(
            timestamp=int(time.time() * 1000),
            candle_timestamp=candle.timestamp,
            symbol=symbol,
            pattern=pattern,
            score=score,
            flow_regime=regime,
            price=candle.close,
            message=f"{pattern.value} detected ({regime.value})",
            timeframe=tf_name,
            direction=direction,
            spot_slope=float(spot_slope),
            perp_slope=float(perp_slope),
            # Visual Fields (Z-Scores)
            spot_slope_z=float(candle.spot_cvd_slope_z if candle.spot_cvd_slope_z is not None else 0.0),
            perp_slope_z=float(candle.perp_cvd_slope_z if candle.perp_cvd_slope_z is not None else 0.0),
            # Debug
            atr_percentile=candle.atr_percentile,
            spot_cvd=candle.spot_cvd,
            perp_cvd=candle.perp_cvd
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
        current_candle = candles[-1]
        regime = self._determine_regime(candles, current_candle)
        spot_slope, perp_slope = self._get_flow_slopes(current_candle)
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
            curr = current_candle
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

            if bullish and not self._bullish_flow_ok(candles, curr, regime):
                return False, "Bullish reclaim but flow not bullish"

            if bearish and not self._bearish_flow_ok(candles, curr, regime):
                return False, "Bearish reclaim but flow not bearish"

            if bullish or bearish:
                return True, "Reclaim detected"

            return False, "Conditions did not form valid reclaim"

        def dbg_ignition():
            curr = current_candle

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

            if curr.close > curr.open and not self._bullish_flow_ok(candles, curr, regime):
                return False, "Bullish candle but flow not bullish"

            if curr.close < curr.open and not self._bearish_flow_ok(candles, curr, regime):
                return False, "Bearish candle but flow not bearish"

            return True, "Ignition confirmed"

        def dbg_pullback():
            curr = current_candle

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
                if not self._bullish_flow_ok(candles, curr, regime):
                    return False, "Flow not bullish"
            else:
                if not self._bearish_flow_ok(candles, curr, regime):
                    return False, "Flow not bearish"

            return True, f"Pullback OK (dir={impulse_dir})"

        def dbg_trap():
            curr = current_candle
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
            curr = current_candle

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
        res = out["patterns"]["VWAP_RECLAIM"]
        if not res["ok"]:
            snapshot = build_snapshot(
                symbol=symbol,
                pattern=PatternType.VWAP_RECLAIM,
                candle=current_candle,
                regime=regime,
                score=0.0,
                passed=False,
                failed_reason=res["reason"],
                debug_data=out,
            )
            write_snapshot(snapshot)

        out["patterns"]["IGNITION"] = _dbg_wrapper("IGNITION", dbg_ignition)
        res = out["patterns"]["IGNITION"]
        if not res["ok"]:
            snapshot = build_snapshot(
                symbol=symbol,
                pattern=PatternType.IGNITION,
                candle=current_candle,
                regime=regime,
                score=0.0,
                passed=False,
                failed_reason=res["reason"],
                debug_data=out,
            )
            write_snapshot(snapshot)

        out["patterns"]["PULLBACK"] = _dbg_wrapper("PULLBACK", dbg_pullback)
        res = out["patterns"]["PULLBACK"]
        if not res["ok"]:
            snapshot = build_snapshot(
                symbol=symbol,
                pattern=PatternType.PULLBACK,
                candle=current_candle,
                regime=regime,
                score=0.0,
                passed=False,
                failed_reason=res["reason"],
                debug_data=out,
            )
            write_snapshot(snapshot)

        out["patterns"]["TRAP"] = _dbg_wrapper("TRAP", dbg_trap)   
        res = out["patterns"]["TRAP"]
        if not res["ok"]:
            snapshot = build_snapshot(
                symbol=symbol,
                pattern=PatternType.TRAP,
                candle=current_candle,
                regime=regime,
                score=0.0,
                passed=False,
                failed_reason=res["reason"],
                debug_data=out,
            )
            write_snapshot(snapshot)

        out["patterns"]["FAILED_BREAKOUT"] = _dbg_wrapper("FAILED_BREAKOUT", dbg_failed)
        res = out["patterns"]["FAILED_BREAKOUT"]
        if not res["ok"]:
            snapshot = build_snapshot(
                symbol=symbol,
                pattern=PatternType.FAILED_BREAKOUT,
                candle=current_candle,
                regime=regime,
                score=0.0,
                passed=False,
                failed_reason=res["reason"],
                debug_data=out,
            )
            write_snapshot(snapshot)

        return out
