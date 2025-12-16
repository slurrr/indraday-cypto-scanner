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
from models.types import Trade, Alert
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

    # Components
    ui = ConsoleUI(console=console)
    data_processor = DataProcessor(status_sink=ui)
    analyzer = Analyzer()
    ui.dirty = True

    # Per-symbol locks to prevent race conditions between Spot and Perp threads
    # without blocking unrelated symbols
    symbol_locks = {s: threading.Lock() for s in SYMBOLS}

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
                    update_indicators(history)
                    
                    # 3. Analyze
                    alerts = analyzer.analyze(symbol, history)
                    
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

    # Function to reconcile candle in background
    def reconcile_candle(symbol: str, timestamp: int):
        # 1. Network Fetch (Slow, no lock needed)
        api_candle = client.fetch_latest_candle(symbol)
        
        if api_candle and api_candle.timestamp == timestamp:
            # 2. Update History (Fast, needs lock)
            with symbol_locks[symbol]:
                data_processor.update_history_candle(symbol, api_candle)
                
                # Update indicators again so history is clean for NEXT minute
                history = data_processor.get_history(symbol)
                update_indicators(history)
                
                # 3. Re-Analyze with clean data (Slow Path Analysis)
                # This catches alerts missed by the fast path due to data drift
                reconciled_alerts = analyzer.analyze(symbol, history)
                
                if reconciled_alerts:
                    debug_logger.debug(
                        f"[DEBUG_BARS] {symbol} 3m bars={len(history)} "
                        f"ready={len(history) >= 120}"
                    )
                    handle_alerts(reconciled_alerts)

    # Callback for new trades
    def on_trade(trade: Trade):
        # Acquire lock for this specific symbol
        # This prevents Spot and Perp threads from modifying the same symbol's history concurrently
        # providing thread safety without global blocking.
        with symbol_locks[trade.symbol]:
            # process_trade returns a Candle ONLY when a minute closes
            closed_candle = data_processor.process_trade(trade)
            
            # --- FAST PATH: Immediate Analysis ---
            if not closed_candle:
                return

            # Offload heavy analysis to worker
            symbol = closed_candle.symbol
            with queue_lock:
                if symbol not in queued_symbols:
                    queued_symbols.add(symbol)
                    analysis_queue.put(symbol)

        # --- SLOW PATH: Background Reconciliation ---
        # Spawning a thread here moves the Network I/O out of the critical path
        threading.Thread(
            target=reconcile_candle, 
            args=(symbol, closed_candle.timestamp), 
            daemon=True
        ).start()

    # Setup Binance Client
    client = BinanceClient(SYMBOLS, on_trade_callback=on_trade, status_sink=ui)
    ui.status.binance_client = client
    logger.info(f"main.py client id: {id(client)}")

    # Initialize History
    try:
        history_map = client.fetch_historical_candles(lookback_bars=1000)
        data_processor.init_history(history_map)
        
        # Pre-calculate indicators for history so we start hot
        for symbol, hist in history_map.items():
            if hist:
                update_indicators(hist)
                # analyze prefetched history
                alerts = analyzer.analyze(symbol, hist)
                for alert in alerts:
                    ui.add_alert(alert)

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
