from rich.console import Console
from rich.table import Table
from rich.live import Live
from rich.layout import Layout
from typing import List
import pandas as pd
from models.types import Alert, FlowRegime

class ConsoleUI:
    def __init__(self):
        self.console = Console()
        self.alerts: List[Alert] = []
        
    def add_alert(self, alert: Alert):
        self.alerts.insert(0, alert)
        # Keep only last 50 alerts
        self.alerts = self.alerts[:50]
        
    def generate_table(self) -> Table:
        table = Table(title="Intraday Flow Scanner")
        table.add_column("Time", style="cyan", no_wrap=True)
        table.add_column("Symbol", style="magenta")
        table.add_column("Pattern", style="green")
        table.add_column("Price", justify="right")
        table.add_column("Regime", justify="center")
        table.add_column("Score", justify="right")
        
        for alert in self.alerts:
            # Color code regime
            regime_style = "white"
            if alert.flow_regime == FlowRegime.BULLISH_CONSENSUS:
                regime_style = "bold green"
            elif alert.flow_regime == FlowRegime.BEARISH_CONSENSUS:
                regime_style = "bold red"
            elif alert.flow_regime == FlowRegime.CONFLICT:
                regime_style = "yellow"
                
            # Format time: Milliseconds -> UTC -> Denver
            ts = pd.to_datetime(alert.timestamp, unit='ms').tz_localize('UTC').tz_convert('America/Denver')
            time_str = ts.strftime('%H:%M:%S')

            table.add_row(
                time_str,
                alert.symbol,
                alert.pattern.value,
                f"{alert.price:.4f}",
                f"[{regime_style}]{alert.flow_regime.value}[/]",
                str(alert.score)
            )
        return table
