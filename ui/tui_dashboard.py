from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import Static, DataTable, Footer


# ---------------------------------------------------------------------------
# Backend-facing models (these will eventually be fed by Krakked services)
# ---------------------------------------------------------------------------


@dataclass
class PositionRow:
    pair: str
    size: float
    entry: float
    mark: float
    pnl_usd: float
    has_warning: bool = False  # e.g. drift / special status


@dataclass
class AssetRow:
    asset: str
    local: float
    exchange: float
    value_usd: float
    integrity: str  # "ok", "drift", "unknown"


@dataclass
class PortfolioSummary:
    total_equity: float
    unrealized_pnl: float
    session_realized_pnl: float
    cash_usd: float
    drift_detected: bool
    data_stale: bool
    kill_switch_blocked: bool
    positions_ok: bool
    last_update: datetime


@dataclass
class LogEntry:
    ts: datetime
    level: str  # INFO, WARN, ERROR, EXEC, RISK, SYSTEM
    message: str


@dataclass
class RiskStatus:
    kill_switch_active: bool
    daily_drawdown_pct: float
    total_exposure_pct: float
    per_asset_exposure_pct: Dict[str, float]
    per_strategy_exposure_pct: Dict[str, float]


# ---------------------------------------------------------------------------
# Temporary dummy backend (replace with real Krakked wiring later)
# ---------------------------------------------------------------------------


class DummyBackend:
    """Temporary stand-in for PortfolioService + RiskEngine + logs."""

    def get_summary(self) -> PortfolioSummary:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        return PortfolioSummary(
            total_equity=12381.12,
            unrealized_pnl=321.12,
            session_realized_pnl=-45.18,
            cash_usd=4200.00,
            drift_detected=True,
            data_stale=True,
            kill_switch_blocked=True,
            positions_ok=True,
            last_update=now,
        )

    def get_positions(self) -> List[PositionRow]:
        return [
            PositionRow("XBT/USD", 0.1500, 61200, 62500, 195.00),
            PositionRow("ETH/USD", -1.2000, 3400, 3380, -24.00),
            PositionRow("SOL/USD", 10.0000, 145.00, 150.00, 50.00, has_warning=True),
        ]

    def get_assets(self) -> List[AssetRow]:
        return [
            AssetRow("USD", 4200.00, 4200.00, 4200.00, "ok"),
            AssetRow("XBT", 0.1500, 0.1500, 9375.00, "ok"),
            AssetRow("ETH", 1.2000, 1.2000, 4056.00, "ok"),
            AssetRow("DOGE", 50.0000, 0.0000, 5.00, "drift"),
        ]

    def get_logs(self) -> List[LogEntry]:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        return [
            LogEntry(now, "INFO", "System synchronized successfully."),
            LogEntry(now, "EXEC", "BUY 0.15 XBT/USD @ 61200"),
            LogEntry(now, "WARN", "High slippage detected on ETH/USD order."),
            LogEntry(now, "ERROR", "API connection to exchange failed."),
        ]

    def get_risk_status(self) -> RiskStatus:
        return RiskStatus(
            kill_switch_active=True,
            daily_drawdown_pct=-3.4,
            total_exposure_pct=62.5,
            per_asset_exposure_pct={
                "XBT": 35.0,
                "ETH": 20.0,
                "SOL": 7.5,
            },
            per_strategy_exposure_pct={
                "trend_v1": 40.0,
                "mean_rev_v1": 15.0,
                "manual": 7.5,
            },
        )

    # These will be wired to real functions later
    def sync_portfolio(self) -> None:
        pass

    def halt_strategies(self) -> None:
        pass

    def emergency_stop(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------


class StatusPanel(Static):
    """Left sidebar: system status, integrity, actions, menu."""

    summary: reactive[Optional[PortfolioSummary]] = reactive(None)
    current_view: reactive[str] = reactive("dashboard")

    def update_summary(self, summary: PortfolioSummary) -> None:
        self.summary = summary

    def set_view(self, view: str) -> None:
        self.current_view = view

    def render(self) -> str:
        s = self.summary
        if s is None:
            return "Loading..."

        lines: list[str] = []

        # SYSTEM STATUS
        lines.append("[bold]SYSTEM STATUS[/bold]")
        lines.append("")
        lines.append(f"  [green]● ONLINE[/green]")

        lines.append("")
        lines.append("[bold]INTEGRITY[/bold]")

        def flag(label: str, ok: bool, style_ok: str, style_bad: str) -> None:
            icon = "✔" if ok else "⚠"
            style = style_ok if ok else style_bad
            lines.append(f"  [{style}]{icon} {label}[/]")

        flag("Positions OK", s.positions_ok, "green", "yellow")
        flag("Drift Detected", not s.drift_detected, "green", "yellow")
        flag("Stale Data", not s.data_stale, "green", "yellow")
        # If kill_switch_blocked is True, that means kill switch is ACTIVE
        flag("Blocked by Kill Switch", not s.kill_switch_blocked, "green", "red")

        lines.append("")
        lines.append("[bold]ACTIONS[/bold]")
        lines.append("  [cyan]Sync Portfolio[/]   [dim](s)[/]")
        lines.append("  [cyan]Halt Strategies[/]  [dim](h)[/]")
        lines.append("  [red]Emergency Stop[/]    [dim](e)[/]")

        lines.append("")
        lines.append("[bold]MENU[/bold]")

        if self.current_view == "dashboard":
            lines.append("  [bold cyan]▸ Dashboard[/]")
            lines.append("    Risk")
        else:
            lines.append("    Dashboard")
            lines.append("  [bold cyan]▸ Risk[/]")
        lines.append("    Strategies")
        lines.append("    Trade History")
        lines.append("    Settings")

        return "\n".join(lines)


class SummaryTiles(Static):
    """Top-right summary block: equity, PnL, cash, last update."""

    summary: reactive[Optional[PortfolioSummary]] = reactive(None)

    def update_summary(self, summary: PortfolioSummary) -> None:
        self.summary = summary

    def render(self) -> str:
        s = self.summary
        if s is None:
            return "Loading..."

        def pnl_style(value: float) -> str:
            if value > 0:
                return "green"
            if value < 0:
                return "red"
            return "white"

        last = s.last_update.strftime("%Y-%m-%dT%H:%M:%SZ")

        return (
            f"[b]TOTAL EQUITY[/b]\n"
            f"  ${s.total_equity:,.2f}\n\n"
            f"[b]UNREALIZED PNL[/b]\n"
            f"  [{pnl_style(s.unrealized_pnl)}]{s.unrealized_pnl:+,.2f}[/]\n\n"
            f"[b]SESSION REALIZED[/b]\n"
            f"  [{pnl_style(s.session_realized_pnl)}]{s.session_realized_pnl:+,.2f}[/]\n\n"
            f"[b]AVAILABLE CASH[/b]\n"
            f"  ${s.cash_usd:,.2f}\n\n"
            f"[dim]Last update: {last}[/dim]"
        )


class PositionsTable(DataTable):
    """Positions table widget."""

    def on_mount(self) -> None:
        self.cursor_type = "row"

    def populate(self, positions: List[PositionRow]) -> None:
        self.clear(columns=True)
        self.add_columns("PAIR", "SIZE", "ENTRY", "MARK", "PNL")
        for pos in positions:
            style = ""
            if pos.pnl_usd > 0:
                style = "green"
            elif pos.pnl_usd < 0:
                style = "red"
            row = [
                f"{'⚠ ' if pos.has_warning else ''}{pos.pair}",
                f"{pos.size:,.4f}",
                f"{pos.entry:,.2f}",
                f"{pos.mark:,.2f}",
                f"[{style}]{pos.pnl_usd:+,.2f}[/]" if style else f"{pos.pnl_usd:+,.2f}",
            ]
            self.add_row(*row)


class AssetTable(DataTable):
    """Asset integrity table widget."""

    def on_mount(self) -> None:
        self.cursor_type = "row"

    def populate(self, assets: List[AssetRow]) -> None:
        self.clear(columns=True)
        self.add_columns("ASSET", "LOCAL", "EXCHANGE", "VALUE (USD)", "INTEGRITY")
        for a in assets:
            if a.integrity == "ok":
                icon = "[green]✔[/]"
            elif a.integrity == "drift":
                icon = "[yellow]⚠[/]"
            else:
                icon = "[red]?[/]"
            self.add_row(
                a.asset,
                f"{a.local:,.4f}",
                f"{a.exchange:,.4f}",
                f"{a.value_usd:,.2f}",
                icon,
            )


class LogPanel(Static):
    """Log output at bottom."""

    logs: reactive[List[LogEntry]] = reactive([])

    def update_logs(self, entries: List[LogEntry]) -> None:
        self.logs = entries

    def render(self) -> str:
        if not self.logs:
            return "[dim]No log entries.[/dim]"

        lines: list[str] = []
        for entry in self.logs:
            ts = entry.ts.strftime("%Y-%m-%dT%H:%M:%SZ")
            level = entry.level.upper()
            if level == "INFO":
                style = "cyan"
            elif level == "WARN":
                style = "yellow"
            elif level == "ERROR":
                style = "red"
            elif level == "EXEC":
                style = "magenta"
            elif level == "RISK":
                style = "yellow"
            else:
                style = "white"
            lines.append(f"{ts} [{style}]{level}[/] {entry.message}")
        return "\n".join(lines)


class RiskSummaryPanel(Static):
    """Top block on Risk view."""

    risk: reactive[Optional[RiskStatus]] = reactive(None)

    def update_risk(self, risk: RiskStatus) -> None:
        self.risk = risk

    def render(self) -> str:
        r = self.risk
        if r is None:
            return "Loading..."

        ks_style = "red" if r.kill_switch_active else "green"
        ks_label = "ACTIVE" if r.kill_switch_active else "INACTIVE"

        dd_color = "red" if r.daily_drawdown_pct < 0 else "green"
        exp_color = "yellow" if r.total_exposure_pct > 80 else "white"

        return (
            f"[b]KILL SWITCH:[/] [{ks_style}]{ks_label}[/]\n"
            f"[b]Daily Drawdown:[/] [{dd_color}]{r.daily_drawdown_pct:+.2f}%[/]\n"
            f"[b]Total Exposure:[/] [{exp_color}]{r.total_exposure_pct:.2f}%[/]"
        )


class RiskExposureTable(DataTable):
    """Generic exposure table for risk view."""

    def on_mount(self) -> None:
        self.cursor_type = "row"

    def populate_from_dict(self, data: Dict[str, float], label: str) -> None:
        self.clear(columns=True)
        self.add_columns(label, "% of Equity")
        for key, pct in data.items():
            style = "yellow" if pct > 50 else "white"
            self.add_row(key, f"[{style}]{pct:.2f}%[/]")


# ---------------------------------------------------------------------------
# Main TUI App
# ---------------------------------------------------------------------------


class KrakkedDashboard(App):
    """TUI dashboard for Krakked in the style of the web mock."""

    CSS_PATH = "dashboard.css"

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("s", "sync_portfolio", "Sync Portfolio"),
        ("h", "halt_strategies", "Halt Strategies"),
        ("e", "emergency_stop", "Emergency Stop"),
        ("1", "view_dashboard", "Dashboard"),
        ("2", "view_risk", "Risk"),
    ]

    def __init__(self, backend: Optional[DummyBackend] = None) -> None:
        super().__init__()
        self.backend = backend or DummyBackend()
        self.current_view: str = "dashboard"

    def compose(self) -> ComposeResult:
        # Top-level: sidebar + right side (dashboard/risk)
        with Horizontal():
            self.status_panel = StatusPanel(id="status")
            yield self.status_panel

            with Vertical(id="right_root"):
                # DASHBOARD VIEW
                with Vertical(id="dashboard_root"):
                    self.summary_panel = SummaryTiles(id="summary")
                    yield self.summary_panel

                    with Horizontal(id="middle"):
                        with Vertical(id="positions_block"):
                            yield Static("POSITIONS", classes="block-title")
                            self.positions_table = PositionsTable(zebra_stripes=True)
                            yield self.positions_table

                        with Vertical(id="assets_block"):
                            yield Static("ASSET INTEGRITY", classes="block-title")
                            self.asset_table = AssetTable(zebra_stripes=True)
                            yield self.asset_table

                    with Vertical(id="log_block"):
                        yield Static("LOG", classes="block-title")
                        self.log_panel = LogPanel()
                        yield self.log_panel

                # RISK VIEW (second screen)
                with Vertical(id="risk_root"):
                    yield Static("RISK STATUS", classes="block-title")
                    self.risk_summary_panel = RiskSummaryPanel(id="risk_summary")
                    yield self.risk_summary_panel

                    with Horizontal(id="risk_tables"):
                        with Vertical(id="risk_asset_block"):
                            yield Static("PER-ASSET EXPOSURE", classes="block-title")
                            self.risk_asset_table = RiskExposureTable(zebra_stripes=True)
                            yield self.risk_asset_table

                        with Vertical(id="risk_strategy_block"):
                            yield Static("PER-STRATEGY EXPOSURE", classes="block-title")
                            self.risk_strategy_table = RiskExposureTable(zebra_stripes=True)
                            yield self.risk_strategy_table

        yield Footer()

    def on_mount(self) -> None:
        # Only show dashboard at start
        self.set_view("dashboard")
        self.refresh_all()

    # ------------------------------------------------------------------
    # View switching
    # ------------------------------------------------------------------

    def set_view(self, view: str) -> None:
        self.current_view = view
        dash = self.query_one("#dashboard_root", Vertical)
        risk = self.query_one("#risk_root", Vertical)
        if view == "dashboard":
            dash.display = True
            risk.display = False
        else:
            dash.display = False
            risk.display = True
        self.status_panel.set_view(view)

    def action_view_dashboard(self) -> None:
        self.set_view("dashboard")

    def action_view_risk(self) -> None:
        self.set_view("risk")

    # ------------------------------------------------------------------
    # Actions (key bindings)
    # ------------------------------------------------------------------

    def action_sync_portfolio(self) -> None:
        self.backend.sync_portfolio()
        self.refresh_all()

    def action_halt_strategies(self) -> None:
        self.backend.halt_strategies()
        self.refresh_all()

    def action_emergency_stop(self) -> None:
        self.backend.emergency_stop()
        self.refresh_all()

    # ------------------------------------------------------------------
    # Data refresh
    # ------------------------------------------------------------------

    def refresh_all(self) -> None:
        summary = self.backend.get_summary()
        positions = self.backend.get_positions()
        assets = self.backend.get_assets()
        logs = self.backend.get_logs()
        risk_status = self.backend.get_risk_status()

        # Dashboard
        self.status_panel.update_summary(summary)
        self.summary_panel.update_summary(summary)
        self.positions_table.populate(positions)
        self.asset_table.populate(assets)
        self.log_panel.update_logs(logs)

        # Risk view
        self.risk_summary_panel.update_risk(risk_status)
        self.risk_asset_table.populate_from_dict(
            risk_status.per_asset_exposure_pct, "ASSET"
        )
        self.risk_strategy_table.populate_from_dict(
            risk_status.per_strategy_exposure_pct, "STRATEGY"
        )


def main() -> None:
    app = KrakkedDashboard()
    app.run()


if __name__ == "__main__":
    main()
