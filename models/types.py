from enum import Enum
from dataclasses import dataclass, field
from typing import List, Optional
from datetime import datetime

class FlowRegime(str, Enum):
    CONSENSUS = "FLOW_CONSENSUS"
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
