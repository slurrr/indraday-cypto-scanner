import time
import signal
import sys
from rich.live import Live
from config.settings import SYMBOLS
from data.binance_client import BinanceClient
from core.data_processor import DataProcessor
from core.analyzer import Analyzer
from core.indicators import update_indicators
from ui.console import ConsoleUI
from models.types import Trade
from utils.logger import setup_logger

logger = setup_logger("Main")

def main():
    logger.info("Starting Intraday Flow Scanner...")

    # Components
    data_processor = DataProcessor()
    analyzer = Analyzer()
    ui = ConsoleUI()

    # Callback for new trades
    def on_trade(trade: Trade):
        # process_trade returns a Candle ONLY when a minute closes
        closed_candle = data_processor.process_trade(trade)
        
        if closed_candle:
            symbol = closed_candle.symbol
            # 1. Get updated history
            history = data_processor.get_history(symbol)
            
            # 2. Update Indicators (VWAP, ATR, etc.) for this symbol
            # Performance Note: Re-calculating for whole history every minute is fine for MVP
            update_indicators(history)
            
            # 3. Analyze Patterns
            new_alerts = analyzer.analyze(symbol, history)
            
            # 4. Update UI
            for alert in new_alerts:
                ui.add_alert(alert)
                logger.info(f"ALERT: {alert}")

    # Setup Binance Client
    client = BinanceClient(SYMBOLS, on_trade_callback=on_trade)
    
    # Initialize History
    try:
        history_map = client.fetch_historical_candles(lookback_minutes=1000)
        data_processor.init_history(history_map)
        
        # Pre-calculate indicators for history so we start hot
        for symbol in SYMBOLS:
            hist = data_processor.get_history(symbol)
            if hist:
                update_indicators(hist)
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
        with Live(ui.generate_table(), refresh_per_second=4) as live:
            while True:
                live.update(ui.generate_table())
                time.sleep(0.25)
    except KeyboardInterrupt:
        logger.info("Keyboard Interrupt")
    finally:
        client.stop()

if __name__ == "__main__":
    main()
