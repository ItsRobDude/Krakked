Phase 6 – UI & Control Plane Design Contract

1. Purpose & Scope

The UI & Control Plane is responsible for:
	•	Giving you a real‑time dashboard of:
	•	Equity, PnL, exposure, open positions (Phase 3).
	•	Strategy status, signals, risk state (Phase 4).
	•	Orders and executions (Phase 5).
	•	Providing controls for:
	•	Risk settings (max risk per trade, kill switch, etc.).
	•	Strategy enable/disable, config tweaks.
	•	Execution mode (paper/dry‑run vs live).
	•	Manual safety actions: cancel all, flatten portfolio, trigger snapshots.
	•	Exposing a stable HTTP API that a web frontend (and future tools) can use.

It is not responsible for:
	•	Core trading logic (no indicators or position sizing here).
	•	Direct Kraken calls (still only Phase 1/5 do that).
	•	PnL or portfolio math (that’s Phase 3).
	•	Strategy decisions or risk checks (that’s Phase 4).

Assumptions:
	•	Phases 1–5 are implemented and usable as Python modules.
	•	The bot runs as a long‑lived process on a host you control.
	•	We can add a small HTTP server (e.g. FastAPI/Starlette/Flask) and a frontend (simple JS/HTML or a small SPA).

⸻

2. High-Level Architecture

2.1 Components

Phase 6 adds two main components:
	1.	Backend API server (kraken_bot.ui.api):
	•	A lightweight HTTP server in the same process as the bot core.
	•	Exposes:
	•	GET endpoints to read state (portfolio, risk, strategies, orders, decisions).
	•	POST/PATCH endpoints to change config (risk, strategies, execution mode) and trigger actions (run cycle, kill switch, cancel all, flatten).
	•	Talks to:
	•	PortfolioService (Phase 3),
	•	StrategyRiskEngine (Phase 4),
	•	ExecutionService/OMS (Phase 5),
	•	MarketDataAPI (Phase 2).
	2.	Web UI frontend (kraken_bot.ui.web or static files served by the API):
	•	React/Vue or simple HTML+JS — implementation detail.
	•	Consumes the backend API.
	•	Provides:
	•	Dashboard,
	•	Risk Management panel,
	•	Strategy view,
	•	Orders & trades view,
	•	Settings.

2.2 Module layout

Add a new package:

src/kraken_bot/ui/
  __init__.py
  api.py           # HTTP API server (FastAPI/Flask/etc)
  routes/
    __init__.py
    portfolio.py   # endpoints under /api/portfolio
    risk.py        # /api/risk
    strategies.py  # /api/strategies
    execution.py   # /api/execution
    system.py      # /api/system (health, config, etc)
  web/
    index.html     # main UI shell
    static/        # JS/CSS bundle, icons, etc (or separate frontend repo)

The UI layer must not depend on KrakenRESTClient directly; it only sees the higher-level services.

⸻

3. Configuration (config.yaml)

Add a ui section:

ui:
  enabled: true
  host: "127.0.0.1"
  port: 8080
  base_path: "/"             # base URL prefix, e.g. "/krakked"
  auth:
    enabled: false           # if true, require auth token
    token: ""                # static API token for now (Phase 6)
  read_only: false           # if true, disable mutating endpoints (no config changes, no manual actions)
  refresh_intervals:
    dashboard_ms: 5000       # dashboard polling/WS update interval
    orders_ms: 5000
    strategies_ms: 10000

Defaults:
	•	enabled = true for dev, host=127.0.0.1, auth.enabled=false (local only).
	•	read_only=false for now (you can flip this in production).

Expose this config through AppConfig as a new UIConfig dataclass.

⸻

4. Backend API Design

4.1 API style

Use a simple JSON REST API, optionally with WebSockets later:
	•	Base path: /api
	•	All responses JSON: {"data": ..., "error": null | "message"}.
	•	Auth:
	•	If ui.auth.enabled, require a header: Authorization: Bearer <token>.

4.2 Portfolio endpoints

GET /api/portfolio/summary

Returns high‑level portfolio state:

{
  "equity_usd": 12345.67,
  "cash_usd": 2345.67,
  "realized_pnl_usd": 456.78,
  "unrealized_pnl_usd": 123.45,
  "drift_flag": false,
  "last_snapshot_ts": "2025-01-01T12:00:00Z"
}

Implementation:
	•	Calls PortfolioService.get_equity() and get_snapshots(limit=1).

⸻

GET /api/portfolio/positions

Returns current open positions:

[
  {
    "pair": "XBTUSD",
    "base_asset": "XBT",
    "base_size": 0.42,
    "avg_entry_price": 40000.0,
    "current_price": 41000.0,
    "value_usd": 17220.0,
    "unrealized_pnl_usd": 420.0,
    "strategy_tag": "trend_v1"  // optional, if attributable
  },
  ...
]

Implementation:
	•	Combines PortfolioService.get_positions() with current prices from MarketDataAPI.

⸻

GET /api/portfolio/exposure

Returns exposure by asset and optionally by strategy:

{
  "by_asset": [
    { "asset": "XBT", "value_usd": 17220.0, "pct_of_equity": 34.4 },
    { "asset": "ETH", "value_usd": 5000.0, "pct_of_equity": 10.0 }
  ],
  "by_strategy": [
    { "strategy_id": "trend_v1", "value_usd": 15000.0, "pct_of_equity": 30.0 },
    { "strategy_id": "manual", "value_usd": 2000.0, "pct_of_equity": 4.0 }
  ]
}

Implementation:
	•	PortfolioService.get_asset_exposure() plus strategy attribution using PnL/positions.

⸻

GET /api/portfolio/trades

Query trades:
	•	Query params:
	•	pair (optional)
	•	limit (default 100)
	•	since (optional timestamp)
	•	strategy_id (optional)

Response: list of simplified trade records (PnL, fees, strategy tag).

⸻

POST /api/portfolio/snapshot
	•	If not read_only:
	•	Calls PortfolioService.create_snapshot().
	•	Returns created snapshot summary.

⸻

4.3 Risk endpoints

GET /api/risk/status

Returns the current risk state:

{
  "kill_switch_active": false,
  "daily_drawdown_pct": -2.5,
  "drift_flag": false,
  "total_exposure_pct": 60.0,
  "per_asset_exposure_pct": {
    "XBT": 35.0,
    "ETH": 15.0
  },
  "per_strategy_exposure_pct": {
    "trend_v1": 40.0,
    "mean_rev_v1": 5.0,
    "manual": 15.0
  }
}

Implementation:
	•	Calls StrategyRiskEngine.get_risk_status().

⸻

GET /api/risk/config

Return current risk config:
	•	max_risk_per_trade_pct
	•	max_portfolio_risk_pct
	•	max_open_positions
	•	etc.

⸻

PATCH /api/risk/config
	•	If not read_only:
	•	Accepts partial updates (e.g. { "max_risk_per_trade_pct": 0.75 }).
	•	Validates values.
	•	Applies changes via StrategyRiskEngine.reload_config() / underlying config layer.
	•	Persists changes into a runtime config overlay (Phase 6 scope); persisting back to YAML can be a Phase 7 concern.

⸻

POST /api/risk/kill_switch

Payload: { "active": true | false }
	•	If true, activate kill switch (block new risk).
	•	If false, clear kill switch state (if allowed by config).

⸻

4.4 Strategy endpoints

GET /api/strategies

List strategies:

[
  {
    "id": "trend_v1",
    "type": "trend_following",
    "enabled": true,
    "userref": 12345,
    "timeframes": ["1h", "4h"],
    "max_positions": 6,
    "current_positions": 4,
    "pnl_summary": {
      "realized_usd": 1200.0,
      "unrealized_usd": 300.0
    },
    "last_intents_at": "2025-01-01T12:00:00Z",
    "last_actions_at": "2025-01-01T12:05:00Z"
  },
  ...
]

Implementation:
	•	StrategyRiskEngine.get_strategy_state().

⸻

PATCH /api/strategies/{strategy_id}/enabled
	•	If not read_only:
	•	Enable/disable a strategy at runtime.
	•	This toggles the strategy in StrategyRiskEngine and risk budgets accordingly.

⸻

GET /api/strategies/{strategy_id}/config

Returns the specific config (type, params, risk allocation).

⸻

PATCH /api/strategies/{strategy_id}/config
	•	If not read_only:
	•	Allow safe, limited runtime tuning (e.g. max_positions, target_allocation_pct).
	•	Restrict changes that would break invariants (e.g. cannot change type).

⸻

4.5 Execution / OMS endpoints

GET /api/execution/open_orders

Returns current open/partially filled LocalOrders:

[
  {
    "local_id": "uuid-1",
    "pair": "XBTUSD",
    "side": "buy",
    "order_type": "limit",
    "kraken_order_id": "OID123",
    "requested_base_size": 0.1,
    "requested_price": 40500.0,
    "status": "open",
    "cumulative_base_filled": 0.05,
    "avg_fill_price": 40450.0,
    "created_at": "...",
    "updated_at": "..."
  },
  ...
]

Implementation:
	•	ExecutionService.get_open_orders().

⸻

GET /api/execution/recent_executions
	•	limit query param.
	•	Returns recent ExecutionResult summaries.

⸻

POST /api/execution/cancel_all
	•	If not read_only:
	•	Calls ExecutionService.cancel_all().

⸻

POST /api/execution/cancel/{local_id}
	•	If not read_only:
	•	Calls ExecutionService.cancel_order(local_id).

⸻

POST /api/execution/flatten_all
	•	If not read_only:
	•	High-level action:
	•	Generate a “flatten” ExecutionPlan: for each position, target size = 0.
	•	Pass it through Phase 4 risk engine with a special flag “emergency flatten”.
	•	Execute via Phase 5.
	•	This is a coordinated cross-layer operation; API just triggers the orchestrator entry point.

⸻

4.6 System & control endpoints

GET /api/system/health
	•	Returns:
	•	market_data_ok (from MarketDataAPI.get_data_status()),
	•	portfolio_ok (no drift or drift acknowledged),
	•	strategy_engine_ok,
	•	execution_ok (OMS reachable, no fatal errors),
	•	current mode (live / paper / dry_run).

⸻

GET /api/system/config
	•	Return a redacted snapshot of current config (no secrets).

⸻

PATCH /api/system/execution_mode
	•	If not read_only:
	•	Switch between live, paper, dry_run modes.
	•	May require a restart or explicit “apply changes” action depending on implementation.

⸻

4.7 Authentication & security

Phase 6 baseline:
	•	Default: bind to 127.0.0.1 and auth.enabled=false for local dev.
	•	If ui.auth.enabled=true:
	•	Require Authorization: Bearer <token> for all requests except /api/system/health.
	•	Rate limit or protect destructive actions (flatten_all, cancel_all) with:
	•	Confirmations at UI level (e.g. “Type FLATTEN to confirm”).
	•	Unique endpoint or separate permission role later (Phase 7).

⸻

5. Frontend UI Design

Note: This is a contract for what the UI should show, not a specific JS framework mandate.

5.1 Screens / Panels

5.1.1 Dashboard
Key widgets:
	•	Equity card:
	•	equity_usd, cash_usd, realized/unrealized PnL.
	•	Exposure card:
	•	Pie chart or bar chart: asset allocation, cash vs crypto.
	•	Risk status banner:
	•	Kill-switch state, daily drawdown %, drift flag.
	•	Red/amber/green states.
	•	Activity:
	•	List of last N executions (pair, side, size, status).
	•	Last N trades (pair, side, pnl, fee).

Data sources:
	•	/api/portfolio/summary
	•	/api/portfolio/exposure
	•	/api/risk/status
	•	/api/execution/recent_executions

⸻

5.1.2 Risk Management
Controls:
	•	Sliders/inputs for:
	•	max_risk_per_trade_pct
	•	max_portfolio_risk_pct
	•	max_open_positions
	•	max_per_asset_pct
	•	max_per_strategy_pct[<strategy>]
	•	Toggles:
	•	Kill switch on/off.
	•	include_manual_positions.

Display:
	•	“If all stops hit” estimate (Phase 4 can expose this).
	•	Current vs new values (before you hit Save).

Data sources:
	•	/api/risk/status
	•	/api/risk/config
	•	/api/strategies

Actions:
	•	PATCH /api/risk/config
	•	POST /api/risk/kill_switch

⸻

5.1.3 Strategies
For each strategy:
	•	Show:
	•	id, type, enabled, userref.
	•	Timeframes, max_positions, per-strategy risk budget.
	•	PnL summary, open positions count, last run timestamps.
	•	Controls:
	•	Enable/disable strategy.
	•	Adjust safe runtime parameters (e.g. max_positions, target allocation).

Data sources:
	•	/api/strategies
	•	/api/strategies/{id}/config

Actions:
	•	PATCH /api/strategies/{id}/enabled
	•	PATCH /api/strategies/{id}/config

⸻

5.1.4 Orders & Executions
Two tabs:
	1.	Open Orders:
	•	Table of open/partial orders with:
	•	Pair, side, size, price, status, strategy_id.
	•	Action buttons:
	•	Cancel (per order).
	•	Cancel all.
	2.	Execution History:
	•	Recent ExecutionResults:
	•	Per plan: timestamp, number of actions, success/errors.
	•	Drill-down: view LocalOrders for that plan.

Data sources:
	•	/api/execution/open_orders
	•	/api/execution/recent_executions

Actions:
	•	POST /api/execution/cancel/{local_id}
	•	POST /api/execution/cancel_all

⸻

5.1.5 Trades & PnL
	•	Trades table:
	•	pair, time, side, size, price, fee, PnL, strategy_tag.
	•	Filters:
	•	Date range,
	•	Pair,
	•	Strategy,
	•	Realized vs unrealized PnL.

Data source:
	•	/api/portfolio/trades

⸻

5.1.6 Settings
	•	Execution mode:
	•	Radio: live / paper / dry_run.
	•	UI options:
	•	Theme, refresh intervals.
	•	System info:
	•	App version, schema version, last restart time.

Data sources:
	•	/api/system/config
	•	/api/system/health

Actions:
	•	PATCH /api/system/execution_mode

⸻

6. Orchestration & Integration

6.1 Process model

Two typical deployment modes:
	1.	Single process:
	•	Main bot process runs:
	•	MarketDataAPI (WS loop),
	•	PortfolioService (sync loop),
	•	StrategyRiskEngine (decision cycles),
	•	ExecutionService (order handling),
	•	UI server.
Orchestration is done by a “main” component that:
	•	Schedules portfolio sync + strategy cycles (Phase 4),
	•	Calls ExecutionService for each ExecutionPlan,
	•	UI only introspects & triggers high-level actions.
	2.	Multi-process (future):
	•	UI may run as a separate process, calling the core bot over HTTP or RPC.
	•	Phase 6 only requires the API contract to be stable enough for that.

For Phase 6, assume single process.

6.2 Safe control paths

All mutating operations from UI go through:
	•	A small “controller/facade” that:
	•	Validates that the system is in a state where the action makes sense (e.g., don’t change execution mode if there’s an active live plan in progress, or require explicit confirmation).
	•	Logs actions with timestamps and the remote IP/user.

Examples:
	•	Flatten all:
	•	UI → /api/execution/flatten_all → controller:
	•	Asks StrategyEngine to generate an emergency flatten plan.
	•	Passes plan to ExecutionService.
	•	Kill switch:
	•	UI → /api/risk/kill_switch → StrategyRiskEngine updates its internal state.

⸻

7. Testing Expectations (Phase 6)

7.1 API-level tests

Using a test client (e.g. FastAPI TestClient):
	•	Auth:
	•	Verify requests without auth fail when ui.auth.enabled=true.
	•	Verify with correct token they succeed.
	•	Portfolio endpoints:
	•	With mocked PortfolioService, assert that /api/portfolio/summary, /positions, /exposure, /trades return expected data shapes.
	•	Risk endpoints:
	•	Mock StrategyRiskEngine:
	•	get_risk_status, get_risk_config.
	•	Verify PATCH updates and error handling on invalid values.
	•	Strategies endpoints:
	•	Mock get_strategy_state; ensure enable/disable & config updates hit the right methods.
	•	Execution endpoints:
	•	Mock ExecutionService; ensure cancellation and flatten endpoints call the correct methods.

7.2 Read-only mode tests
	•	With ui.read_only=true:
	•	All mutating endpoints (PATCH, POST for actions) must:
	•	Return 403 or a read_only error.
	•	GET endpoints remain available.

7.3 Basic UI integration tests

If you ship bundled static assets:
	•	Verify GET / serves the main HTML.
	•	Verify static JS/CSS are served under expected paths.
	•	Optionally, test that the UI hits expected API routes (smoke tests).

⸻

8. Phase 6 Acceptance Checklist

Phase 6 is complete when:
	•	ui package exists with:
	•	API server (api.py),
	•	route modules for portfolio, risk, strategies, execution, system,
	•	basic web assets (or a documented external frontend project).
	•	config.yaml includes a ui section and UIConfig is wired into AppConfig.
	•	Backend API exposes:
	•	/api/portfolio/* for summary, positions, exposure, trades, snapshots.
	•	/api/risk/* for risk status and config (plus kill switch).
	•	/api/strategies/* for strategy state, enable/disable, and runtime config tweaks.
	•	/api/execution/* for open orders, recent executions, cancel, cancel_all, flatten_all.
	•	/api/system/* for health, config, and execution mode.
	•	UI server integrates with:
	•	PortfolioService,
	•	StrategyRiskEngine,
	•	ExecutionService,
	•	MarketDataAPI,
without directly calling KrakenRESTClient.
	•	Auth & read-only:
	•	Auth token support is implemented when enabled.
	•	read_only mode blocks all mutating endpoints.
	•	A minimal but functional web UI exists:
	•	Dashboard view,
	•	Risk Management panel,
	•	Strategy view,
	•	Orders & executions view,
	•	Settings/system view.
	•	Test suite covers:
	•	API shape and basic behaviors,
	•	Auth and read-only behavior,
	•	Integration with mocked Phase 3–5 services.

At that point, Krakked has a real control plane: you can see everything that matters, tweak risk/strategy, execute emergency controls, and monitor the bot’s behavior — all without touching code or logs directly. Phase 7 can then focus on deployment, monitoring, and long-term ops.