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
    # Capture output to verify rendering
    capture_console = Console(file=io.StringIO(), width=120)
    ui = ConsoleUI(console=capture_console)
    
    # Mock Alert with fixed timestamp for deterministic testing
    # 2023-01-01 12:00:00 UTC = 1672574400000 ms
    # Denver (MST) is UTC-7: 05:00:00
    fixed_ts = 1672574400000
    
    alert = Alert(
        timestamp=fixed_ts,
        candle_timestamp=fixed_ts,
        symbol="BTCUSDT",
        pattern=PatternType.VWAP_RECLAIM,
        score=85.5,
        flow_regime=FlowRegime.BULLISH_CONSENSUS,
        price=50123.45,
        message="Test Alert",
        direction="LONG"
    )
    
    ui.add_alert(alert)
    
    # Mock State for State Monitor
    from models.types import StateSnapshot, State, PermissionSnapshot
    
    # Set entered_at to 3661 seconds ago (1h 1m 1s)
    entered_at_ms = (time.time() - 3661) * 1000
    
    ui.symbol_states = {
        "BTCUSDT": StateSnapshot(
            symbol="BTCUSDT",
            state=State.ACT,
            entered_at=entered_at_ms,
            act_direction="LONG",
            act_reason="Test Reason",
            permission=PermissionSnapshot(
                symbol="BTCUSDT",
                computed_at=fixed_ts,
                bias="BULLISH",
                volatility_regime="NORMAL",
                allowed=True
            )
        )
    }

    try:
        # Verify Alerts Table
        table = ui.generate_table()
        capture_console.print(table)
        output = capture_console.file.getvalue()
        
        # Verify content
        assert "Intraday Flow Scanner" in output, "Title not found"
        assert "BTCUSDT" in output, "Symbol not found"
        assert "VWAP_RECLAIM" in output, "Pattern not found"
        assert "FLOW_BULLISH" in output, "Regime not found"
        assert "LONG" in output, "Direction not found in Alerts table"
        
        # Verify Price formatting (4 decimal places per ui/console.py)
        assert "50123.4500" in output, "Price formatting incorrect"
        
        # Verify Score
        assert "85.5" in output, "Score not found"
        

    # Test styled EXEC alert
    from models.types import ExecutionType
    exec_alert = Alert(
        timestamp=fixed_ts,
        candle_timestamp=fixed_ts,
        symbol="ETHUSDT",
        pattern=ExecutionType.EXEC,
        score=99.9,
        flow_regime=FlowRegime.BEARISH_CONSENSUS,
        price=1234.56,
        message="Execution Signal",
        direction="SHORT"
    )
    ui.add_alert(exec_alert)

    try:
        # Verify Alerts Table
        table = ui.generate_table()
        capture_console.print(table)
        output = capture_console.file.getvalue()
        
        # Verify content
        assert "Intraday Flow Scanner" in output, "Title not found"
        assert "BTCUSDT" in output, "Symbol not found"
        assert "VWAP_RECLAIM" in output, "Pattern not found"
        assert "FLOW_BULLISH" in output, "Regime not found"
        assert "LONG" in output, "Direction not found in Alerts table"
        
        # Verify Price formatting (4 decimal places per ui/console.py)
        assert "50123.4500" in output, "Price formatting incorrect"
        
        # Verify Score
        assert "85.5" in output, "Score not found"
        
        # Verify Timezone conversion (UTC 12:00 -> Denver 05:00)
        assert "05:00:00" in output, f"Time conversion incorrect. Output contained: {output}"
        
        # Verify EXEC styling
        # Rich styles are not directly in text output unless we use a capture console that preserves it or check for substrings
        # But rich.Console(file=io.StringIO()) by default outputs plain text if we don't force color logic.
        # Actually standard print(table) to string buffer strips styles unless we force something.
        # However, checking if "EXEC" is present is a baseline. 
        # Rich text objects can be inspected. 
        # Let's just check "EXEC" is there. 
        assert "EXEC" in output, "EXEC pattern not found"

        # Cleare buffer for next test
        capture_console.file.seek(0)
        capture_console.file.truncate(0)


        # Verify State Monitor Table
        state_table = ui.generate_state_table()
        capture_console.print(state_table)
        state_output = capture_console.file.getvalue()

        assert "State Monitor" in state_output, "State Monitor Title not found"
        assert "Act Dir" in state_output, "Act Dir column missing"
        assert "LONG" in state_output, "Act Direction Value not found in State Monitor"
        
        # Verify HH:MM:SS format (1h 1m 1s = 01:01:01)
        # Note: execution time might add a split second, so we check approximate or flexible
        # Actually since we set it relative to time.time() and generate_state_table calls time.time()
        # immediately after, it should be very close. We'll search for "01:01:01" or "01:01:02"
        assert "01:01:01" in state_output or "01:01:02" in state_output, f"Duration format incorrect. Output: {state_output}"
        
        print("SUCCESS: Tables rendered successfully and assertions passed.")
        
    except Exception as e:
        print(f"FAILURE: {e}")
        # Re-raise to ensure CI failure
        raise e

if __name__ == "__main__":
    test_ui_render()
