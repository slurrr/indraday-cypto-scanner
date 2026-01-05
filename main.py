import os
import sys

# --- PRE-IMPORT CONFIGURATION ---
INSTANCE_ID = os.environ.get("SCANNER_INSTANCE", os.getpid())
# Set the log file for all components to match this instance
os.environ["SCANNER_LOG_FILE"] = f"utils/scanner_{INSTANCE_ID}.log"

from config.settings import ANALYZER_DEBUG, ENABLE_EXEC_ALERTS
import json
from typing import List
from rich.console import Console
from rich.live import Live
from collections import OrderedDict
import copy

# Keep a real stdout for Rich to use
REAL_STDOUT = sys.__stdout__

# Create Rich console bound to REAL terminal output
console = Console(file=REAL_STDOUT, soft_wrap=True)

import time
import signal
import queue
import threading

# Core imports (Delayed until after env var set)
from ui.console import ConsoleUI, UIStatus
from config.settings import SYMBOLS
from data.binance_client import BinanceClient
from concurrent.futures import ThreadPoolExecutor
from core.data_processor import DataProcessor
from core.analyzer import Analyzer
from core.indicators import calculate_indicators_full, update_latest_candle, update_indicators_from_index
from models.types import Trade, Alert, TimeframeContext, State, StateSnapshot, ExecutionType
from utils.logger import setup_logger

LOG_FILE = os.environ["SCANNER_LOG_FILE"]
DEBUG_LOG_FILE = f"utils/debug_scanner_{INSTANCE_ID}.log"

logger = setup_logger(
    "scanner",
    log_file=LOG_FILE,
)

debug_logger = setup_logger(
    "debug_scanner",
    log_file=DEBUG_LOG_FILE,
    level="DEBUG",
)

def format_candle_log(c) -> str:
    """Format candle data for detailed logging."""
    vwap = f"{c.vwap:.4f}" if c.vwap else "-"
    atr = f"{c.atr:.4f}" if c.atr else "-"
    atr_pct = f"{c.atr_percentile:.1f}" if c.atr_percentile is not None else "-"
    spot_slope = f"{c.spot_cvd_slope:.3f}" if c.spot_cvd_slope is not None else "-"
    perp_slope = f"{c.perp_cvd_slope:.3f}" if c.perp_cvd_slope is not None else "-"
    spot_z = f"{c.spot_cvd_slope_z:.2f}" if c.spot_cvd_slope_z is not None else "-"
    perp_z = f"{c.perp_cvd_slope_z:.2f}" if c.perp_cvd_slope_z is not None else "-"
    return (
        f"Candle[ts={c.timestamp} O={c.open:.4f} H={c.high:.4f} L={c.low:.4f} C={c.close:.4f} "
        f"VWAP={vwap} ATR={atr} ATR%={atr_pct} "
        f"SpotCVD={c.spot_cvd:.1f} PerpCVD={c.perp_cvd:.1f} "
        f"SpotSlope={spot_slope} PerpSlope={perp_slope} "
        f"SpotZ={spot_z} PerpZ={perp_z}]"
    )

def main():
    logger.info("Starting Intraday Flow Scanner...")

    # Initialize Timeframe Context
    from config.settings import CANDLE_TIMEFRAME_MINUTES
    tf_context = TimeframeContext(
        name=f"{CANDLE_TIMEFRAME_MINUTES}m",
        interval_ms=CANDLE_TIMEFRAME_MINUTES * 60 * 1000
    )
    logger.info(f"Active Timeframe: {tf_context.name} ({tf_context.interval_ms}ms)")

    # 15m Timeframe Context (Read-Only)
    from config.settings import TIMEFRAME_15M
    tf_context_15m = TimeframeContext(
        name=TIMEFRAME_15M,
        interval_ms=15 * 60 * 1000
    )
    logger.info(f"Active Timeframe: {tf_context_15m.name} ({tf_context_15m.interval_ms}ms)")

    # 1m Timeframe Context (Execution)
    from config.settings import TIMEFRAME_1M
    tf_context_1m = TimeframeContext(
        name=TIMEFRAME_1M,
        interval_ms=1 * 60 * 1000
    )
    logger.info(f"Active Timeframe: {tf_context_1m.name} ({tf_context_1m.interval_ms}ms)")

    # Components
    ui = ConsoleUI(console=console)
    data_processor = DataProcessor(status_sink=ui, context=tf_context)
    data_processor_15m = DataProcessor(status_sink=ui, context=tf_context_15m)
    data_processor_1m = DataProcessor(status_sink=ui, context=tf_context_1m)
    
    analyzer = Analyzer()
    ui.dirty = True

    # Per-symbol locks to prevent race conditions between Spot and Perp threads
    # without blocking unrelated symbols
    symbol_locks = {s: threading.Lock() for s in SYMBOLS}
    
    # Thread Pool for Reconciliation Tasks
    # Limits concurrent network/processing tasks to prevent Segfaults/resource exhaustion
    reconciliation_executor = ThreadPoolExecutor(max_workers=40, thread_name_prefix="ReconcileWorker")

    # State Management (Step 6)
    # Initialize state for each symbol
    symbol_states: Dict[str, StateSnapshot] = {
        s: StateSnapshot(symbol=s, state=State.WATCH, entered_at=int(time.time() * 1000)) for s in SYMBOLS
    }

    # Deduplication Set: Stores (symbol, pattern_name, candle_timestamp)
    # Changed to OrderedDict to allow FIFO eviction
    sent_alerts = OrderedDict()
    sent_alerts_lock = threading.Lock()

    def handle_alerts(alerts: List[Alert]):
        new_unique_alerts = []
        with sent_alerts_lock:
            for alert in alerts:
                # Deduplication Key: Symbol + Pattern + Candle Timestamp
                # This ensures we don't alert twice for the exact same event on the exact same candle
                key = (alert.symbol, alert.pattern.value, alert.candle_timestamp)
                
                if key not in sent_alerts:
                    sent_alerts[key] = True # Mark as seen
                    new_unique_alerts.append(alert)
                    
                    # Enforce Size Cap (FIFO)
                    if len(sent_alerts) > 10000:
                        sent_alerts.popitem(last=False)
        
        
        for alert in new_unique_alerts:
            ui.add_alert(alert)
            logger.info(f"ALERT: {alert}")
        
        return new_unique_alerts

    # --- Analysis Worker Implementation ---
    analysis_queue = queue.Queue()
    queued_symbols = set()
    queue_lock = threading.Lock()

    def analysis_worker():
        while True:
            symbol = analysis_queue.get()
            try:
                # Remove from set to allow re-queuing
                with queue_lock:
                    queued_symbols.discard(symbol)
                
                # Perform Analysis with lock
                with symbol_locks[symbol]:
                    HISTORY_COPY_DEPTH = 300 # Don't need full history for analysis
                    
                    # 1. Get history (Spot)
                    # DataProcessor.get_history returns CLOSED candles.
                    raw_history = data_processor.get_history(symbol, source='spot')
                    
                    # Create a working copy for analysis
                    # We need to append the ACTIVE candle to this list to analyze live price action.
                    if raw_history:
                        history = raw_history[-HISTORY_COPY_DEPTH:] 
                    else:
                        history = []

                    # CRITICAL FIX: Validate indicator integrity on slice before analysis
                    # If any closed candles are missing indicators, repair the chain
                    if history and len(history) >= 30:
                        missing_count = sum(1 for c in history[-30:] if c.atr_percentile is None)
                        if missing_count > 0:
                            logger.warning(f"INDICATOR_REPAIR: {symbol} has {missing_count}/30 candles missing atr_percentile. Running full recalc.")
                            calculate_indicators_full(history, context=tf_context)

                    # 1a. Inject ACTIVE Spot candle
                    # This is critical. Without this, we analyze 3m-old data.
                    if symbol in data_processor.active_spot_candles:
                         active_spot = data_processor.active_spot_candles[symbol]
                         # Ensure continuity (timestamp should be > last history)
                         if not history or active_spot.timestamp > history[-1].timestamp:
                             # We must copy active_spot because update_latest_candle mutates it
                             # and we don't want to corrupt the source of truth in DataProcessor before close.
                             # Actually, mutating the active candle's indicators is fine/good, 
                             # but let's be safe and copy to avoid thread race on fields.
                             import copy
                             active_spot_snap = copy.copy(active_spot)
                             history.append(active_spot_snap)
                             
                    # 2. Update Indicators (Incremental - FAST)
                    # This will calculate slope/vwap on the just-appended active tip
                    update_latest_candle(history, context=tf_context)

                    # TRACE LOGGING: Spot Injection (Throttled)
                    # Moved after update_latest_candle to show calculated slopes
                    if symbol in data_processor.active_spot_candles and history:
                         now_sec = int(time.time())
                         # We can use a module-level dict for throttling since we are in a closure/worker
                         if not hasattr(analysis_worker, "_last_log"): analysis_worker._last_log = {}
                         
                         if now_sec > analysis_worker._last_log.get(f"{symbol}_spot", 0):
                              last_s = history[-1]
                              # Extract last 5 cumulative CVDs for verification
                              cvd_window = [round(c.cum_spot_cvd, 1) for c in history[-5:]] if hasattr(last_s, 'cum_spot_cvd') else []
                              # logger.debug(f"[TRACE][{symbol}] Injected Active Spot: C={last_s.close} SCVD={last_s.spot_cvd} CumWindow={cvd_window} Slope={last_s.spot_cvd_slope:.3f}")
                              analysis_worker._last_log[f"{symbol}_spot"] = now_sec
                    
                    # 3. Analyze
                    current_state = symbol_states[symbol]
                    # Fetch Perp history for flow context
                    raw_perp_history = data_processor.get_history(symbol, source='perp')
                    # Create copy to allow injection
                    if raw_perp_history:
                        perp_history = list(raw_perp_history[-HISTORY_COPY_DEPTH:])
                    else:
                        perp_history = []
                        
                    # 3b. Inject ACTIVE Perp Candle
                    # Same logic as Spot: we need the live flow!
                    if symbol in data_processor.active_perp_candles:
                        active_perp = data_processor.active_perp_candles[symbol]
                        # Ensure continuity
                        if not perp_history or active_perp.timestamp > perp_history[-1].timestamp:
                            import copy
                            active_perp_snap = copy.copy(active_perp)
                            perp_history.append(active_perp_snap)
                    
                    # 3c. Update Indicators for Perp Tip
                    # Vital to get the slope on the active candle
                    if perp_history:
                         # We can't use update_latest_candle easily if the previous candles don't have cumulative sums
                         # stored?
                         # DataProcessor.get_history returns candles that DO have cumulative sums if they were 
                         # initialized/updated properly?
                         # Yes, init_hybrid and reconcile_candle update them.
                         # But active_perp is raw.
                         # update_latest_candle handles incremental update using prev candle.
                         update_latest_candle(perp_history, context=tf_context)
                         
                         # TRACE LOGGING: Perp Injection (Throttled)
                         now_sec = int(time.time())
                         if not hasattr(analysis_worker, "_last_log"): analysis_worker._last_log = {}
                         
                         if now_sec > analysis_worker._last_log.get(f"{symbol}_perp", 0):
                             last_p = perp_history[-1]
                             # Extract last 5 cumulative PERP CVDs
                             cvd_window_p = [round(c.cum_perp_cvd, 1) for c in perp_history[-5:]] if hasattr(last_p, 'cum_perp_cvd') else []
                             logger.debug(f"[TRACE][{symbol}] Injected Active Perp: C={last_p.close} SCVD={last_p.spot_cvd} PCVD={last_p.perp_cvd} CumWindow={cvd_window_p} Slope={last_p.perp_cvd_slope:.3f}")
                             analysis_worker._last_log[f"{symbol}_perp"] = now_sec
                    
                    # DEBUG: Trace history length
                    if len(history) < 30:
                        logger.warning(f"DEBUG_HISTORY: {symbol} has {len(history)} bars (Insufficient!). ActiveSpot found: {symbol in data_processor.active_spot_candles}")

                    # --- CRITICAL FIX: Live 15m Permission Update ---
                    # Update permission NOW using the active 15m candle so 3m analysis sees fresh Bias.
                    try:
                        # 1. Fetch 15m History (Spot)
                        raw_15m = data_processor_15m.get_history(symbol, source='spot')
                        hist_15m = list(raw_15m[-100:]) if raw_15m else [] # Copy last 100
                        
                        # 2. Inject Active 15m Candle
                        if symbol in data_processor_15m.active_spot_candles:
                             active_15m = data_processor_15m.active_spot_candles[symbol]
                             # Ensure continuity
                             if not hist_15m or active_15m.timestamp > hist_15m[-1].timestamp:
                                 import copy
                                 active_15m_snap = copy.copy(active_15m)
                                 hist_15m.append(active_15m_snap)
                                 
                        # 3. Update Indicators for the Tip (VWAP needed for Permission)
                        if hist_15m:
                             update_latest_candle(hist_15m, context=tf_context_15m)
                             
                             # 4. Analyze & Update State
                             perm_snapshot = analyzer.analyze_permission(symbol, hist_15m, context=tf_context_15m)
                             
                             # Update the SHARED state object (thread-safe enough as we are in the worker)
                             # Note: analyze() below uses 'current_state' referentially, so it WILL see this update!
                             current_state.permission = perm_snapshot
                             
                    except Exception as e:
                        logger.error(f"Error updating live 15m permission for {symbol}: {e}")

                    alerts = analyzer.analyze(symbol, history, context=tf_context, state=current_state, perp_candles=perp_history)

                    
                    # Debug logic
                    if ANALYZER_DEBUG:
                        dbg = analyzer.debug_analyze(symbol, history)
                        for pat, result in dbg["patterns"].items():
                            if not result["ok"] and any(k in result["reason"].lower() for k in ["not", "missing", "near", "flow"]):
                                debug_logger.debug(f"[DEBUG][{symbol}] Almost {pat}: {result['reason']}")
                                # Log candle data for almost-alerts
                                if history:
                                    debug_logger.debug(f"[DEBUG][{symbol}] {format_candle_log(history[-1])}")

                    # 4. Handle Alerts
                    if alerts:
                        debug_logger.debug(
                            f"[DEBUG_BARS] {symbol} 3m bars={len(history)} "
                            f"ready={len(history) >= 120}"
                        )
                        new_alerts = handle_alerts(alerts)
                        # Log candle data for each new alert
                        if new_alerts and history:
                            candle = history[-1]
                            for a in new_alerts:
                                logger.info(f"ALERT_CANDLE: {a.symbol}|{a.pattern.value} {format_candle_log(candle)}")
                        
            except Exception as e:
                logger.error(f"Error in analysis worker for {symbol}: {e}")
            finally:
                analysis_queue.task_done()

    # Start worker thread
    threading.Thread(target=analysis_worker, daemon=True, name="AnalysisWorker").start()

    # Function to reconcile candle in background (Shared Logic)
    def reconcile_candle(symbol: str, timestamp: int, processor: DataProcessor, context: TimeframeContext, callback=None):
        # Full Segregation Reconcile: We must reconcile BOTH streams or just the one we care about.
        # To be safe, we mainly care about Spot for analysis, but Perp for data integrity.
        # Let's reconcile BOTH.
        
        # 1. Spot Reconcile
        spot_candle = client.fetch_latest_candle(symbol, context=context, source='spot')
        if spot_candle and spot_candle.timestamp == timestamp:
            with symbol_locks[symbol]:
                processor.update_history_candle(symbol, spot_candle, source='spot')
                history_spot = processor.get_history(symbol, source='spot')
                idx = next((i for i, c in enumerate(history_spot) if c.timestamp == timestamp), -1)
                if idx != -1: update_indicators_from_index(history_spot, idx, context=context)

        # 2. Perp Reconcile
        perp_candle = client.fetch_latest_candle(symbol, context=context, source='perp')
        if perp_candle and perp_candle.timestamp == timestamp:
            with symbol_locks[symbol]:
                processor.update_history_candle(symbol, perp_candle, source='perp')
                history_perp = processor.get_history(symbol, source='perp')
                idx = next((i for i, c in enumerate(history_perp) if c.timestamp == timestamp), -1)
                if idx != -1: update_indicators_from_index(history_perp, idx, context=context)

        # 3. Callback (Analysis on Spot as priority)
        if callback:
             spot_hist = processor.get_history(symbol, source='spot')
             callback(symbol, spot_hist, context)

    # --- Analysis Callbacks ---
    def analyze_3m(symbol: str, history: List, context: TimeframeContext):
        # Retrieve state
        state = symbol_states[symbol]
        
        # Get Perp History for Context
        perp_history = data_processor.get_history(symbol, source='perp')
        
        # Ensure the latest closed Perp candle has indicators (slope) calculated
        # (DataProcessor only aggregates raw data; indicators must be updated)
        # CRITICAL FIX: Use calculate_indicators_full to rebuild the entire cumulative chain.
        # update_latest_candle is not enough because the history accumulated by DataProcessor
        # has never had its cumulative sums (cum_perp_cvd) calculated.
        if perp_history:
            calculate_indicators_full(perp_history, context=context)

        # --- FIX: REGIME RACE CONDITION ---
        # If the latest Spot candle matches the ACTIVE Perp candle (not yet closed),
        # we must include the Active Perp candle to get the correct flow slope.
        # Otherwise, we might be looking at an old Perp candle and miss the current move.
        if symbol in data_processor.active_perp_candles:
            active_perp = data_processor.active_perp_candles[symbol]
            # Match timestamps (Spot Analysis triggered by closed spot candle at 'timestamp')
            # 'history[-1]' should be the closed spot candle we are analyzing.
            if history and history[-1].timestamp == active_perp.timestamp:
                # We need a list we can mutate safely
                perp_history = list(perp_history) # Shallow copy of list
                
                # Clone active perp to avoid race conditions with network thread updating it
                active_perp_snap = copy.copy(active_perp)
                perp_history.append(active_perp_snap)
                
                # Calculate slope for this new tail (O(1))
                update_latest_candle(perp_history, context=context)
                
                # DEBUG: Why is slope -0.0? Use the object in the list which has the calculation
                logger.debug(f"DEBUG_PERP_INJECT: {symbol} LastTS={perp_history[-1].timestamp} CVD={perp_history[-1].perp_cvd} Slope={perp_history[-1].perp_cvd_slope}")

        # LOG SPOT SLOPE FOR VERIFICATION
        if history and history[-1].spot_cvd_slope is not None:
            logger.info(f"DEBUG_SPOT_SLOPE: {symbol} LastTS={history[-1].timestamp} CVD={history[-1].spot_cvd} Slope={history[-1].spot_cvd_slope}")


        # CRITICAL FIX: Self-healing for un-initialized history
        # If we have history but indicators are missing (e.g. init failure), force recalc/repair
        if history and len(history) > 30:
            last = history[-1]
            if last.spot_cvd_slope is None or last.atr_percentile is None:
                logger.warning(f"DATA_INTEGRITY: {symbol} has {len(history)} bars but missing indicators (Slope={last.spot_cvd_slope}, ATR%={last.atr_percentile}). Forcing full recalculation.")
                calculate_indicators_full(history, context=context)
                
                # Verify repair
                if history[-1].spot_cvd_slope is None:
                     logger.error(f"DATA_INTEGRITY: Recalculation FAILED for {symbol}")

        reconciled_alerts = analyzer.analyze(symbol, history, context=context, state=state, perp_candles=perp_history)
        
        # State update (placeholder) done inside analyze via side-effect on 'state' object
        
        if reconciled_alerts:
            debug_logger.debug(
                f"[DEBUG_BARS] {symbol} 3m bars={len(history)} ready={len(history) >= 120}"
            )
            new_alerts = handle_alerts(reconciled_alerts)
            # Log candle data for reconciled alerts
            if new_alerts and history:
                for a in new_alerts:
                    logger.info(f"ALERT_CANDLE: {a.symbol}|{a.pattern.value} {format_candle_log(history[-1])}")

    def analyze_15m(symbol: str, history: List, context: TimeframeContext):
        perm_snapshot = analyzer.analyze_permission(symbol, history, context=context)
        
        # Attach permission to state
        if symbol in symbol_states:
             symbol_states[symbol].permission = perm_snapshot
             
        # CRITICAL FIX: Self-healing for un-initialized history (15m)
        if history and len(history) > 30:
            last = history[-1]
            if last.atr is None: # Permission logic uses ATR massively
                 logger.warning(f"DATA_INTEGRITY_15M: {symbol} 15m history un-initialized. Forcing recalc.")
                 calculate_indicators_full(history, context=context)
                 # Re-run permission? It already ran with bad data...
                 # We should re-run it.
                 perm_snapshot = analyzer.analyze_permission(symbol, history, context=context)
                 if symbol in symbol_states:
                     symbol_states[symbol].permission = perm_snapshot

        logger.info(f"[15m] Permission Snapshot for {symbol}: {perm_snapshot} -> State Updated")

    def analyze_1m(symbol: str, history: List, context: TimeframeContext):
        # Early exit if EXEC alerts are disabled
        if not ENABLE_EXEC_ALERTS:
            return
            
        # Retrieve state
        state = symbol_states.get(symbol)
        
        # --- PERP INJECTION FOR 1M ---
        # --- ACTIVE CANDLE INJECTION (1m) ---
        # Ensure we are analyzing the LIVE candle, not just the closed one.
        HISTORY_COPY_DEPTH = 60
        
        # 1. Inject Active Spot
        # We need a copy because we might append the active candle
        history = list(history[-HISTORY_COPY_DEPTH:]) if history else []
        
        if symbol in data_processor_1m.active_spot_candles:
             active_spot = data_processor_1m.active_spot_candles[symbol]
             if not history or active_spot.timestamp > history[-1].timestamp:
                 import copy
                 active_spot_snap = copy.copy(active_spot)
                 history.append(active_spot_snap)
                 
                 # Update indicators for the new tip
                 update_latest_candle(history, context=context)

        # 2. Get Perp History & Inject Active Perp
        raw_perp_inv = data_processor_1m.get_history(symbol, source='perp')
        if raw_perp_inv:
             perp_history = list(raw_perp_inv[-HISTORY_COPY_DEPTH:])
        else:
             perp_history = []
             
        if symbol in data_processor_1m.active_perp_candles:
             active_perp = data_processor_1m.active_perp_candles[symbol]
             if not perp_history or active_perp.timestamp > perp_history[-1].timestamp:
                  import copy
                  active_perp_snap = copy.copy(active_perp)
                  perp_history.append(active_perp_snap)

        # 3. Calculate Indicators for Perp (Full/Partial)
        if perp_history:
             # Just update latest for speed if history exists, else full
             if len(perp_history) > 1 and perp_history[-2].perp_cvd_slope is not None:
                  update_latest_candle(perp_history, context=context)
             else:
                  calculate_indicators_full(perp_history, context=context)
             
             # Match and Inject Slope into Current Spot
             current = history[-1]
             # Match timestamps
             if perp_history[-1].timestamp == current.timestamp:
                 current.perp_cvd_slope = perp_history[-1].perp_cvd_slope
                 current.perp_cvd = perp_history[-1].perp_cvd
                 logger.debug(f"DEBUG_PERP_INJECT_1M: {symbol} Slope={current.perp_cvd_slope}")
        
        # CRITICAL FIX: Self-healing for un-initialized history (1m)
        if history and len(history) > 30:
            last = history[-1]
            if last.spot_cvd_slope is None or last.atr_percentile is None:
                logger.warning(f"DATA_INTEGRITY_1M: {symbol} has {len(history)} bars but missing indicators. Forcing full recalculation.")
                calculate_indicators_full(history, context=context)

        # Run execution analysis (gated by ACT state inside method)
        exec_signals = analyzer.analyze_execution(symbol, history, state=state)
        
        if exec_signals:
            for sig in exec_signals:
                logger.info(f"EXECUTION SIGNAL: {sig}")
                
                # Emit as Alert for UI visibility
                alert = Alert(
                    timestamp=int(time.time() * 1000),
                    candle_timestamp=sig.timestamp,
                    symbol=sig.symbol,
                    pattern=ExecutionType.EXEC,
                    score=sig.score,  # Use properly calculated EXEC score (IGNITION-like scale)
                    flow_regime=analyzer._determine_regime(history, history[-1]), # roughly
                    price=sig.price,
                    message=f"{sig.direction}: {sig.reason}",
                    timeframe="1m",
                    direction=sig.direction,
                    # USE NORMALIZED Z-SCORES
                    spot_slope=float(history[-1].spot_cvd_slope_z if history[-1].spot_cvd_slope_z is not None else 0.0),
                    perp_slope=float(history[-1].perp_cvd_slope_z if history[-1].perp_cvd_slope_z is not None else 0.0),
                    # Debug Fields
                    atr_percentile=history[-1].atr_percentile,
                    spot_cvd=history[-1].spot_cvd,
                    perp_cvd=history[-1].perp_cvd
                )
                new_alerts = handle_alerts([alert])
                # Log candle data for execution signals
                if new_alerts and history:
                    logger.info(f"ALERT_CANDLE: {sig.symbol}|EXEC {format_candle_log(history[-1])}")



    # Callback for new trades
    # Callback for new trades
    def on_trade(trade: Trade):
        # Acquire lock for this specific symbol
        # This prevents Spot and Perp threads from modifying the same symbol's history concurrently
        # providing thread safety without global blocking.
        with symbol_locks[trade.symbol]:
            # --- 3m Processing ---
            closed_spot, closed_perp = data_processor.process_trade(trade)
            
            # --- FAST PATH: Immediate Analysis (3m) ---
            # Trigger analysis primarily on SPOT close (Chart Parity)
            if closed_spot:
                # Offload heavy analysis to worker
                symbol = closed_spot.symbol
                with queue_lock:
                    if symbol not in queued_symbols:
                        queued_symbols.add(symbol)
                        analysis_queue.put(symbol)

                # --- SLOW PATH: Background Reconciliation (3m) ---
                reconciliation_executor.submit(
                    reconcile_candle, 
                    symbol, closed_spot.timestamp, data_processor, tf_context, analyze_3m
                )

            # --- 15m Processing ---
            closed_spot_15, closed_perp_15 = data_processor_15m.process_trade(trade)
            if closed_spot_15:
                # No fast path for 15m yet, just reconciliation/permission check
                reconciliation_executor.submit(
                    reconcile_candle,
                    closed_spot_15.symbol, closed_spot_15.timestamp, data_processor_15m, tf_context_15m, analyze_15m
                )

            # --- 1m Processing ---
            closed_spot_1m, closed_perp_1m = data_processor_1m.process_trade(trade)
            if closed_spot_1m:
                reconciliation_executor.submit(
                    reconcile_candle,
                    closed_spot_1m.symbol, closed_spot_1m.timestamp, data_processor_1m, tf_context_1m, analyze_1m
                )

    def backfill_handler(symbol: str, trades: List[Trade]):
        """Handler for backfilled trades from BinanceClient"""
        if symbol not in symbol_locks:
            return
            
        with symbol_locks[symbol]:
            # Route backfilled trades to all processors
            data_processor.fill_gap_from_trades(symbol, trades)
            data_processor_15m.fill_gap_from_trades(symbol, trades)
            data_processor_1m.fill_gap_from_trades(symbol, trades)
            
            # We don't trigger analysis here to avoid flood, 
            # but next live trade will trigger analysis on correct data.

    # Setup Binance Client
    client = BinanceClient(SYMBOLS, on_trade_callback=on_trade, status_sink=ui)
    client.set_backfill_callback(backfill_handler)
    ui.status.binance_client = client
    logger.info(f"main.py client id: {id(client)}")

    # Initialize History with Hybrid Approach (Klines + AggTrades)
    try:
        now_ms = int(time.time() * 1000)
        # DETERMINISTIC STARTUP: Floor to 3m candle boundary
        # This ensures multiple instances started within the same 3-minute window
        # will fetch identical data and have identical initial states.
        aligned_now_ms = now_ms - (now_ms % tf_context.interval_ms)
        warmup_duration_ms = 20 * 60 * 1000 # 20 minutes warmup for flow
        kline_end_ms = aligned_now_ms - warmup_duration_ms
        
        def init_hybrid(proc, ctx, lookback):
            # 1. Bulk Klines (Fast) - Fetch BOTH Spot and Perp
            logger.info(f"[{ctx.name}] Fetching bulk history (Spot & Perp)...")
            
            # RETRY LOGIC: Ensure we have data before starting
            max_retries = 5
            for attempt in range(max_retries):
                h_map_spot = client.fetch_historical_candles(lookback_bars=lookback, context=ctx, kline_end_time=kline_end_ms, source='spot')
                h_map_perp = client.fetch_historical_candles(lookback_bars=lookback, context=ctx, kline_end_time=kline_end_ms, source='perp')
                
                # Check if we got data for at least most symbols
                if h_map_spot and len(h_map_spot) >= len(SYMBOLS) * 0.8:
                     break
                
                logger.warning(f"[{ctx.name}] Initialization fetched partial/empty data (Attempt {attempt+1}/{max_retries}). Retrying in 5s...")
                time.sleep(5)
            
            if not h_map_spot:
                logger.critical(f"[{ctx.name}] FAILED to initialize spot history after retries. Scanner will run cold.")
            
            proc.init_history(h_map_spot, source='spot')
            proc.init_history(h_map_perp, source='perp')
            
            # 2. Warmup Backfill (Pristine Flow)
            logger.info(f"[{ctx.name}] Warming up flow (AggTrades 20m)...")
            # Note: fill_gap_from_trades now handles routing to both spot/perp histories based on trade.source
            # fetched trades contain 'source' field correctly set by client
            if h_map_spot: # Only backfill if we have symbols
                for sym in h_map_spot.keys():
                    try:
                        trades = client.fetch_agg_trades(sym, start_time=kline_end_ms, end_time=aligned_now_ms)
                        proc.fill_gap_from_trades(sym, trades)
                    except Exception as e:
                        logger.error(f"[{ctx.name}] Backfill failed for {sym}: {e}")
            
            # 3. Calculate Indicators (Full Batched) - Calculate for BOTH
            logger.info(f"[{ctx.name}] Calculating indicators...")
            for sym, hist in proc.spot_history.items():
                if hist: calculate_indicators_full(hist, context=ctx)
            for sym, hist in proc.perp_history.items():
                if hist: calculate_indicators_full(hist, context=ctx)
            
            return h_map_spot

        # --- 3m Initialization ---
        history_map = init_hybrid(data_processor, tf_context, 500)
        # Verify and Initial Analyze (on SPOT history)
        for symbol, hist in data_processor.spot_history.items():
             if hist:
                 state = symbol_states[symbol]
                 perp_hist = data_processor.perp_history.get(symbol, [])
                 alerts = analyzer.analyze(symbol, hist, context=tf_context, state=state, perp_candles=perp_hist)
                 for alert in alerts:
                     ui.add_alert(alert)

        # --- 15m Initialization ---
        init_hybrid(data_processor_15m, tf_context_15m, 200)
        for symbol, hist in data_processor_15m.spot_history.items():
             if hist:
                 perm = analyzer.analyze_permission(symbol, hist, context=tf_context_15m)
                 if symbol in symbol_states:
                     symbol_states[symbol].permission = perm
                 logger.info(f"[15m Init] {symbol} Permission: {perm.bias}/{perm.volatility_regime} allowed={perm.allowed}")

        # --- 1m Initialization ---
        init_hybrid(data_processor_1m, tf_context_1m, 60)
        # No analysis needed for 1m start check

        # Force UI to render prefetched alerts
        if ui.alerts:
            ui.dirty = True

    except Exception as e:
        logger.error(f"Failed to initialize history: {e}")

    client.start()


    # Graceful Shutdown
    def signal_handler(sig, frame):
        logger.info("Shutting down (Signal)...")
        # Let the finally block handle cleanup by raising SystemExit
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    # UI Loop
    try:
        with Live(
            ui.generate_layout(), 
            console=console,
            auto_refresh=False,
            screen=False
        ) as live:
            while True:
                # Update UI with latest state (thread-safe copy inside method)
                ui.update_state_monitor(symbol_states)
                
                if ui.dirty:          # set only when alerts change
                    ui.dirty = False
                    live.update(ui.generate_layout(), refresh=True)
                time.sleep(0.1)
    except KeyboardInterrupt:
        logger.info("Keyboard Interrupt")
    finally:
        logger.info("Performing cleanup...")
        client.stop()
        # Shutdown executor, don't wait for pending, cancel them if possible
        # Python 3.9+ supports cancel_futures=True
        reconciliation_executor.shutdown(wait=False, cancel_futures=True)
        logger.info("Cleanup complete.")

if __name__ == "__main__":
    main()
