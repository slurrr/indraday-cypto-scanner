import json
import threading
import time
import websocket
from typing import Callable, List, Dict
from config.settings import BINANCE_SPOT_WS_URL, BINANCE_PERP_WS_URL, CANDLE_TIMEFRAME_MINUTES
from models.types import Trade, Candle, StatusSink
from utils.logger import setup_logger
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from collections import defaultdict

logger = setup_logger("BinanceClient")

class BinanceClient:
    def __init__(self, symbols: List[str], on_trade_callback: Callable[[Trade], None], status_sink: StatusSink = None):
        self.metrics = defaultdict(int)
        self.symbols = [s.upper() for s in symbols]
        self.on_trade_callback = on_trade_callback
        self.status_sink = status_sink
        self.ws_spot = None
        self.ws_perp = None
        self.keep_running = True
        # Initialize persistent session for API calls to prevent socket exhaustion
        self.session = requests.Session()
        retries = Retry(total=3, backoff_factor=0.5, status_forcelist=[500, 502, 503, 504])
        adapter = HTTPAdapter(max_retries=retries)
        self.session.mount('https://', adapter)
        self.session.mount('http://', adapter)
        logger.info(f"BinanceClient instance created: {id(self)}")

    def _on_message_spot(self, ws, message):
        self.metrics["ws_messages_total"] += 1

        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            self.metrics["ws_messages_dropped"] += 1
            return

        if data.get('e') != 'aggTrade':
            self.metrics["ws_messages_dropped"] += 1
            return

        try:
            trade = Trade(
                symbol=data['s'],
                price=float(data['p']),
                quantity=float(data['q']),
                timestamp=int(data['T']),
                is_buyer_maker=data['m'],
                source='spot'
            )
        except (KeyError, TypeError, ValueError):
            self.metrics["ws_messages_dropped"] += 1
            return

        try:
            self.on_trade_callback(trade)
        except Exception:
            logger.exception("Error in spot on_trade_callback")
            self.metrics["ws_messages_dropped"] += 1
            return

    def _on_message_perp(self, ws, message):
        self.metrics["ws_messages_total"] += 1

        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            self.metrics["ws_messages_dropped"] += 1
            return

        if data.get('e') != 'aggTrade':
            self.metrics["ws_messages_dropped"] += 1
            return

        try:
            trade = Trade(
                symbol=data['s'],
                price=float(data['p']),
                quantity=float(data['q']),
                timestamp=int(data['T']),
                is_buyer_maker=data['m'],
                source='perp'
            )
        except (KeyError, TypeError, ValueError):
            self.metrics["ws_messages_dropped"] += 1
            return

        try:
            self.on_trade_callback(trade)
        except Exception:
            logger.exception("Error in perp on_trade_callback")
            self.metrics["ws_messages_dropped"] += 1
            return

    def get_ws_metrics(self):
        total = self.metrics["ws_messages_total"]
        dropped = self.metrics["ws_messages_dropped"]
        drop_pct = (dropped / total * 100) if total else 0.0

        return {
            "total": total,
            "dropped": dropped,
            "drop_pct": drop_pct,
        }

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
        # IMPORTANT: Binance requires lowercase symbols for streams (e.g. btcusdt@aggTrade)
        params = [f"{s.lower()}@aggTrade" for s in self.symbols]
        subscribe_msg = {
            "method": "SUBSCRIBE",
            "params": params,
            "id": 1
        }
        ws.send(json.dumps(subscribe_msg))
        logger.info(f"Subscribed to {len(self.symbols)} symbols")

    def fetch_historical_candles(self, lookback_bars: int = 1000) -> Dict[str, List[Candle]]:
        """
        Fetch historical klines for all symbols via REST API to initialize history.
        Uses Binance Spot API.
        """
        history = {}
        logger.info(f"Fetching {lookback_bars} bars of history for {len(self.symbols)} symbols...")
        
        base_url = "https://api.binance.com/api/v3/klines"
        
        for index, symbol in enumerate(self.symbols):
            try:
                # Interval 1m, Limit = lookback
                params = {
                    "symbol": symbol.upper(),
                    "interval": f"{CANDLE_TIMEFRAME_MINUTES}m",
                    "limit": lookback_bars
                }
                resp = self.session.get(base_url, params=params, timeout=10)
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
                
                # Small sleep to prevent rate limiting during init burst if many symbols
                if index % 10 == 0:
                    time.sleep(0.1)
                
            except Exception as e:
                logger.error(f"Failed to fetch history for {symbol}: {e}")
                
        return history

    def fetch_latest_candle(self, symbol: str) -> requests.Response:
        """
        Fetch the most recently closed candle for a specific symbol via REST API.
        This is used for reconciliation.
        """
        base_url = "https://api.binance.com/api/v3/klines"
        try:
            # We want the LAST closed candle. 
            # Requesting limit=2 ensures we get the just-closed one + the currently forming one.
            # We will take the second to last item.
            params = {
                "symbol": symbol.upper(),
                "interval": f"{CANDLE_TIMEFRAME_MINUTES}m",
                "limit": 2
            }
            resp = self.session.get(base_url, params=params, timeout=5)
            resp.raise_for_status()
            data = resp.json()
            
            if len(data) >= 2:
                # data[-2] is the fully closed candle we want
                k = data[-2]
                return Candle(
                    symbol=symbol,
                    timestamp=k[0],
                    open=float(k[1]),
                    high=float(k[2]),
                    low=float(k[3]),
                    close=float(k[4]),
                    volume=float(k[5]),
                    spot_cvd=0.0, # REST API doesn't give us CVD, we must preserve or approximate? 
                                  # Ideally we preserve the local CVD but correct the Prices/Volume.
                    perp_cvd=0.0,
                    closed=True
                )
        except Exception as e:
            # Log specific error if it's related to connections
            logger.error(f"Failed to fetch latest candle for {symbol}: {e}")
        return None

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
        threading.Thread(target=self.ws_spot.run_forever, kwargs={'ping_interval': 20, 'ping_timeout': 10}, daemon=True).start()
        threading.Thread(target=self.ws_perp.run_forever, kwargs={'ping_interval': 20, 'ping_timeout': 10}, daemon=True).start()

    def stop(self):
        self.keep_running = False
        if self.ws_spot:
            self.ws_spot.close()
        if self.ws_perp:
            self.ws_perp.close()
        # Close the http session
        if self.session:
            self.session.close()
