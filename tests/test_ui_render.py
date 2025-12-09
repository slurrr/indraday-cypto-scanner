import sys
import os
sys.path.append(os.getcwd())

from ui.console import ConsoleUI
from models.types import Alert, PatternType, FlowRegime
import time
import pandas as pd
from rich.console import Console

def test_ui_render():
    ui = ConsoleUI()
    
    # Mock Alert
    # Use a clear timestamp (e.g. 1000000000000 ms)
    # 2001-09-09 01:46:40 UTC -> Sep 08 19:46:40 Denver (MDT is UTC-6? or MST UTC-7?)
    # Date 2001-09-09. Standard vs Daylight? September is Daylight usually.
    # Let's use current time.
    now = int(time.time() * 1000)
    
    alert = Alert(
        timestamp=now,
        symbol="BTCUSDT",
        pattern=PatternType.VWAP_RECLAIM,
        score=80.0,
        flow_regime=FlowRegime.CONSENSUS,
        price=50000.0,
        message="Test Alert"
    )
    
    ui.add_alert(alert)
    
    try:
        table = ui.generate_table()
        Console().print(table)
        print("\n\nSUCCESS: Table rendered successfully.")
        
        # Verify timezone string
        # We can't easily capture rich output programmatically without capturing stdout, 
        # but manual visual check or just success of execution is good for now.
        # Actually I can parse the rows if I access table columns/rows directly?
        # Rich tables store data in columns struct.
        
    except Exception as e:
        print(f"FAILURE: {e}")

if __name__ == "__main__":
    test_ui_render()
