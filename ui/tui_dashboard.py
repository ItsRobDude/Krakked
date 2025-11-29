from __future__ import annotations

import logging
import os
import importlib.util
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Protocol

import requests

TEXTUAL_AVAILABLE = importlib.util.find_spec("textual") is not None

if TEXTUAL_AVAILABLE:
    from textual.app import App, ComposeResult
    from textual.containers import Horizontal, Vertical
    from textual.reactive import reactive
    from textual.widgets import Static, DataTable, Footer

if not TEXTUAL_AVAILABLE:
    _TEXTUAL_MESSAGE = (
        "The Textual dependency is required for the TUI. Install the 'tui' extra via "
        "`poetry install -E tui` and re-run the dashboard."
    )
    if __name__ == "__main__":
        raise SystemExit(_TEXTUAL_MESSAGE)
    raise ImportError(_TEXTUAL_MESSAGE)


logger = logging.getLogger(__name__)


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
    system_mode: str = "unknown"
    ui_read_only: bool = False


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
# Backend protocol
# ---------------------------------------------------------------------------


class BackendProtocol(Protocol):
    def get_summary(self) -> PortfolioSummary:
        ...

    def get_positions(self) -> List[PositionRow]:
        ...

    def get_assets(self) -> List[AssetRow]:
        ...

    def get_logs(self) -> List[LogEntry]:
        ...

    def get_risk_status(self) -> RiskStatus:
        ...

    def set_kill_switch(self, active: bool) -> Optional[str]:
        ...

    def sync_portfolio(self) -> None:
        ...

    def halt_strategies(self) -> None:
        ...

    def emergency_stop(self) -> None:
        ...


# ---------------------------------------------------------------------------
# Temporary dummy backend (replace with real Krakked wiring later)
# ---------------------------------------------------------------------------


class DummyBackend:
    """Temporary stand-in for PortfolioService + RiskEngine + logs."""

    def __init__(self) -> None:
        self.kill_switch_active = True

    def get_summary(self) -> PortfolioSummary:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        return PortfolioSummary(
            total_equity=12381.12,
            unrealized_pnl=321.12,
            session_realized_pnl=-45.18,
            cash_usd=4200.00,
            drift_detected=True,
            data_stale=True,
            kill_switch_blocked=self.kill_switch_active,
            positions_ok=True,
            last_update=now,
            system_mode="paper",
            ui_read_only=False,
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
            kill_switch_active=self.kill_switch_active,
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

    def set_kill_switch(self, active: bool) -> Optional[str]:
        self.kill_switch_active = active
        return None

    # These will be wired to real functions later
    def sync_portfolio(self) -> None:
        pass

    def halt_strategies(self) -> None:
        pass

    def emergency_stop(self) -> None:
        pass


class HttpBackend:
    """HTTP-backed implementation that talks to the Krakked API."""

    def __init__(self, base_url: Optional[str] = None) -> None:
        self.base_url = (base_url or os.environ.get("KRAKKED_API_URL") or "http://localhost:8000").rstrip(
            "/"
        )
        self.session = requests.Session()
        token = os.environ.get("KRAKKED_API_TOKEN")
        if token:
            self.session.headers.update({"Authorization": f"Bearer {token}"})

    def _extract_error_message(self, response: Optional[requests.Response]) -> Optional[str]:
        if response is None:
            return None
        try:
            payload = response.json()
            if isinstance(payload, dict):
                return str(payload.get("error") or payload.get("detail"))
        except Exception:
            pass
        return response.text or None

    def _request(self, method: str, path: str, payload: Optional[dict] = None) -> dict:
        url = f"{self.base_url}{path}"
        response = self.session.request(method, url, json=payload, timeout=2)
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict) and payload.get("error"):
            raise RuntimeError(str(payload["error"]))
        return payload.get("data") if isinstance(payload, dict) else payload

    def _get(self, path: str) -> dict:
        return self._request("GET", path)

    def _post(self, path: str, payload: Optional[dict] = None) -> dict:
        return self._request("POST", path, payload)

    def get_summary(self) -> PortfolioSummary:
        health = self._get("/api/system/health") or {}
        data = self._get("/api/portfolio/summary") or {}
        last_snapshot = data.get("last_snapshot_ts")
        if isinstance(last_snapshot, str):
            try:
                last_dt = datetime.fromisoformat(last_snapshot.replace("Z", "+00:00"))
            except ValueError:
                last_dt = datetime.now(timezone.utc)
        else:
            last_dt = datetime.now(timezone.utc)

        drift_flag = bool(data.get("drift_flag", False))

        return PortfolioSummary(
            total_equity=float(data.get("equity_usd", 0.0)),
            unrealized_pnl=float(data.get("unrealized_pnl_usd", 0.0)),
            session_realized_pnl=float(data.get("realized_pnl_usd", 0.0)),
            cash_usd=float(data.get("cash_usd", 0.0)),
            drift_detected=drift_flag,
            data_stale=last_snapshot is None,
            kill_switch_blocked=False,
            positions_ok=not drift_flag,
            last_update=last_dt,
            system_mode=str(health.get("current_mode") or "unknown"),
            ui_read_only=bool(health.get("ui_read_only", False)),
        )

    def get_positions(self) -> List[PositionRow]:
        data = self._get("/api/portfolio/positions") or []
        positions: List[PositionRow] = []
        for item in data:
            size = float(item.get("base_size", 0.0))
            entry = float(item.get("avg_entry_price", 0.0))
            mark = float(item.get("current_price", entry or 0.0))
            pnl = item.get("unrealized_pnl_usd")
            if pnl is None:
                pnl = (mark - entry) * size
            positions.append(
                PositionRow(
                    pair=str(item.get("pair") or ""),
                    size=size,
                    entry=entry,
                    mark=mark,
                    pnl_usd=float(pnl),
                    has_warning=bool(item.get("strategy_tag")),
                )
            )
        return positions

    def get_assets(self) -> List[AssetRow]:
        exposure = self._get("/api/portfolio/exposure") or {}
        assets: List[AssetRow] = []
        for row in exposure.get("by_asset", []) or []:
            value = float(row.get("value_usd", 0.0))
            assets.append(
                AssetRow(
                    asset=str(row.get("asset") or ""),
                    local=value,
                    exchange=value,
                    value_usd=value,
                    integrity="ok",
                )
            )
        return assets

    def get_logs(self) -> List[LogEntry]:
        logs: List[LogEntry] = []
        data = self._get("/api/execution/recent_executions") or []
        for item in data:
            ts_raw = item.get("completed_at") or item.get("started_at")
            try:
                ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
            except Exception:
                ts = datetime.now(timezone.utc)

            level = "INFO" if item.get("success") else "ERROR"
            if item.get("errors"):
                level = "ERROR"
            elif item.get("warnings"):
                level = "WARN"

            message = f"{item.get('plan_id') or 'plan'} ({len(item.get('orders') or [])} orders)"
            logs.append(LogEntry(ts=ts, level=level, message=message))

        return logs

    def get_risk_status(self) -> RiskStatus:
        data = self._get("/api/risk/status") or {}
        return RiskStatus(
            kill_switch_active=bool(data.get("kill_switch_active", False)),
            daily_drawdown_pct=float(data.get("daily_drawdown_pct", 0.0)),
            total_exposure_pct=float(data.get("total_exposure_pct", 0.0)),
            per_asset_exposure_pct=data.get("per_asset_exposure_pct", {}) or {},
            per_strategy_exposure_pct=data.get("per_strategy_exposure_pct", {}) or {},
        )

    def set_kill_switch(self, active: bool) -> Optional[str]:
        try:
            self._post("/api/risk/kill_switch", {"active": active})
            return None
        except requests.HTTPError as exc:
            return self._extract_error_message(exc.response) or str(exc)
        except RuntimeError as exc:
            return str(exc)
        except Exception as exc:  # pragma: no cover - defensive
            return str(exc)

    def sync_portfolio(self) -> None:
        self._post("/api/portfolio/snapshot")

    def halt_strategies(self) -> None:
        self._post("/api/execution/cancel_all")

    def emergency_stop(self) -> None:
        self._post("/api/execution/flatten_all")


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
        lines.append(f"  Mode: [bold]{s.system_mode.upper()}[/bold]")
        if s.ui_read_only:
            lines.append("  [yellow]READ-ONLY[/]")

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
        disabled_note = " [yellow](read-only)[/]" if s.ui_read_only else ""
        live_gate = s.system_mode.lower() != "live"
        unavailable_note = " [dim](paper mode)[/]" if live_gate else ""
        control_style = "dim" if (s.ui_read_only or live_gate) else "cyan"
        lines.append(
            f"  [{control_style}]Sync Portfolio[/]   [dim](s)[/]{disabled_note or unavailable_note}"
        )
        lines.append(
            f"  [{control_style}]Halt Strategies[/]  [dim](h)[/]{disabled_note or unavailable_note}"
        )
        lines.append(
            f"  [red]{'Emergency Stop' if not (s.ui_read_only or live_gate) else '[dim]Emergency Stop[/]'}    [dim](e)[/]{disabled_note or unavailable_note}"
        )

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
    error_message: reactive[Optional[str]] = reactive(None)

    def update_risk(self, risk: RiskStatus) -> None:
        self.risk = risk

    def set_error(self, error: Optional[str]) -> None:
        self.error_message = error

    def render(self) -> str:
        r = self.risk
        if r is None:
            return "Loading..."

        ks_style = "red" if r.kill_switch_active else "green"
        ks_label = "ACTIVE" if r.kill_switch_active else "INACTIVE"

        dd_color = "red" if r.daily_drawdown_pct < 0 else "green"
        exp_color = "yellow" if r.total_exposure_pct > 80 else "white"

        lines = [
            f"[b]KILL SWITCH:[/] [{ks_style}]{ks_label}[/]",
            f"[b]Daily Drawdown:[/] [{dd_color}]{r.daily_drawdown_pct:+.2f}%[/]",
            f"[b]Total Exposure:[/] [{exp_color}]{r.total_exposure_pct:.2f}%[/]",
        ]

        if self.error_message:
            lines.append(f"[red]Kill switch update failed:[/] {self.error_message}")

        return "\n".join(lines)


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
        ("k", "toggle_kill_switch", "Toggle Kill Switch"),
        ("1", "view_dashboard", "Dashboard"),
        ("2", "view_risk", "Risk"),
    ]

    def __init__(self, backend: Optional[BackendProtocol] = None) -> None:
        super().__init__()
        self.backend: BackendProtocol = backend or self._init_backend()
        self.current_view: str = "dashboard"

    def _init_backend(self) -> BackendProtocol:
        try:
            live_backend = HttpBackend()
            # Probe the API to confirm availability; fall back if it fails.
            live_backend.get_summary()
            logger.info("Using HttpBackend for dashboard data", extra={"base_url": live_backend.base_url})
            return live_backend
        except Exception as exc:  # pragma: no cover - defensive fallback
            logger.info("Falling back to DummyBackend: %s", exc)
            return DummyBackend()

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

    def _controls_enabled(self) -> bool:
        summary = self.status_panel.summary
        return bool(summary and not summary.ui_read_only and summary.system_mode.lower() == "live")

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
        if not self._controls_enabled():
            logger.info("Sync blocked: controls disabled (read-only or non-live mode)")
            return
        self.backend.sync_portfolio()
        self.refresh_all()

    def action_halt_strategies(self) -> None:
        if not self._controls_enabled():
            logger.info("Halt strategies blocked: controls disabled (read-only or non-live mode)")
            return
        self.backend.halt_strategies()
        self.refresh_all()

    def action_emergency_stop(self) -> None:
        if not self._controls_enabled():
            logger.info("Emergency stop blocked: controls disabled (read-only or non-live mode)")
            return
        self.backend.emergency_stop()
        self.refresh_all()

    def action_toggle_kill_switch(self) -> None:
        risk = self.risk_summary_panel.risk
        if risk is None:
            logger.info("Toggle kill switch skipped: risk status not loaded")
            return

        summary = self.status_panel.summary
        if summary and summary.ui_read_only:
            message = "Kill switch toggle blocked: UI is read-only"
            logger.warning(message)
            self.risk_summary_panel.set_error(message)
            return

        target_state = not risk.kill_switch_active
        prompt = "Activate kill switch? (y/N): " if target_state else "Deactivate kill switch? (y/N): "
        try:
            response = (self.console.input(prompt) if hasattr(self, "console") else input(prompt)).strip().lower()
        except Exception:
            response = ""

        if response not in {"y", "yes"}:
            logger.info("Kill switch toggle cancelled by user")
            return

        error = self.backend.set_kill_switch(target_state)
        if error:
            logger.error("Kill switch toggle failed: %s", error)
            self.risk_summary_panel.set_error(error)
        else:
            self.risk_summary_panel.set_error(None)

        self.refresh_risk_view()

    # ------------------------------------------------------------------
    # Data refresh
    # ------------------------------------------------------------------

    def refresh_all(self) -> None:
        summary = self.backend.get_summary()
        positions = self.backend.get_positions()
        assets = self.backend.get_assets()
        logs = self.backend.get_logs()

        # Dashboard
        self.status_panel.update_summary(summary)
        self.summary_panel.update_summary(summary)
        self.positions_table.populate(positions)
        self.asset_table.populate(assets)
        self.log_panel.update_logs(logs)

        self.refresh_risk_view()

    def refresh_risk_view(self) -> None:
        risk_status = self.backend.get_risk_status()
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
