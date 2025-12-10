from typing import Dict, List, Optional
from models.types import Trade, Candle, StatusSink
from utils.logger import setup_logger
import math
from time import time

logger = setup_logger("DataProcessor")

class DataProcessor:
    def __init__(self, status_sink: StatusSink):
        self.status_sink = status_sink
        # symbol -> current_candle (Candle)
        self.active_candles: Dict[str, Candle] = {}
        # symbol -> history of candles (list)
        self.history: Dict[str, List[Candle]] = {}
        
    def process_trade(self, trade: Trade) -> Optional[Candle]:
        """
        Ingest a trade, update the current candle. 
        Returns a Candle if a candle just closed (for the PREVIOUS minute), else None.
        """
        symbol = trade.symbol
        timestamp_s = trade.timestamp / 1000.0
        minute_start_ms = int(timestamp_s // 60) * 60 * 1000
        
        closed_candle = None
        
        # Check if we need to rotate the candle
        if symbol in self.active_candles:
            current_candle = self.active_candles[symbol]
            if current_candle.timestamp != minute_start_ms:
                # Close the old candle
                current_candle.closed = True
                self._add_to_history(symbol, current_candle)
                closed_candle = current_candle
                
                # Start new candle
                self.active_candles[symbol] = self._create_new_candle(trade, minute_start_ms, current_candle.close)
            else:
                # Update existing candle
                self._update_candle(current_candle, trade)
        else:
            # First candle for this symbol
            self.active_candles[symbol] = self._create_new_candle(trade, minute_start_ms, trade.price)
        self.status_sink.tick()
        return closed_candle

    def _create_new_candle(self, trade: Trade, timestamp: int, open_price: float) -> Candle:
        candle = Candle(
            symbol=trade.symbol,
            timestamp=timestamp,
            open=open_price,   # Ideally we want the very first trade price, or prev close
            high=trade.price,
            low=trade.price,
            close=trade.price,
            volume=0.0
        )
        self._update_candle(candle, trade)
        return candle

    def _update_candle(self, candle: Candle, trade: Trade):
        candle.high = max(candle.high, trade.price)
        candle.low = min(candle.low, trade.price)
        candle.close = trade.price
        candle.volume += trade.quantity
        
        # CVD Logic
        # Buyer maker = sell side execution (downward pressure usually)
        # But commonly: is_buyer_maker=True -> Sell, False -> Buy
        # Delta = Volume if Buy, -Volume if Sell
        delta = trade.quantity if not trade.is_buyer_maker else -trade.quantity
        
        if trade.source == 'spot':
            candle.spot_cvd += delta
        elif trade.source == 'perp':
            candle.perp_cvd += delta

    def _add_to_history(self, symbol: str, candle: Candle):
        if symbol not in self.history:
            self.history[symbol] = []
        self.history[symbol].append(candle)
        # Keep last 1000 candles to prevent memory leak in MVP
        if len(self.history[symbol]) > 1000:
            self.history[symbol].pop(0)

    def update_history_candle(self, symbol: str, new_candle: Candle):
        """
        Replaces a candle in history with a reconciled version (e.g. from API).
        Preserves the CVD from the local version if the new version has 0.
        """
        if symbol not in self.history:
            return

        for i, c in enumerate(self.history[symbol]):
            if c.timestamp == new_candle.timestamp:
                # Preserve locally calculated CVD since REST API doesn't have it
                new_candle.spot_cvd = c.spot_cvd
                new_candle.perp_cvd = c.perp_cvd
                
                self.history[symbol][i] = new_candle
                logger.debug(f"Reconciled candle for {symbol} at {new_candle.timestamp}")
                return

    def get_history(self, symbol: str) -> List[Candle]:
        return self.history.get(symbol, [])

    def init_history(self, history: Dict[str, List[Candle]]):
        """Initialize history with fetched candles"""
        self.history = history
        # Also initialize active_candles based on last history candle if needed?
        # Actually, active_candle is for the *current* minute being built.
        # History contains *closed* candles.
        # So we just populate self.history.
        logger.info(f"Initialized history for {len(history)} symbols")
