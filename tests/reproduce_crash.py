
import sys
import os
import time
from typing import List
from collections import OrderedDict
import threading

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.types import Alert, ExecutionType, FlowRegime, PatternType
from rich.console import Console

# Mock UI
class MockUI:
    def __init__(self):
        self.lock = threading.Lock()
        self.alerts = []
        self.dirty = False
    
    def add_alert(self, alert):
        with self.lock:
            self.alerts.append(alert)
        print(f"UI RECEIVED ALERT: {alert}")

# Mock Logger
class MockLogger:
    def info(self, msg):
        print(f"LOG: {msg}")

logger = MockLogger()
ui = MockUI()
console = Console()

# Mock Sent Alerts (from main.py)
sent_alerts = OrderedDict()
sent_alerts_lock = threading.Lock()

def handle_alerts(alerts: List[Alert]):
    new_unique_alerts = []
    with sent_alerts_lock:
        for alert in alerts:
            # Deduplication Key: Symbol + Pattern + Candle Timestamp
            key = (alert.symbol, alert.pattern.value, alert.candle_timestamp)
            
            if key not in sent_alerts:
                sent_alerts[key] = True # Mark as seen
                new_unique_alerts.append(alert)
                
                # Enforce Size Cap (FIFO)
                if len(sent_alerts) > 10000:
                    sent_alerts.popitem(last=False)
    
    for alert in new_unique_alerts:
        ui.add_alert(alert)
        logger.info(f"ALERT: {alert}")

def test_crash():
    print("Testing Alert Creation...")
    
    try:
        # Mimic main.py analyze_1m logic
        
        # 1. Create dummy values
        timestamp = int(time.time() * 1000)
        candle_timestamp = timestamp - 60000
        symbol = "BTCUSDT"
        pattern = ExecutionType.EXEC
        score = 10.0
        flow_regime = FlowRegime.BULLISH_CONSENSUS
        price = 90000.0
        message = "LONG: Price > VWAP"
        direction = "LONG"
        
        # 2. Instantiate Alert (Exact syntax from main.py)
        alert = Alert(
            timestamp=int(time.time() * 1000),
            candle_timestamp=candle_timestamp,
            symbol=symbol,
            pattern=pattern,
            score=score, 
            flow_regime=flow_regime,
            price=price,
            message=message,
            timeframe="1m",
            direction=direction
        )
        print("Alert created successfully.")
        
        # 3. Call handle_alerts
        handle_alerts([alert])
        print("handle_alerts finished successfully.")
        
    except Exception as e:
        print(f"CRASHED: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_crash()
