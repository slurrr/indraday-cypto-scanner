import os

# Symbols to scan
SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "DOGEUSDT", 
    "XRPUSDT", "ADAUSDT", "ONDOUSDT", "LTCUSDT", "AVAXUSDT",
    "XLMUSDT", "ZECUSDT", "FILUSDT", "AAVEUSDT", "LINKUSDT",
    "XPLUSDT", "NEARUSDT", "PEPEUSDT", "WLFIUSDT", "LINEAUSDT",
    "PUMPUSDT", "SHIBUSDT", "FLOKIUSDT", "BONKUSDT", "LUNCUSDT",
]

# Map internal scanner symbols (Spot) to Binance Perp symbols if different
PERP_SYMBOL_MAPPING = {
    "PEPEUSDT": "1000PEPEUSDT",
    "SHIBUSDT": "1000SHIBUSDT",
    "FLOKIUSDT": "1000FLOKIUSDT",
    "BONKUSDT": "1000BONKUSDT",
    "LUNCUSDT": "1000LUNCUSDT",
    "RATSUSDT": "1000RATSUSDT",
}

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

# WATCH Promotion Config
WATCH_ELIGIBLE_PATTERNS = [
    "VWAP_RECLAIM",
    "IGNITION",
    "PULLBACK",
    "TRAP", 
    "FAILED_BREAKOUT"
]

ACT_ELIGIBLE_PATTERNS = [
    "VWAP_RECLAIM",
    "IGNITION",
    "PULLBACK",
    "TRAP", 
    "FAILED_BREAKOUT"
]

ACT_DEMOTION_PATTERNS = []

# Durations (ms)
MAX_ACT_DURATION_MS = 15 * 60 * 1000  # 15 minutes
MAX_WATCH_DURATION_MS = 60 * 60 * 1000  # 60 minutes

# Alert Scoring

# Alert Scoring
MIN_ALERT_SCORE = 50
SCORING_WEIGHTS = {
    "BASE_PATTERN": 50,
    "FLOW_ALIGNMENT": 20,
    "VOLATILITY": 15,
    "CONTEXT": 15
}

# Logging
LOG_LEVEL = "DEBUG"
LOG_FILE = "utils/scanner.log"
DEBUG_LOG_FILE = "utils/debug_scanner.log"

# Flow Thresholds
BASE_FLOW_SLOPE_THRESHOLD_1M = 0.5 # Deprecated?
SLOPE_Z_SCORE_WINDOW = 60 # Lookback to normalize slope variance
FLOW_SLOPE_THRESHOLD = 0.5 # Threshold in Sigma. 0.5 = Top ~30% (Mild). 1.0 = Mod. 2.0 = Extreme.


# Debug mode for analyzer
ANALYZER_DEBUG = True   # Set False for production

# Feature Toggles
ENABLE_EXEC_ALERTS = False  # Set True to enable 1m EXEC alerts, False to pause them

# UI Configuration
ENABLE_STATE_MONITOR = True # Toggle to show/hide the State Monitor panel