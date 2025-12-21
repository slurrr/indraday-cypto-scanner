from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, Any, Optional, List

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

class ExecutionType(str, Enum):
    EXEC = "EXEC"

@dataclass(slots=True)
class Trade:
    symbol: str
    price: float
    quantity: float
    timestamp: int  # Milliseconds
    is_buyer_maker: bool
    source: str = "spot"  # 'spot' or 'perp'

@dataclass(slots=True)
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

    # Cumulative State (for incremental updates)
    cum_pv: float = 0.0
    cum_vol: float = 0.0
    cum_spot_cvd: float = 0.0 # Cumulative sum of spot_cvd up to this candle
    cum_perp_cvd: float = 0.0 # Cumulative sum of perp_cvd up to this candle
    
@dataclass
class TimeframeContext:
    name: str
    interval_ms: int

class State(Enum):
    IGNORE = "IGNORE"
    WATCH = "WATCH"
    ACT = "ACT"

@dataclass
class StateSnapshot:
    symbol: str
    state: State = State.IGNORE
    entered_at: int = 0
    last_updated_at: int = 0
    watch_reason: Optional[str] = None
    act_reason: Optional[str] = None
    act_direction: Optional[str] = None
    reasons: List[str] = field(default_factory=list)
    active_patterns: List[str] = field(default_factory=list)
    permission: Optional["PermissionSnapshot"] = None

@dataclass
class PermissionSnapshot:
    symbol: str
    computed_at: int
    bias: str  # e.g., "BULLISH", "BEARISH", "NEUTRAL"
    volatility_regime: str  # e.g., "LOW", "NORMAL", "HIGH"
    allowed: bool
    reasons: List[str] = field(default_factory=list)
    timeframe: str = "15m"

@dataclass(slots=True)
class Alert:
    timestamp: int
    candle_timestamp: int
    symbol: str
    pattern: PatternType | ExecutionType
    score: float
    flow_regime: FlowRegime
    price: float
    message: str
    direction: Optional[str] = None
    timeframe: str = "3m"  # Default for backward compatibility during refactor

    @property
    def is_execution(self) -> bool:
        return isinstance(self.pattern, ExecutionType)
    
    def __str__(self):
        return f"[{self.timeframe}] {self.symbol} | {self.pattern.value} | {self.flow_regime.value} | Score: {self.score:.1f}"

@dataclass
class ExecutionSignal:
    symbol: str
    timestamp: int
    price: float
    direction: str  # "LONG" or "SHORT"
    reason: str
    strength: float = 0.0
    
    def __str__(self):
        return f"[EXEC] {self.symbol} {self.direction} @ {self.price} | {self.reason} ({self.strength:.1f})"
