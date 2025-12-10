from enum import Enum
from dataclasses import dataclass, field
from typing import List, Optional
from datetime import datetime
from typing import Protocol, Any

class StatusSink(Protocol):
    def feed_connected(self):
        ...

    def tick(self):
        ...

    def alert_fired(self, n: int = 1):
        ...

    def error(self, msg: str):
        ...

class FlowRegime(str, Enum):
    BULLISH_CONSENSUS = "FLOW_BULLISH"
    BEARISH_CONSENSUS = "FLOW_BEARISH"
    PERP_DOMINANT = "FLOW_PERP_DOMINANT"
    SPOT_DOMINANT = "FLOW_SPOT_DOMINANT"
    CONFLICT = "FLOW_CONFLICT"
    NEUTRAL = "FLOW_NEUTRAL"

class PatternType(str, Enum):
    VWAP_RECLAIM = "VWAP_RECLAIM"
    PULLBACK = "PULLBACK"
    TRAP = "TRAP"
    IGNITION = "IGNITION"
    FAILED_BREAKOUT = "FAILED_BREAKOUT"

@dataclass
class Trade:
    symbol: str
    price: float
    quantity: float
    timestamp: int  # Milliseconds
    is_buyer_maker: bool
    source: str = "spot"  # 'spot' or 'perp'

@dataclass
class Candle:
    symbol: str
    timestamp: int        # Open time (ms)
    open: float
    high: float
    low: float
    close: float
    volume: float
    spot_cvd: float = 0.0
    perp_cvd: float = 0.0
    closed: bool = False
    
    # Indicators
    vwap: Optional[float] = None
    atr: Optional[float] = None
    vwap_slope: Optional[float] = None
    atr_percentile: Optional[float] = None
    spot_cvd_slope: Optional[float] = None
    perp_cvd_slope: Optional[float] = None
    
@dataclass
class Alert:
    timestamp: int
    symbol: str
    pattern: PatternType
    score: float
    flow_regime: FlowRegime
    price: float
    message: str
    
    def __str__(self):
        return f"{self.symbol} | {self.pattern.value} | {self.flow_regime.value} | Score: {self.score:.1f}"
