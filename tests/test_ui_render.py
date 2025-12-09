import sys
import os
import io
import time
import pandas as pd
from rich.console import Console

if os.getcwd() not in sys.path:
    sys.path.append(os.getcwd())

from ui.console import ConsoleUI
from models.types import Alert, PatternType, FlowRegime

def test_ui_render():
    ui = ConsoleUI()
    
    # Mock Alert with fixed timestamp for deterministic testing
    # 2023-01-01 12:00:00 UTC = 1672574400000 ms
    # Denver (MST) is UTC-7: 05:00:00
    fixed_ts = 1672574400000
    
    alert = Alert(
        timestamp=fixed_ts,
        symbol="BTCUSDT",
        pattern=PatternType.VWAP_RECLAIM,
        score=85.5,
        flow_regime=FlowRegime.BULLISH_CONSENSUS,
        price=50123.45,
        message="Test Alert"
    )
    
    ui.add_alert(alert)
    
    try:
        table = ui.generate_table()
        
        # Capture output to verify rendering
        capture_console = Console(file=io.StringIO(), width=120)
        capture_console.print(table)
        output = capture_console.file.getvalue()
        
        # Verify content
        assert "Intraday Flow Scanner" in output, "Title not found"
        assert "BTCUSDT" in output, "Symbol not found"
        assert "VWAP_RECLAIM" in output, "Pattern not found"
        assert "FLOW_BULLISH" in output, "Regime not found"
        
        # Verify Price formatting (4 decimal places per ui/console.py)
        assert "50123.4500" in output, "Price formatting incorrect"
        
        # Verify Score
        assert "85.5" in output, "Score not found"
        
        # Verify Timezone conversion (UTC 12:00 -> Denver 05:00)
        # 12:00 UTC is 05:00 MST
        assert "05:00:00" in output, f"Time conversion incorrect. Output contained: {output}"
        
        print("SUCCESS: Table rendered successfully and assertions passed.")
        
    except Exception as e:
        print(f"FAILURE: {e}")
        # Re-raise to ensure CI failure
        raise e

if __name__ == "__main__":
    test_ui_render()
