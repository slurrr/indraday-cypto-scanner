import sys, os
from rich.console import Console
from rich.live import Live

# Keep a real stdout for Rich to use
REAL_STDOUT = sys.__stdout__

# Create Rich console bound to REAL terminal output
console = Console(file=REAL_STDOUT)

from ui.console import ConsoleUI  # import AFTER console exists

import time
import signal
from config.settings import SYMBOLS
from data.binance_client import BinanceClient
from core.data_processor import DataProcessor
from core.analyzer import Analyzer
from core.indicators import update_indicators
from models.types import Trade
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

    def handle_alerts(alerts: List[Alert]):
        for alert in alerts:
            ui.add_alert(alert)
            logger.info(f"ALERT: {alert}")

    # Callback for new trades
    def on_trade(trade: Trade):
        # process_trade returns a Candle ONLY when a minute closes
        closed_candle = data_processor.process_trade(trade)
        if not closed_candle:
            return

        symbol = closed_candle.symbol
        # 1. Get updated history
        history = data_processor.get_history(symbol)
        
        # 2. Update Indicators (VWAP, ATR, etc.) for this symbol
        # Performance Note: Re-calculating for whole history every minute is fine for MVP
        update_indicators(history)
        
        # 3. Analyze Patterns
        new_alerts = analyzer.analyze(symbol, history)

        # 4. Update UI
        if new_alerts:
            handle_alerts(new_alerts)

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
                    live.update(ui.generate_layout())
                    ui.dirty = False
                time.sleep(0.1)
    except KeyboardInterrupt:
        pass # logger.info("Keyboard Interrupt")
    finally:
        pass # client.stop()

if __name__ == "__main__":
    main()
