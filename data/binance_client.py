import json
import threading
import time
import random
import websocket
from typing import Callable, List, Dict, Optional
from config.settings import BINANCE_SPOT_WS_URL, BINANCE_PERP_WS_URL, CANDLE_TIMEFRAME_MINUTES, PERP_SYMBOL_MAPPING
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
        # Increase pool size to handle burst of requests (reconciliation)
        adapter = HTTPAdapter(max_retries=retries, pool_connections=50, pool_maxsize=50)
        self.session.mount('https://', adapter)
        self.session.mount('http://', adapter)
        
        # Gap Detection
        self.backfill_callback: Optional[Callable[[str, List[Trade]], None]] = None
        self.last_msg_time_ms = int(time.time() * 1000) # Initialize to now to avoid startup gap
        
        # Symbol Normalization (Reverse Mapping)
        # Mapped (External) -> Internal (Spot)
        self.reverse_perp_mapping = {v: k for k, v in PERP_SYMBOL_MAPPING.items()}
        
        logger.info(f"BinanceClient instance created: {id(self)}")
        
    def set_backfill_callback(self, callback: Callable[[str, List[Trade]], None]):
        self.backfill_callback = callback

    def _on_message_spot(self, ws, message):
        self.metrics["ws_messages_total"] += 1
        self.last_msg_time_ms = int(time.time() * 1000)

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
        self.last_msg_time_ms = int(time.time() * 1000)

        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            self.metrics["ws_messages_dropped"] += 1
            return

        if data.get('e') != 'aggTrade':
            self.metrics["ws_messages_dropped"] += 1
            return

        try:
            raw_symbol = data['s']
            # Normalize to internal symbol
            internal_symbol = self.reverse_perp_mapping.get(raw_symbol, raw_symbol)
            
            trade = Trade(
                symbol=internal_symbol,
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
        
        # Determine if this is the Perp WS or Spot WS to use correct symbols
        is_perp = "fstream" in ws.url or "perp" in ws.url.lower() # Basic check based on URL constants
        
        if is_perp:
             # Use mapped symbols for Perp
             target_symbols = [PERP_SYMBOL_MAPPING.get(s, s) for s in self.symbols]
        else:
             target_symbols = self.symbols

        params = [f"{s.lower()}@aggTrade" for s in target_symbols]
        subscribe_msg = {
            "method": "SUBSCRIBE",
            "params": params,
            "id": 1
        }
        ws.send(json.dumps(subscribe_msg))
        logger.info(f"Subscribed to {len(self.symbols)} symbols")
        
        # Check for gap
        now = int(time.time() * 1000)
        gap_ms = now - self.last_msg_time_ms
        if gap_ms > 2000: # 2 seconds threshold
             logger.warning(f"Detected connection gap of {gap_ms}ms. Triggering backfill...")
             start_ts = self.last_msg_time_ms
             end_ts = now
             threading.Thread(target=self._perform_backfill, args=(start_ts, end_ts), daemon=True).start()

    def _perform_backfill(self, start_ts: int, end_ts: int):
        if not self.backfill_callback:
            return
            
        logger.info(f"Backfilling gap: {start_ts} to {end_ts} ({end_ts - start_ts}ms)")
        for symbol in self.symbols:
             trades = self.fetch_agg_trades(symbol, start_ts, end_ts)
             if trades:
                 try:
                     self.backfill_callback(symbol, trades)
                 except Exception as e:
                     logger.error(f"Backfill callback failed for {symbol}: {e}")
        logger.info("Backfill complete.")

    def fetch_historical_candles(self, lookback_bars: int = 1000, context: Optional["TimeframeContext"] = None, kline_end_time: Optional[int] = None, source: str = 'spot') -> Dict[str, List[Candle]]:
        """
        Fetch historical klines for all symbols via REST API to initialize history.
        source: 'spot' or 'perp'
        """
        history = {}
        logger.info(f"Fetching {lookback_bars} bars of {source} history for {len(self.symbols)} symbols...")
        
        # Spot: https://api.binance.com/api/v3/klines
        # Perp: https://fapi.binance.com/fapi/v1/klines
        
        base_url = "https://api.binance.com/api/v3/klines" if source == 'spot' else "https://fapi.binance.com/fapi/v1/klines"
        
        # Use context interval if available
        interval = f"{int(context.interval_ms // 60000)}m" if context else f"{CANDLE_TIMEFRAME_MINUTES}m"

        for index, symbol in enumerate(self.symbols):
            try:
                # Resolve symbol for request (handle 1000PEPE etc for Perps)
                req_symbol = symbol
                if source == 'perp':
                    req_symbol = PERP_SYMBOL_MAPPING.get(symbol, symbol)

                # Interval 1m, Limit = lookback
                params = {
                    "symbol": req_symbol.upper(),
                    "interval": interval,
                    "limit": lookback_bars
                }
                if kline_end_time:
                    params["endTime"] = kline_end_time
                
                resp = self.session.get(base_url, params=params, timeout=10)
                resp.raise_for_status()
                data = resp.json()
                
                candles = []
                for k in data:
                    # k schema: 
                    # 0: Open time, 1: Open, 2: High, 3: Low, 4: Close, 5: Volume, 
                    # 6: Close time, 7: Quote Vol, 8: Trades, 9: Taker Buy Base Asset Vol, ...
                    
                    vol = float(k[5])
                    taker_buy_vol = float(k[9])
                    
                    # CVD Approximation for History: 2 * TakerBuy - TotalVolume
                    # (Buy Vol - Sell Vol) = (TakerBuy) - (Total - TakerBuy) = 2*TakerBuy - Total
                    cvd_val = 2 * taker_buy_vol - vol
                    
                    c = Candle(
                        symbol=symbol,
                        timestamp=k[0],
                        open=float(k[1]),
                        high=float(k[2]),
                        low=float(k[3]),
                        close=float(k[4]),
                        volume=vol,
                        closed=True,
                        spot_cvd=cvd_val if source == 'spot' else 0.0,
                        perp_cvd=cvd_val if source == 'perp' else 0.0
                    )
                    candles.append(c)
                
                history[symbol] = candles
                
                # Small sleep to prevent rate limiting during init burst if many symbols
                if index % 10 == 0:
                    time.sleep(0.1)
                
            except Exception as e:
                logger.error(f"Failed to fetch {source} history for {symbol}: {e}")
                
        return history

    def fetch_latest_candle(self, symbol: str, context: Optional["TimeframeContext"] = None, source: str = 'spot') -> requests.Response:
        """
        Fetch the most recently closed candle for a specific symbol via REST API.
        This is used for reconciliation.
        """
        base_url = "https://api.binance.com/api/v3/klines" if source == 'spot' else "https://fapi.binance.com/fapi/v1/klines"
        interval = f"{int(context.interval_ms // 60000)}m" if context else f"{CANDLE_TIMEFRAME_MINUTES}m"
        try:
            # We want the LAST closed candle. 
            # Requesting limit=2 ensures we get the just-closed one + the currently forming one.
            # We will take the second to last item.
            
            # Resolve symbol for request
            req_symbol = symbol
            if source == 'perp':
                req_symbol = PERP_SYMBOL_MAPPING.get(symbol, symbol)
            
            params = {
                "symbol": req_symbol.upper(),
                "interval": interval,
                "limit": 2
            }
            resp = self.session.get(base_url, params=params, timeout=5)
            resp.raise_for_status()
            data = resp.json()
            
            if len(data) >= 2:
                # data[-2] is the fully closed candle we want
                k = data[-2]
                vol = float(k[5])
                taker_buy_vol = float(k[9])
                cvd_val = 2 * taker_buy_vol - vol
                
                return Candle(
                    symbol=symbol,
                    timestamp=k[0],
                    open=float(k[1]),
                    high=float(k[2]),
                    low=float(k[3]),
                    close=float(k[4]),
                    volume=vol,
                    spot_cvd=cvd_val if source == 'spot' else 0.0,
                    perp_cvd=cvd_val if source == 'perp' else 0.0,
                    closed=True
                )
        except Exception as e:
            # Log specific error if it's related to connections
            logger.error(f"Failed to fetch {source} latest candle for {symbol}: {e}")
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
        
        # Wrapper to ensure reconnection
        def _run_socket_forever(ws_app, name):
            retry_count = 0
            while self.keep_running:
                try:
                    logger.info(f"Starting {name} Websocket run_forever loop...")
                    # run_forever blocks until disconnection
                    # Optimized Settings based on data (Max lag observed = 18s)
                    # Timeout=30s provides >1.5x safety buffer against local blocking.
                    # Interval=35s satisfies library constraint (Interval > Timeout) and gives ~65s zombie detection.
                    ws_app.run_forever(ping_interval=35, ping_timeout=30)
                    
                    if not self.keep_running:
                        break
                        
                    logger.warning(f"{name} Websocket disconnected. Reconnecting...")
                    
                    # Exponential backoff with jitter
                    retry_count += 1
                    sleep_time = min(60, (2 ** retry_count)) + random.uniform(0, 1)
                    logger.info(f"Sleeping {sleep_time:.2f}s before {name} reconnect...")
                    time.sleep(sleep_time)
                    
                except Exception as e:
                    logger.error(f"Critical error in {name} run loop: {e}")
                    time.sleep(5)

        threading.Thread(target=_run_socket_forever, args=(self.ws_spot, "Spot"), daemon=True).start()
        threading.Thread(target=_run_socket_forever, args=(self.ws_perp, "Perp"), daemon=True).start()

    def stop(self):
        self.keep_running = False
        if self.ws_spot:
            self.ws_spot.close()
        if self.ws_perp:
            self.ws_perp.close()
        # Close the http session
        if self.session:
            self.session.close()

    def fetch_agg_trades(self, symbol: str, start_time: int, end_time: int) -> List[Trade]:
        """
        Fetch raw aggTrades for a specific time range. 
        Handles pagination (limit 1000 per request).
        """
        trades = []
        current_start = start_time
        
        # Determine source and URL based on symbol or config?
        # For now, we assume standard pairs. 
        # Ideally, we should know if a symbol is spot or perp. 
        # But our system mixes them (AAVEUSDT implies both).
        # We need to fetch BOTH Spot and Perp trades for the same ticker!
        
        # --- Helper to fetch from one source ---
        def _fetch_source(url_base: str, source_type: str):
            source_trades = []
            curr = start_time
            
            # Resolve symbol for request
            req_symbol = symbol
            if source_type == 'perp':
                req_symbol = PERP_SYMBOL_MAPPING.get(symbol, symbol)
            
            while True:
                if curr >= end_time:
                    break
                
                params = {
                    "symbol": req_symbol.upper(),
                    "startTime": curr,
                    "endTime": end_time,
                    "limit": 1000
                }
                
                try:
                    resp = self.session.get(url_base, params=params, timeout=10)
                    resp.raise_for_status()
                    data = resp.json()
                    
                    if not data:
                        break
                        
                    for t in data:
                        # aggTrade format:
                        # Spot: { "a": 26129, "p": "0.01633102", "q": "4.70443515", "f": 27781, "l": 27781, "T": 1498793709153, "m": true, "M": true }
                        # Perp: { "a": 26129, "p": "0.01633102", "q": "4.70443515", "f": 27781, "l": 27781, "T": 1498793709153, "m": true }
                        ts = int(t['T'])
                        if ts > end_time:
                            break
                            
                        tr = Trade(
                            symbol=symbol.upper(),
                            price=float(t['p']),
                            quantity=float(t['q']),
                            timestamp=ts,
                            is_buyer_maker=t['m'],
                            source=source_type
                        )
                        source_trades.append(tr)
                        
                    # Update cursor to last timestamp + 1 to avoid dupes
                    last_ts = int(data[-1]['T'])
                    if last_ts == curr:
                         # Stuck loop protection
                         curr += 1000 
                    else:
                         curr = last_ts + 1
                         
                    # Optimization: if we got < 1000, we're likely done
                    if len(data) < 1000:
                        break
                        
                except Exception as e:
                    logger.error(f"Failed to fetch aggTrades for {symbol} ({source_type}): {e}")
                    break
            return source_trades

        # Fetch Spot
        spot_trades = _fetch_source("https://api.binance.com/api/v3/aggTrades", "spot")
        
        # Fetch Perp
        perp_trades = _fetch_source("https://fapi.binance.com/fapi/v1/aggTrades", "perp")
        
        # Merge and Sort
        all_trades = spot_trades + perp_trades
        all_trades.sort(key=lambda x: x.timestamp)
        
        return all_trades
