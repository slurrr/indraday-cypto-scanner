from typing import Dict, List, Optional
from models.types import Trade, Candle, StatusSink
from utils.logger import setup_logger
import math
from time import time
from config.settings import CANDLE_TIMEFRAME_MINUTES

logger = setup_logger("DataProcessor")

class DataProcessor:
    def __init__(self, status_sink: StatusSink, context: Optional["TimeframeContext"] = None):
        self.status_sink = status_sink
        # Default to 3m/180000ms if not provided (transition period)
        from config.settings import CANDLE_TIMEFRAME_MINUTES
        self.tf_ms = int(context.interval_ms) if context else int(CANDLE_TIMEFRAME_MINUTES * 60 * 1000)
        self.name = context.name if context else "3m"
        
        # symbol -> history of candles (list)
        self.spot_history: Dict[str, List[Candle]] = {}
        self.perp_history: Dict[str, List[Candle]] = {}
        
        # symbol -> current_candle (Candle)
        self.active_spot_candles: Dict[str, Candle] = {}
        self.active_perp_candles: Dict[str, Candle] = {}
        
        # Throttling for UI updates
        self.last_tick_update_time = 0.0
        
    def process_trade(self, trade: Trade) -> tuple[Optional[Candle], Optional[Candle]]:
        """
        Ingest a trade, update the current candle. 
        Returns (closed_spot_candle, closed_perp_candle).
        """
        assert trade is not None
        assert trade.timestamp is not None
        symbol = trade.symbol
        
        # Determine strict source context
        is_spot = (trade.source == 'spot')
        active_candles = self.active_spot_candles if is_spot else self.active_perp_candles
        history_store = self.spot_history if is_spot else self.perp_history
        
        minute_start_ms = (trade.timestamp // self.tf_ms) * self.tf_ms
        
        # TRACE LOGGING: Trade Ingestion
        # (Disabled for safety - too high volume)
        # logger.debug(f"[TRACE][{symbol}] Ingest Trade: {trade.price} @ {trade.quantity} ({trade.source})")
        
        closed_candle = None
        
        # Check if we need to rotate the candle
        if symbol in active_candles:
            current_candle = active_candles[symbol]
            if current_candle.timestamp != minute_start_ms:
                # Close the old candle
                current_candle.closed = True
                
                # Add to correct history
                if symbol not in history_store:
                    history_store[symbol] = []
                history_store[symbol].append(current_candle)
                
                 # Keep last 500 candles to prevent memory leak
                if len(history_store[symbol]) > 500:
                    history_store[symbol].pop(0)
                    
                closed_candle = current_candle
                
                # Start new candle
                active_candles[symbol] = self._create_new_candle(trade, minute_start_ms, current_candle.close)
            else:
                # Update existing candle
                self._update_candle(current_candle, trade)
        else:
            # First candle for this symbol
            active_candles[symbol] = self._create_new_candle(trade, minute_start_ms, trade.price)
            
        if closed_candle:
            # Only tick UI on Spot closes? Or both? 
            # Let's tick on Spot closes since that drives the "Last Tick" for the user usually.
            if is_spot:
                self.status_sink.tick()
                self.last_tick_update_time = time()
        else:
            # Throttle UI updates to 1s
            if is_spot:
                now = time()
                if now - self.last_tick_update_time >= 1.0:
                    self.status_sink.tick()
                    self.last_tick_update_time = now

        # Logic: We return both potential closes.
        if is_spot:
            return closed_candle, None
        else:
            return None, closed_candle

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
        # Strict Segregation: Update only what this candle corresponds to.
        # Note: 'candle' here is known to be the correct type (Spot or Perp) because of routing in process_trade.
        
        candle.volume += trade.quantity
        
        # Price Update (Native)
        candle.high = max(candle.high, trade.price)
        candle.low = min(candle.low, trade.price)
        candle.close = trade.price
        
        # CVD Logic
        delta = trade.quantity if not trade.is_buyer_maker else -trade.quantity
        
        if trade.source == 'spot':
            candle.spot_cvd += delta
        elif trade.source == 'perp':
            candle.perp_cvd += delta
            
        # TRACE LOGGING: Candle Update
        # (Disabled for safety - too high volume)
        # logger.debug(f"[TRACE][{candle.symbol}] Candle Update: C={candle.close} V={candle.volume} SCVD={candle.spot_cvd} PCVD={candle.perp_cvd}")

    # _add_to_history removed, logic moved inline to process_trade for separate streams

    def update_history_candle(self, symbol: str, new_candle: Candle, source: str = 'spot'):
        """
        Replaces a candle in history with a reconciled version (e.g. from API).
        Preserves the CVD from the local version if the new version has 0.
        """
        history = self.spot_history if source == 'spot' else self.perp_history
        
        if symbol not in history:
            return

        for i, c in enumerate(history[symbol]):
            if c.timestamp == new_candle.timestamp:
                history[symbol][i] = new_candle
                return

    def get_history(self, symbol: str, source: str = 'spot') -> List[Candle]:
        if source == 'spot':
             return self.spot_history.get(symbol, [])
        else:
             return self.perp_history.get(symbol, [])

    def init_history(self, history: Dict[str, List[Candle]], source: str = 'spot'):
        """Initialize history with fetched candles"""
        if source == 'spot':
            self.spot_history = history
        else:
            self.perp_history = history
            
        logger.info(f"Initialized {source} history for {len(history)} symbols")

    def fill_gap_from_trades(self, symbol: str, trades: List[Trade]):
        """
        Replays a list of trades to reconstruct candles and CVD.
        Used for backfilling gaps from aggTrades.
        """
        if not trades:
            return

        # Ensure trades are sorted
        trades.sort(key=lambda x: x.timestamp)
        
        # We need to process these trades as if they were live.
        # But we must ensure we don't start a new candle with a huge gap 
        # without closing the previous one properly.
        # process_trade handles logic: "if timestamp > active_candle + interval -> close active, start new".
        
        for trade in trades:
            self.process_trade(trade)
            
        logger.info(f"Backfilled gap with {len(trades)} trades for {symbol}")
