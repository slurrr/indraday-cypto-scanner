import json
import threading
import time
import websocket
from typing import Callable, List, Dict
from config.settings import BINANCE_SPOT_WS_URL, BINANCE_PERP_WS_URL
from models.types import Trade, Candle, StatusSink
from utils.logger import setup_logger
import requests

logger = setup_logger("BinanceClient")

class BinanceClient:
    def __init__(self, symbols: List[str], on_trade_callback: Callable[[Trade], None], status_sink: StatusSink = None):
        self.symbols = [s.lower() for s in symbols]
        self.on_trade_callback = on_trade_callback
        self.status_sink = status_sink
        self.ws_spot = None
        self.ws_perp = None
        self.keep_running = True
        
    def _on_message_spot(self, ws, message):
        try:
            data = json.loads(message)
            if 'e' in data and data['e'] == 'aggTrade':
                # Map payload to Trade object
                trade = Trade(
                    symbol=data['s'],
                    price=float(data['p']),
                    quantity=float(data['q']),
                    timestamp=data['T'],
                    is_buyer_maker=data['m'],
                    source='spot'
                )
                self.on_trade_callback(trade)
        except Exception as e:
            logger.error(f"Error parsing spot message: {e}")

    def _on_message_perp(self, ws, message):
        try:
            data = json.loads(message)
            if 'e' in data and data['e'] == 'aggTrade':
                trade = Trade(
                    symbol=data['s'],
                    price=float(data['p']),
                    quantity=float(data['q']),
                    timestamp=data['T'],
                    is_buyer_maker=data['m'],
                    source='perp'
                )
                self.on_trade_callback(trade)
        except Exception as e:
            logger.error(f"Error parsing perp message: {e}")

    def _on_error(self, ws, error):
        logger.error(f"Websocket error: {error}")
        if self.status_sink:
            self.status_sink.error(str(error))

    def _on_close(self, ws, close_status_code, close_msg):
        logger.info("Websocket closed")

    def _on_open(self, ws):
        logger.info(f"Websocket opened: {ws.url}")
        if self.status_sink:
            self.status_sink.feed_connected()
        # Subscribe to aggTrade for all symbols
        params = [f"{s}@aggTrade" for s in self.symbols]
        subscribe_msg = {
            "method": "SUBSCRIBE",
            "params": params,
            "id": 1
        }
        ws.send(json.dumps(subscribe_msg))
        logger.info(f"Subscribed to {len(self.symbols)} symbols")

    def fetch_historical_candles(self, lookback_minutes: int = 1000) -> Dict[str, List[Candle]]:
        """
        Fetch historical klines for all symbols via REST API to initialize history.
        Uses Binance Spot API.
        """
        history = {}
        logger.info(f"Fetching {lookback_minutes} minutes of history for {len(self.symbols)} symbols...")
        
        base_url = "https://api.binance.com/api/v3/klines"
        
        for symbol in self.symbols:
            try:
                # Interval 1m, Limit = lookback
                params = {
                    "symbol": symbol.upper(),
                    "interval": "1m",
                    "limit": lookback_minutes
                }
                resp = requests.get(base_url, params=params, timeout=10)
                resp.raise_for_status()
                data = resp.json()
                
                candles = []
                for k in data:
                    # k schema: [Open time, Open, High, Low, Close, Volume, Close time, ...]
                    c = Candle(
                        symbol=symbol,
                        timestamp=k[0],
                        open=float(k[1]),
                        high=float(k[2]),
                        low=float(k[3]),
                        close=float(k[4]),
                        volume=float(k[5]),
                        spot_cvd=0.0,
                        perp_cvd=0.0,
                        closed=True
                    )
                    candles.append(c)
                
                history[symbol] = candles
                
            except Exception as e:
                logger.error(f"Failed to fetch history for {symbol}: {e}")
                
        return history

    def start(self):
        """Start spot and perp connections in separate threads"""
        # Spot Connection
        logger.info("Starting Spot Websocket...")
        self.ws_spot = websocket.WebSocketApp(
            BINANCE_SPOT_WS_URL,
            on_open=self._on_open,
            on_message=self._on_message_spot,
            on_error=self._on_error,
            on_close=self._on_close
        )
        
        # Perp Connection
        logger.info("Starting Perp Websocket...")
        self.ws_perp = websocket.WebSocketApp(
            BINANCE_PERP_WS_URL,
            on_open=self._on_open,
            on_message=self._on_message_perp,
            on_error=self._on_error,
            on_close=self._on_close
        )
        threading.Thread(target=self.ws_spot.run_forever, daemon=True).start()
        threading.Thread(target=self.ws_perp.run_forever, daemon=True).start()

    def stop(self):
        self.keep_running = False
        if self.ws_spot:
            self.ws_spot.close()
        if self.ws_perp:
            self.ws_perp.close()
