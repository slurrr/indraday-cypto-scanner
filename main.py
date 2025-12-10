import sys, os
from typing import List
from rich.console import Console
from rich.live import Live

# Keep a real stdout for Rich to use
REAL_STDOUT = sys.__stdout__

# Create Rich console bound to REAL terminal output
console = Console(file=REAL_STDOUT)

from ui.console import ConsoleUI  # import AFTER console exists

import time
import signal
import threading
from config.settings import SYMBOLS
from data.binance_client import BinanceClient
from core.data_processor import DataProcessor
from core.analyzer import Analyzer
from core.indicators import update_indicators
from models.types import Trade, Alert
from utils.logger import setup_logger

logger = setup_logger("scanner")
logger.info("UI object constructed in main()")

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

            symbol = closed_candle.symbol
            # 1. Get updated history (Local Version)
            history = data_processor.get_history(symbol)
            
            # 2. Update Indicators (VWAP, ATR, etc.) for this symbol
            update_indicators(history)
            
            # 3. Analyze Patterns
            new_alerts = analyzer.analyze(symbol, history)

            # 4. Update UI
            if new_alerts:
                handle_alerts(new_alerts)

        # --- SLOW PATH: Background Reconciliation ---
        # Spawning a thread here moves the Network I/O out of the critical path
        threading.Thread(
            target=reconcile_candle, 
            args=(symbol, closed_candle.timestamp), 
            daemon=True
        ).start()

    # Setup Binance Client
    client = BinanceClient(SYMBOLS, on_trade_callback=on_trade, status_sink=ui)
    
    # Initialize History
    try:
        history_map = client.fetch_historical_candles(lookback_minutes=1000)
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
            refresh_per_second=2,
            screen=False
        ) as live:
            while True:
                if ui.dirty:          # set only when alerts change
                    ui.dirty = False
                    live.update(ui.generate_layout())
                time.sleep(0.1)
    except KeyboardInterrupt:
        pass # logger.info("Keyboard Interrupt")
    finally:
        pass # client.stop()

if __name__ == "__main__":
    main()
