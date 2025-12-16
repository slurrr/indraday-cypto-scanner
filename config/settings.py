import os

# Symbols to scan
SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "DOGEUSDT", 
    "XRPUSDT", "ADAUSDT", "MATICUSDT", "LTCUSDT", "AVAXUSDT",
    "XLMUSDT", "ZECUSDT", "FILUSDT", "AAVEUSDT", "LINKUSDT",
    "XPLUSDT", "NEARUSDT", "PEPEUSDT", "WLFIUSDT", "LINEAUSDT",
]

# Timeframes
TIMEFRAME_1M = "1m"
TIMEFRAME_3M = "3m"
TIMEFRAME_5M = "5m"
TIMEFRAME_15M = "15m"

# Candle timeframe (minutes)
CANDLE_TIMEFRAME_MINUTES = 3

# Websocket URLs
BINANCE_SPOT_WS_URL = "wss://stream.binance.com:9443/ws"
BINANCE_PERP_WS_URL = "wss://fstream.binance.com/ws"

# Calculation Windows
VWAP_SESSION_START_HOUR_UTC = 0  # Crypto is 24/7, but let's reset daily at UK midnight for now or rolling
ATR_WINDOW = 14
ATR_PERCENTILE_WINDOW = 100 # Lookback for percentile rank

# Pattern Thresholds
MIN_ATR_PERCENTILE = 20 # Below this, FLOW_NEUTRAL
IMPULSE_THRESHOLD_ATR = 2.0 # Price move > 2 * ATR required for impulse
IGNITION_EXPANSION_THRESHOLD_ATR = 1.5
PULLBACK_COMPRESSION_THRESHOLD_ATR = 0.8
PULLBACK_VWAP_DISTANCE_ATR = 0.5
SESSION_LOOKBACK_WINDOW = 60
PULLBACK_MAX_DEPTH_ATR = 1.5

# Alert Scoring
MIN_ALERT_SCORE = 50
SCORING_WEIGHTS = {
    "BASE_PATTERN": 50,
    "FLOW_ALIGNMENT": 20,
    "VOLATILITY": 15,
    "CONTEXT": 15
}

# Logging
LOG_LEVEL = "INFO"
LOG_FILE = "utils/scanner.log"
DEBUG_LOG_FILE = "utils/debug_scanner.log"

# Flow Thresholds
BASE_FLOW_SLOPE_THRESHOLD_1M = 0.5
FLOW_SLOPE_THRESHOLD = BASE_FLOW_SLOPE_THRESHOLD_1M * CANDLE_TIMEFRAME_MINUTES # Dynamic scaling per timeframe

# Debug mode for analyzer
ANALYZER_DEBUG = True   # Set False for production