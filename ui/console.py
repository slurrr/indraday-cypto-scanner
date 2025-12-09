from rich.console import Console
from rich.table import Table
from rich.live import Live
from rich.layout import Layout
from typing import List
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
            if alert.flow_regime == FlowRegime.CONSENSUS:
                regime_style = "bold green"
            elif alert.flow_regime == FlowRegime.CONFLICT:
                regime_style = "red"
                
            table.add_row(
                str(alert.timestamp), # TODO: formatting
                alert.symbol,
                alert.pattern.value,
                f"{alert.price:.4f}",
                f"[{regime_style}]{alert.flow_regime.value}[/]",
                str(alert.score)
            )
        return table
