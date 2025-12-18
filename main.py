from config.settings import ANALYZER_DEBUG
import json
import sys, os
from typing import List
from rich.console import Console
from rich.live import Live

# Keep a real stdout for Rich to use
REAL_STDOUT = sys.__stdout__

# Create Rich console bound to REAL terminal output
console = Console(file=REAL_STDOUT)

from ui.console import ConsoleUI, UIStatus  # import AFTER console exists

import time
import signal
import queue
import threading
from config.settings import SYMBOLS
from data.binance_client import BinanceClient
from core.data_processor import DataProcessor
from core.analyzer import Analyzer
from core.indicators import update_indicators
from models.types import Trade, Alert, TimeframeContext, State, StateSnapshot
from utils.logger import setup_logger

INSTANCE_ID = os.environ.get("SCANNER_INSTANCE", os.getpid())

LOG_FILE = f"utils/scanner_{INSTANCE_ID}.log"
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

    # State Management (Step 6)
    # Initialize state for each symbol
    symbol_states: Dict[str, StateSnapshot] = {
        s: StateSnapshot(symbol=s, state=State.WATCH) for s in SYMBOLS
    }

    # Deduplication Set: Stores (symbol, pattern_name, candle_timestamp)
    sent_alerts = set()
    sent_alerts_lock = threading.Lock()

    def handle_alerts(alerts: List[Alert]):
        new_unique_alerts = []
        with sent_alerts_lock:
            for alert in alerts:
                # Deduplication Key: Symbol + Pattern + Candle Timestamp
                # This ensures we don't alert twice for the exact same event on the exact same candle
                key = (alert.symbol, alert.pattern.value, alert.candle_timestamp)
                
                if key not in sent_alerts:
                    sent_alerts.add(key)
                    new_unique_alerts.append(alert)
        
        for alert in new_unique_alerts:
            ui.add_alert(alert)
            logger.info(f"ALERT: {alert}")

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
                    # 1. Get history
                    history = data_processor.get_history(symbol)
                    
                    # 2. Update Indicators
                    update_indicators(history, context=tf_context)
                    
                    # 3. Analyze
                    current_state = symbol_states[symbol]
                    alerts = analyzer.analyze(symbol, history, context=tf_context, state=current_state)
                    
                    # Debug logic
                    if ANALYZER_DEBUG:
                        dbg = analyzer.debug_analyze(symbol, history)
                        for pat, result in dbg["patterns"].items():
                            if not result["ok"] and any(k in result["reason"].lower() for k in ["not", "missing", "near"]):
                                debug_logger.debug(f"[DEBUG][{symbol}] Almost {pat}: {result['reason']}")

                    # 4. Handle Alerts
                    if alerts:
                        debug_logger.debug(
                            f"[DEBUG_BARS] {symbol} 3m bars={len(history)} "
                            f"ready={len(history) >= 120}"
                        )
                        handle_alerts(alerts)
                        
            except Exception as e:
                logger.error(f"Error in analysis worker for {symbol}: {e}")
            finally:
                analysis_queue.task_done()

    # Start worker thread
    threading.Thread(target=analysis_worker, daemon=True, name="AnalysisWorker").start()

    # Function to reconcile candle in background (Shared Logic)
    def reconcile_candle(symbol: str, timestamp: int, processor: DataProcessor, context: TimeframeContext, callback=None):
        # 1. Network Fetch (Slow, no lock needed)
        api_candle = client.fetch_latest_candle(symbol, context=context)
        
        if api_candle and api_candle.timestamp == timestamp:
            # 2. Update History (Fast, needs lock)
            with symbol_locks[symbol]:
                processor.update_history_candle(symbol, api_candle)
                
                # Update indicators again so history is clean for NEXT minute
                history = processor.get_history(symbol)
                update_indicators(history, context=context)
                
                # 3. Callback (Analysis)
                if callback:
                    callback(symbol, history, context)

    # --- Analysis Callbacks ---
    def analyze_3m(symbol: str, history: List, context: TimeframeContext):
        # Retrieve state
        state = symbol_states[symbol]
        
        reconciled_alerts = analyzer.analyze(symbol, history, context=context, state=state)
        
        # State update (placeholder) done inside analyze via side-effect on 'state' object
        
        if reconciled_alerts:
            debug_logger.debug(
                f"[DEBUG_BARS] {symbol} 3m bars={len(history)} ready={len(history) >= 120}"
            )
            handle_alerts(reconciled_alerts)

    def analyze_15m(symbol: str, history: List, context: TimeframeContext):
        perm_snapshot = analyzer.analyze_permission(symbol, history, context=context)
        
        # Attach permission to state
        if symbol in symbol_states:
             symbol_states[symbol].permission = perm_snapshot
             
        # Just log for now
        logger.info(f"[15m] Permission Snapshot for {symbol}: {perm_snapshot} -> State Updated")

    def analyze_1m(symbol: str, history: List, context: TimeframeContext):
        # Retrieve state
        state = symbol_states.get(symbol)
        
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
                    pattern=PatternType.EXEC,
                    score=min(sig.strength * 10.0, 100.0), # normalize strength?
                    flow_regime=analyzer._determine_regime(history, history[-1]), # roughly
                    price=sig.price,
                    message=f"{sig.direction}: {sig.reason}",
                    timeframe="1m"
                )
                handle_alerts([alert])



    # Callback for new trades
    def on_trade(trade: Trade):
        # Acquire lock for this specific symbol
        # This prevents Spot and Perp threads from modifying the same symbol's history concurrently
        # providing thread safety without global blocking.
        with symbol_locks[trade.symbol]:
            # --- 3m Processing ---
            closed_candle = data_processor.process_trade(trade)
            
            # --- FAST PATH: Immediate Analysis (3m) ---
            if closed_candle:
                # Offload heavy analysis to worker
                symbol = closed_candle.symbol
                with queue_lock:
                    if symbol not in queued_symbols:
                        queued_symbols.add(symbol)
                        analysis_queue.put(symbol)

                # --- SLOW PATH: Background Reconciliation (3m) ---
                threading.Thread(
                    target=reconcile_candle, 
                    args=(symbol, closed_candle.timestamp, data_processor, tf_context, analyze_3m), 
                    daemon=True
                ).start()

            # --- 15m Processing ---
            closed_candle_15m = data_processor_15m.process_trade(trade)
            if closed_candle_15m:
                # No fast path for 15m yet, just reconciliation/permission check
                threading.Thread(
                    target=reconcile_candle,
                    args=(closed_candle_15m.symbol, closed_candle_15m.timestamp, data_processor_15m, tf_context_15m, analyze_15m),
                    daemon=True
                ).start()

    # Setup Binance Client
    client = BinanceClient(SYMBOLS, on_trade_callback=on_trade, status_sink=ui)
    ui.status.binance_client = client
    logger.info(f"main.py client id: {id(client)}")

    # Initialize History
    try:
        # --- 3m Initialization ---
        history_map = client.fetch_historical_candles(lookback_bars=1000, context=tf_context)
        data_processor.init_history(history_map)
        
        # Pre-calculate indicators for history so we start hot
        for symbol, hist in history_map.items():
            if hist:
                update_indicators(hist, context=tf_context)
                # analyze prefetched history
                state = symbol_states[symbol]
                alerts = analyzer.analyze(symbol, hist, context=tf_context, state=state)
                for alert in alerts:
                    ui.add_alert(alert)

        # --- 15m Initialization ---
        history_map_15m = client.fetch_historical_candles(lookback_bars=200, context=tf_context_15m)
        data_processor_15m.init_history(history_map_15m)
        for symbol, hist in history_map_15m.items():
            if hist:
                update_indicators(hist, context=tf_context_15m)
                perm = analyzer.analyze_permission(symbol, hist, context=tf_context_15m)
                perm = analyzer.analyze_permission(symbol, hist, context=tf_context_15m)
                logger.info(f"[15m Init] {symbol} Permission: {perm.bias}/{perm.volatility_regime} allowed={perm.allowed}")

        # --- 1m Initialization ---
        history_map_1m = client.fetch_historical_candles(lookback_bars=60, context=tf_context_1m)
        data_processor_1m.init_history(history_map_1m)
        for symbol, hist in history_map_1m.items():
            if hist:
                update_indicators(hist, context=tf_context_1m)
                # No need to analyze execution on history start, just have data ready

        # --- 1m Initialization ---
        history_map_1m = client.fetch_historical_candles(lookback_bars=60, context=tf_context_1m)
        data_processor_1m.init_history(history_map_1m)
        for symbol, hist in history_map_1m.items():
            if hist:
                update_indicators(hist, context=tf_context_1m)
                # No need to analyze execution on history start, just have data ready


        # Force UI to render prefetched alerts
        if ui.alerts:
            ui.dirty = True

    except Exception as e:
        logger.error(f"Failed to initialize history: {e}")

    client.start()


    # Graceful Shutdown
    def signal_handler(sig, frame):
        logger.info("Shutting down...")
        client.stop()
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
                if ui.dirty:          # set only when alerts change
                    ui.dirty = False
                    live.update(ui.generate_layout(), refresh=True)
                time.sleep(0.1)
    except KeyboardInterrupt:
        pass # logger.info("Keyboard Interrupt")
    finally:
        pass # client.stop()

if __name__ == "__main__":
    main()
