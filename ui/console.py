from rich.table import Table
from rich.layout import Layout
import threading
from config.settings import ENABLE_STATE_MONITOR
from typing import List, Dict
import pandas as pd
from models.types import Alert, FlowRegime, State, StateSnapshot
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
        
        # State Monitor Data
        self.symbol_states: Dict[str, StateSnapshot] = {}
        
        # Throttling
        self.last_monitor_refresh = 0.0
        self.shadow_states: Dict[str, str] = {} # Key: Symbol, Value: Signature (State+Patterns)

        # Initialize Layout ONCE
        self.layout = self._init_layout()

    def _init_layout(self) -> Layout:
        layout = Layout()
        
        if ENABLE_STATE_MONITOR:
            # Split screen: Top (Alerts) 60%, Bottom (State + Status) 40%
            layout.split_column(
                Layout(name="table", ratio=6),
                Layout(name="lower_panel", ratio=4)
            )
            # Split lower panel: State Monitor (Top), Status Bar (Bottom fixed)
            layout["lower_panel"].split_column(
                Layout(name="state_monitor"),
                Layout(name="status", size=3)
            )
        else:
            # Original Layout
            layout.split_column(
                Layout(name="table", ratio=4),
                Layout(name="status", size=3),
            )
        return layout

    def feed_connected(self):
        self.status.feed_connected = True
        self.status.last_error = None
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

        logger.info(f"UI RECEIVED ALERT: {alert}")

    def update_state_monitor(self, states: Dict[str, StateSnapshot]):
        """
        Updates the local copy of symbol states for rendering.
        Throttles repaints to 1Hz unless a critical state change occurs.
        """
        if not ENABLE_STATE_MONITOR:
            return

        now = time()
        force_update = False
        
        # Check for critical changes (State or Pattern transitions)
        for sym, snap in states.items():
            # Create a signature for the visual state (excluding timer)
            sig = f"{snap.state.name}|{','.join(snap.active_patterns)}|{snap.permission}"
            
            if sym not in self.shadow_states or self.shadow_states[sym] != sig:
                self.shadow_states[sym] = sig
                force_update = True
        
        # Check for timer update (1Hz)
        if now - self.last_monitor_refresh > 1.0:
            force_update = True
            
        with self.lock:
            self.symbol_states = states.copy()
            
        if force_update:
             self.dirty = True
             self.last_monitor_refresh = now

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
    
    def generate_state_table(self) -> Table:
        """
        Generates a table showing the live state of each symbol.
        """
        table = Table(title="State Monitor")
        table.add_column("Symbol", style="cyan")
        table.add_column("State", justify="center")
        table.add_column("Bias / Perm", justify="center")
        table.add_column("Act Dir", justify="center")
        table.add_column("Active Patterns / Reason", style="dim")
        table.add_column("Time in State", justify="right")
        
        now = time() * 1000 # ms
        
        # Sort by State prominence (ACT > WATCH > IGNORE) then Symbol
        def sort_key(item):
            sym, snap = item
            score = 0
            if snap.state == State.ACT: score = 3
            elif snap.state == State.WATCH: score = 2
            elif snap.state == State.IGNORE: score = 1
            return (-score, sym)
            
        with self.lock:
            sorted_states = sorted(self.symbol_states.items(), key=sort_key)
            
        for symbol, snap in sorted_states:
            # State Color
            state_style = "dim white"
            if snap.state == State.ACT:
                 state_style = "bold green"
            elif snap.state == State.WATCH:
                 state_style = "bold yellow"
            
            # Permission String
            perm_str = "-"
            if snap.permission:
                # 1. Bias Color
                bias_color = "orange3" # Neutral default
                if snap.permission.bias == "BULLISH":
                    bias_color = "green"
                elif snap.permission.bias == "BEARISH":
                    bias_color = "red"
                
                # 2. Volatility Style
                vol_style = ""
                if snap.permission.volatility_regime == "HIGH":
                    vol_style = "bold "
                
                # 3. Allowed Status (Dim if not allowed)
                dim_style = "" if snap.permission.allowed else "dim "
                
                # Assemble
                final_style = f"{dim_style}{vol_style}{bias_color}"
                perm_str = f"[{final_style}]{snap.permission.bias} ({snap.permission.volatility_regime})[/]"
            
            # Reason
            reason = snap.act_reason if snap.state == State.ACT else (snap.watch_reason if snap.state == State.WATCH else "-")
            if snap.active_patterns:
                pat_str = ", ".join(snap.active_patterns)
                reason = f"{reason} [{pat_str}]"
            
            # Duration
            duration_s = int((now - snap.entered_at) / 1000) if snap.entered_at > 0 else 0
            m, s = divmod(duration_s, 60)
            h, m = divmod(m, 60)
            dur_str = f"{h:02d}:{m:02d}:{s:02d}"
            
            table.add_row(
                symbol,
                f"[{state_style}]{snap.state.name}[/]",
                perm_str,
                snap.act_direction or "-",
                str(reason),
                dur_str
            )
            
        return table

    def generate_layout(self) -> Layout:
        """
        Updates the content of the existing layout tree.
        """
        # Update Table (Always)
        self.layout["table"].update(self.generate_table())
        
        # Update Status and Monitor
        if ENABLE_STATE_MONITOR:
             self.layout["lower_panel"]["state_monitor"].update(self.generate_state_table())
             self.layout["lower_panel"]["status"].update(self.generate_status_panel())
        else:
             self.layout["status"].update(self.generate_status_panel())

        return self.layout

    def generate_table(self) -> Table:
        table = Table(title="Intraday Flow Scanner")
        table.add_column("Time", style="cyan", no_wrap=True)
        table.add_column("Symbol", style="magenta")
        table.add_column("Pattern", style="green")
        table.add_column("Direction", style="cyan")
        table.add_column("Price", justify="right")
        table.add_column("Regime", justify="center")
        table.add_column("Score", justify="right")
        
        with self.lock:
            # Copy alerts safely to minimize lock time during rendering layout
            current_alerts = self.alerts[:]
            
        for alert in current_alerts:
            # Color code regime using Z-Score tiers
            # Slopes in Alert are normalized Z-Scores now
            spot_z = alert.spot_slope
            perp_z = alert.perp_slope
            strongest_z = spot_z if abs(spot_z) > abs(perp_z) else perp_z
            
            z_mag = abs(strongest_z)
            
            # Tier Logic (Revised): Mild (<1.0), Moderate (1.0-2.0), Extreme (>2.0)
            # Colors: Green/Red ONLY for CONSENSUS. Others blend in.
            
            base_color = "white"
            style_prefix = ""
            
            # Helper to determine intensity
            def get_style_prefix(z_val):
                if z_val > 2.0: return "bold "
                if z_val < 1.0: return "dim "
                return ""

            if FlowRegime.BULLISH_CONSENSUS in (alert.flow_regime,): # Enum check
                 base_color = "green"
                 style_prefix = get_style_prefix(z)
            elif FlowRegime.BEARISH_CONSENSUS in (alert.flow_regime,):
                 base_color = "red"
                 style_prefix = get_style_prefix(z)
            elif FlowRegime.CONFLICT in (alert.flow_regime,):
                 base_color = "yellow"
                 style_prefix = get_style_prefix(z)
            
            # Dominant/Conflict/Neutral -> White (Default)
                
            regime_style = f"{style_prefix}{base_color}"

                
            # Pattern Style
            pattern_str = alert.pattern.value
            if alert.is_execution:
                 pattern_str = f"[bold white on blue] {alert.pattern.value} [/]"

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
                pattern_str,
                alert.direction or "-",
                f"{alert.price:.4f}",
                f"[{regime_style}]{alert.flow_regime.value}[/]",
                str(alert.score)
            )
        return table

    # Deprecated update helpers (logic moved to generate_layout)
    def update_table(self):
        pass

    def update_status(self):
        pass
