from rich.table import Table
from rich.layout import Layout
import threading
from typing import List
import pandas as pd
from models.types import Alert, FlowRegime
from dataclasses import dataclass
from time import time
from rich.panel import Panel
from rich.text import Text
from rich.layout import Layout
from datetime import datetime
from utils.logger import setup_logger

logger = setup_logger("ui")

@dataclass
class UIStatus:
    feed_connected: bool = False
    last_tick_ts: float | None = None
    total_alerts: int = 0
    last_error: str | None = None
    binance_client: "BinanceClient | None" = None

class ConsoleUI():
    def __init__(self, console):
        logger.info("ConsoleUI initialized")
        self.console = console
        self.alerts: List[Alert] = []
        self.dirty = False 
        self.status = UIStatus()
        self.lock = threading.Lock()

    def feed_connected(self):
        self.status.feed_connected = True
        self.dirty = True

    def tick(self):
        self.status.last_tick_ts = time()

    def alert_fired(self, n: int = 1):
        self.status.total_alerts += n
        self.dirty = True

    def error(self, msg: str):
        self.status.last_error = msg
        self.dirty = True
        
    def add_alert(self, alert: Alert):
        with self.lock:
            self.alerts.insert(0, alert)
            self.alerts = self.alerts[:50]
        self.alert_fired()
        self.dirty = True

        logger.info(f"UI RECEIVED ALERT: {alert.symbol} {alert.pattern}")

    def generate_status_panel(self) -> Panel:
        items = []

        # Feed
        if self.status.feed_connected:
            items.append("[green]Feed: OK[/]")
        else:
            items.append("[red]Feed: DISCONNECTED[/]")

        # Last tick
        now = time()

        if self.status.last_tick_ts is None:
            items.append("[yellow]Waiting for data[/]")
        else:
            age = now - self.status.last_tick_ts
            ts = datetime.fromtimestamp(self.status.last_tick_ts)
            ts_str = ts.strftime("%H:%M:%S")

            if age > 30:
                items.append("[red]Feed stale[/]")
            elif age > 10:
                items.append(f"[yellow]Last tick:[/] {ts_str}")
            else:
                items.append(f"[cyan]Last tick:[/] {ts_str}")

        # Alerts
        items.append(f"[magenta]Alerts:[/] {self.status.total_alerts}")

        # WS Metrics
        if self.status.binance_client:
            ws_metrics = self.status.binance_client.get_ws_metrics()
            items.append(
                f"[blue]WS Messages:[/] {ws_metrics.get('total', 0)} "
                f"(dropped {ws_metrics.get('dropped', 0)} {ws_metrics.get('drop_pct', 0.0):.2f}%)"
            )

        # Error (if any)
        if self.status.last_error:
            items.append(f"[red]Error:[/] {self.status.last_error}")

        content = "  |  ".join(items)
        return Panel(Text.from_markup(content), title="Status", border_style="blue")

    def generate_layout(self) -> Layout:
        layout = Layout()
        layout.split_column(
            Layout(self.generate_table(), name="table", ratio=4),
            Layout(self.generate_status_panel(), name="status", size=3),
        )
        self.layout = layout
        return layout

    def generate_table(self) -> Table:
        table = Table(title="Intraday Flow Scanner")
        table.add_column("Time", style="cyan", no_wrap=True)
        table.add_column("Symbol", style="magenta")
        table.add_column("Pattern", style="green")
        table.add_column("Price", justify="right")
        table.add_column("Regime", justify="center")
        table.add_column("Score", justify="right")
        
        with self.lock:
            # Copy alerts safely to minimize lock time during rendering layout
            current_alerts = self.alerts[:]

        for alert in current_alerts:
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

            # Add TradingView Link
            tv_link = (
                f"[link=https://www.tradingview.com/chart/?symbol=BINANCE:{alert.symbol}]"
                f"{alert.symbol}[/link]"
            )

            table.add_row(
                time_str,
                tv_link,
                alert.pattern.value,
                f"{alert.price:.4f}",
                f"[{regime_style}]{alert.flow_regime.value}[/]",
                str(alert.score)
            )
        return table

    def update_table(self):
        assert hasattr(self, "layout")
        self.layout["table"].update(self.generate_table())

    def update_status(self):
        assert hasattr(self, "layout")
        self.layout["status"].update(self.generate_status_panel())

