Phase 5 – Execution & Order Management (OMS) Design Contract

1. Purpose & Scope

The Execution & OMS layer is responsible for:
	•	Turning Phase 4 ExecutionPlans into real Kraken orders.
	•	Managing the full order lifecycle:
	•	Submit → open → partial fill → filled → canceled → expired → error.
	•	Handling:
	•	Order type selection (market/limit, later stop/other advanced types),
	•	Partial fills and retries,
	•	Cancellations and safety mechanisms.
	•	Providing a clean API for higher layers (strategy/runner/UI) to:
	•	Execute a plan,
	•	Inspect open orders and recent executions,
	•	Access logs of what was attempted vs what actually happened.

It does not:
	•	Decide what to trade or how much – that’s Phase 4.
	•	Compute PnL or portfolio state – that’s Phase 3.
	•	Perform UI logic – that’s Phase 6.

Assumptions:
	•	Region profile US_CA – spot only, no margin/futures, no shorting.
	•	KrakenRESTClient (Phase 1) now supports:
	•	Public endpoints (Ticker, AssetPairs, OHLC).
	•	Private endpoints:
	•	AddOrder, CancelOrder, CancelAll, OpenOrders, ClosedOrders.
	•	Phase 3 portfolio already ingests trades & closed orders and rebuilds positions.
	•	Phase 4 Strategy/Risk Engine outputs ExecutionPlans containing RiskAdjustedActions, not raw orders.

⸻

2. Dependencies & Module Layout

2.1 Dependencies on previous phases

Phase 5 depends on:
	1.	Phase 1 – Connection & Region
	•	KrakenRESTClient with:
	•	Private signing, NonceGenerator, RateLimiter.
	•	Typed exceptions: KrakenAPIError, RateLimitError, AuthError, ServiceUnavailableError.
	•	RegionProfile:
	•	supports_margin=False, supports_futures=False.
	2.	Phase 2 – Market Data & Universe
	•	MarketDataAPI:
	•	get_pair_metadata(pair) for min order size, volume/price decimals, etc.
	•	get_latest_price(pair) for “sanity checks” (avoid insane slippage).
	3.	Phase 3 – Portfolio & PnL
	•	PortfolioService:
	•	get_positions(), get_equity(), get_asset_exposure().
	•	get_trade_history() / get_fee_summary() for reporting.
	•	The existing orders / trades / cash_flows persistence — Execution will add its own order records and then Phase 3 reads results via Kraken.
	4.	Phase 4 – Strategy & Risk Engine
	•	ExecutionPlan and RiskAdjustedAction models:
	•	Plan contains per-pair, per-strategy target positions and risk-check flags.
	•	StrategyRiskEngine or equivalent:
	•	Phase 5 treats its output as a desired portfolio state.

Phase 5 only talks to Kraken via KrakenRESTClient. It should not encode raw HTTP calls on its own.

2.2 Module layout

Create a new package:

src/kraken_bot/execution/
  __init__.py
  models.py          # ExecutionPlan mirror, LocalOrder, ExecutionResult, etc.
  oms.py             # Core Order Management System logic
  router.py          # Order type selection, price/size rounding
  adapter.py         # KrakenExecutionAdapter (wraps KrakenRESTClient)
  scheduler.py       # Optional orchestration helpers (batching, throttling)
  exceptions.py      # ExecutionError, OrderRejectedError, etc.

Integrations:
	•	strategy.engine (Phase 4) calls into execution.oms with an ExecutionPlan.
	•	UI / Phase 6 can call oms.get_open_orders(), oms.get_recent_executions(), etc.

⸻

3. Configuration (config.yaml)

Add an execution section:

execution:
  mode: "live"                    # "live" | "paper" | "dry_run"
  default_order_type: "limit"     # "market" | "limit" (Phase 5 focuses on these)
  max_slippage_bps: 50            # for limit order price offsets (0.5% default)
  time_in_force: "GTC"            # "GTC", "IOC", "GTD" (if supported later)
  post_only: false                # whether we prefer maker orders (if used later)
  validate_only: false            # if true, Kraken validate=1 (no real execution)

  dead_man_switch_seconds: 600    # use CancelAllOrdersAfterX, or 0 to disable

  max_retries: 3                  # per order in case of transient errors
  retry_backoff_seconds: 2        # base backoff time
  retry_backoff_factor: 2.0       # exponential backoff multiplier

  max_concurrent_orders: 10       # limit concurrency to avoid overload
  min_order_notional_usd: 20.0    # enforce reasonable minimum order size

Defaults if missing:
	•	mode = "paper" (safe default for dev).
	•	default_order_type = "limit".
	•	max_slippage_bps = 50.
	•	validate_only = true if mode != "live" unless explicitly overridden.

These are represented in a new ExecutionConfig dataclass and attached to AppConfig.

⸻

4. Core Concepts & Data Model

4.1 Local Order Representation

We need a local order model that tracks the entire lifecycle:

LocalOrder:
  local_id: str                   # internal UUID
  plan_id: str | null             # originating ExecutionPlan id
  strategy_id: str | null         # from RiskAdjustedAction
  pair: str                       # canonical pair, e.g. "XBTUSD"
  side: "buy" | "sell"
  order_type: "market" | "limit"
  kraken_order_id: str | null     # Kraken's "txid" once known
  userref: int | null             # for strategy tagging / Phase 3
  requested_base_size: float      # volume requested
  requested_price: float | null   # for limit orders
  status: "pending" | "submitted" | "open" | "partially_filled"
          | "filled" | "canceled" | "rejected" | "error"
  created_at: datetime (UTC)
  updated_at: datetime (UTC)

  cumulative_base_filled: float   # from Kraken trades
  avg_fill_price: float | null
  last_error: str | null          # last Kraken or internal error message

  raw_request: dict               # full AddOrder payload (for debug)
  raw_response: dict | null       # last Kraken response (for debug)

This is Execution-only state; Phase 3’s trades/positions remain the canonical portfolio truth.

4.2 Execution Result

For each ExecutionPlan, we want a synthetic summary:

ExecutionResult:
  plan_id: str
  started_at: datetime
  completed_at: datetime | null
  success: bool
  orders: list[LocalOrder]        # final state of each order
  errors: list[str]               # high-level errors, if any

Phase 4 and the UI can use this to see “what actually happened” vs what was intended.

4.3 OMS State

OMS maintains in-memory state for:
	•	open_orders – local snapshot of LocalOrder objects with status in open/partial.
	•	recent_executions – ring buffer of most recent ExecutionResults.
	•	A mapping from kraken_order_id → local_id for quick reconciliation.

Persistence:
	•	The SQLite DB may store orders & executions as tables:
	•	execution_orders (LocalOrder snapshots).
	•	execution_results (per-plan summary).
	•	Phase 5 should write to these tables but we keep them conceptually separate from portfolio’s orders/trades (which are derived from Kraken data, not local).

⸻

5. Kraken Execution Adapter

5.1 Responsibilities

KrakenExecutionAdapter (in adapter.py) is the only component that:
	•	Calls KrakenRESTClient private trading endpoints:
	•	AddOrder
	•	CancelOrder
	•	CancelAllOrder[s]
	•	(Optionally) CancelAllOrdersAfterX for dead-man switch.
	•	Translates between:
	•	LocalOrder ↔ Kraken request/response structs.
	•	ExecutionConfig (validate_only, order_type, time_in_force) ↔ Kraken params.

5.2 AddOrder mapping

For each LocalOrder, KrakenExecutionAdapter prepares an AddOrder payload:

pair      => LocalOrder.pair (Kraken’s pair naming, using PairMetadata.rest_symbol)
type      => "buy" | "sell"
ordertype => "market" | "limit"
volume    => formatted base size (respecting lot decimals)
price     => for limit orders only, respecting price decimals
userref   => userref from StrategyConfig, if provided
validate  => 1 if ExecutionConfig.validate_only or mode != "live"

Adapter is responsible for:
	•	Rounding size/price according to PairMetadata:
	•	Volume: truncate/round to volume_decimals.
	•	Price: truncate/round to price_decimals.
	•	Enforcing ExecutionConfig.min_order_notional_usd by comparing:
	•	volume * price vs min_order_notional_usd (for limit), or
	•	Using MarketDataAPI.get_latest_price as fallback for market orders.

If a LocalOrder violates these constraints:
	•	Mark it as status="error".
	•	Do not send to Kraken.
	•	Add a clear last_error message.

5.3 Cancel and dead-man switch

KrakenExecutionAdapter should support:
	•	cancel_order(kraken_order_id):
	•	Calls CancelOrder, maps results, updates LocalOrder status to canceled if success.
	•	cancel_all():
	•	Calls CancelAllOrders.
	•	set_dead_man_switch(seconds):
	•	Uses the Kraken “Cancel all orders after X” endpoint (if enabled in ExecutionConfig).
	•	Called periodically by OMS (e.g., every 1–2 minutes) to refresh the deadline.

All of these operations must:
	•	Respect RateLimiter in KrakenRESTClient.
	•	Translate Kraken errors into typed exceptions.

⸻

6. OMS Logic

6.1 From ExecutionPlan to orders

For each RiskAdjustedAction in the ExecutionPlan:
	•	If action_type == "none" or blocked=True → ignore (log as “no-op”).
	•	Else:
	•	Compute delta between current_base_size and target_base_size:
	•	If target > current → need to buy difference.
	•	If target < current → need to sell difference.
	•	If delta is below min lot size or too close to zero → skip.

OMS then:
	1.	Chooses order type via router.py:
	•	E.g. ExecutionConfig.default_order_type = limit:
	•	For buys: limit price at max_price = current_mid * (1 + max_slippage_bps/10_000).
	•	For sells: limit price at min_price = current_mid * (1 - max_slippage_bps/10_000).
	•	Or uses market orders (simpler) for high-liquidity pairs and/or small sizes.
	2.	Builds a LocalOrder with:
	•	local_id, plan_id, strategy_id, pair, side, order_type, requested_base_size, requested_price, userref.
	3.	Passes it to KrakenExecutionAdapter.add_order() to send to Kraken.

6.2 Partial fills & reconciliation

OMS must handle:
	•	Immediate response:
	•	Kraken AddOrder returns:
	•	txid: new order ID.
	•	Possibly partial fill info (depending on API details).
	•	Update LocalOrder:
	•	kraken_order_id set.
	•	status="submitted" or open/partially_filled based on response.
	•	Ongoing fills:
	•	OMS should periodically reconcile:
	•	OpenOrders and/or ClosedOrders to update:
	•	cumulative_base_filled,
	•	avg_fill_price,
	•	status (filled / canceled / expired).
	•	This can be:
	•	A separate reconciliation loop in Phase 5, or
	•	Left to Phase 3’s sync + some helper functions in OMS to refresh LocalOrder from portfolio’s view.
	•	For Phase 5, the minimum requirement:
	•	After an ExecutionPlan is executed, ExecutionResult has an accurate view of which orders ended up fully filled, partially filled, or not filled at all.

Partial fill policy:
	•	For Phase 5:
	•	Treat partial fills as “okay”:
	•	Let them stand.
	•	Don’t auto-replace with new orders unless a future ExecutionPlan calls for further adjustment.
	•	Position sizing logic in Phase 4 should treat current holdings (from Portfolio) as truth and ask for additional adjustments.

6.3 Error handling & retries

For each LocalOrder submission:
	•	Distinguish between:
	•	Transient issues:
	•	Network errors,
	•	ServiceUnavailableError,
	•	RateLimitError.
	•	Permanent / logical issues:
	•	AuthError,
	•	KrakenAPIError with invalid params (e.g. “EOrder:Insufficient funds”).

Retry policy:
	•	For transient errors:
	•	Retry up to ExecutionConfig.max_retries with exponential backoff:
	•	Sleep retry_backoff_seconds * retry_backoff_factor^attempt.
	•	After exhausting retries:
	•	Mark LocalOrder as status="error".
	•	For permanent errors:
	•	Mark LocalOrder as status="rejected".
	•	Do not retry.

OMS should ensure that idempotence is maintained:
	•	If an order submission’s status is unknown (network error after sending), OMS should:
	•	Check OpenOrders for a matching userref or client-supplied ID where possible, or
	•	Avoid blindly resubmitting orders without a check to prevent duplicates.

6.4 Concurrency & rate limits

OMS must respect:
	•	ExecutionConfig.max_concurrent_orders:
	•	Limit the number of simultaneous AddOrder calls in flight.
	•	KrakenRESTClient’s RateLimiter:
	•	All execution calls use the same rate limiter as other private requests.

It’s acceptable for Phase 5 to sequence orders sequentially per plan as a starting point (one after another), while obeying the rate limit.

⸻

7. Execution Modes: live, paper, dry run

Execution behavior depends on execution.mode and execution.validate_only:
	1.	live:
	•	Real AddOrder calls with validate=0.
	•	Orders are actually placed.
	•	Dead-man switch recommended if configured (dead_man_switch_seconds > 0).
	2.	paper:
	•	Two sub-options:
	•	validate_only=true:
	•	Kraken validates orders without executing.
	•	OMS records them as “simulated executed” with no actual fills.
	•	Or full internal simulation:
	•	OMS can simulate fills based on market data, but Phase 5 doesn’t have to implement that in v1.
	3.	dry_run:
	•	No calls to Kraken at all.
	•	OMS just logs what it would do and returns an ExecutionResult with success=True, but kraken_order_id=None.

Phase 5 v1 requirement:
	•	Support live and paper (validate-only) modes.
	•	dry_run can be implemented as a simple short-circuit in OMS (no adapter calls).

⸻

8. Public API of the Execution/OMS Layer

Expose a main façade, e.g. ExecutionService or OrderManagementService in oms.py:

8.1 Lifecycle
	•	initialize():
	•	Load ExecutionConfig.
	•	Create KrakenExecutionAdapter and link to KrakenRESTClient.
	•	Connect to persistence backend (SQLite) and ensure execution tables exist.
	•	Optionally set the initial dead-man switch if configured.

8.2 Core methods
	•	execute_plan(plan: ExecutionPlan) -> ExecutionResult:
	•	Takes a Phase-4 ExecutionPlan,
	•	For each RiskAdjustedAction:
	•	Compute deltas,
	•	Build LocalOrders,
	•	Route & send to Kraken or simulate (depending on mode),
	•	Block until all immediate responses are received,
	•	Optionally run a short reconciliation pass (e.g. check OpenOrders),
	•	Return ExecutionResult.
	•	get_open_orders() -> list[LocalOrder]:
	•	Return in-memory or DB-sourced view of open/partially filled orders.
	•	get_recent_executions(limit: int = 50) -> list[ExecutionResult]:
	•	Return recent ExecutionResults.
	•	cancel_order(local_id: str) -> LocalOrder:
	•	Cancel a specific LocalOrder by local ID (or kraken_order_id),
	•	Updates and returns its final state.
	•	cancel_all() -> None:
	•	Invokes KrakenExecutionAdapter.cancel_all() and updates local open orders.
	•	refresh_from_exchange() -> None:
	•	Re-sync local open_orders with Kraken’s OpenOrders and ClosedOrders,
	•	Intended to reconcile state in long-running processes or after restarts.

⸻

9. Persistence & Schema

Extend the existing SQLite DB with two new tables (v4 schema bump):

9.1 execution_orders table

Fields (minimal v1):

CREATE TABLE IF NOT EXISTS execution_orders (
    local_id TEXT PRIMARY KEY,
    plan_id TEXT,
    strategy_id TEXT,
    pair TEXT NOT NULL,
    side TEXT NOT NULL,
    order_type TEXT NOT NULL,
    kraken_order_id TEXT,
    userref INTEGER,
    requested_base_size REAL NOT NULL,
    requested_price REAL,
    status TEXT NOT NULL,
    cumulative_base_filled REAL NOT NULL DEFAULT 0.0,
    avg_fill_price REAL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    last_error TEXT,
    raw_request TEXT,
    raw_response TEXT
);

9.2 execution_results table

CREATE TABLE IF NOT EXISTS execution_results (
    plan_id TEXT PRIMARY KEY,
    started_at INTEGER NOT NULL,
    completed_at INTEGER,
    success INTEGER NOT NULL,
    error_summary TEXT,
    raw_json TEXT
);

Note:
	•	raw_json stores serialized ExecutionResult and/or list of LocalOrders for debug.
	•	Higher layers (Phase 6/7) can read these for logs/analytics.

Schema versioning:
	•	Increment schema_version.version to indicate Phase 5 schema has been applied.
	•	On initialization, ExecutionService ensures tables/pages are present; any missing columns are handled via simple ALTER/CREATE operations.

⸻

10. Testing Expectations (pytest)

10.1 Kraken adapter tests

With a mocked KrakenRESTClient:
	•	Verify AddOrder mapping:
	•	Volume and price are rounded to expected decimals.
	•	userref, pair, type, ordertype, validate are correctly passed.
	•	Verify error mapping:
	•	When Kraken returns an error payload, the adapter raises the right exception class.

10.2 OMS plan execution tests

Using mocks for KrakenExecutionAdapter:
	•	Given an ExecutionPlan with:
	•	One RiskAdjustedAction that increases a position,
	•	Ensure execute_plan:
	•	Creates one LocalOrder with correct pair, side, and volume.
	•	Calls adapter exactly once.
	•	Persists the order and result to SQLite.
	•	Returns ExecutionResult with success=True.
	•	Given an ExecutionPlan with multiple pairs:
	•	Check that orders for each pair are created and sized correctly based on current_base_size and target_base_size.

10.3 Retry & error handling tests
	•	Simulate a transient error on the first call to AddOrder and success on the second:
	•	Verify that OMS retries according to max_retries and backoff.
	•	Simulate a permanent error (OrderRejectedError):
	•	Verify that the order is marked rejected and no retries are attempted.

10.4 Mode behavior tests
	•	With mode="dry_run":
	•	Ensure no calls to KrakenRESTClient are made.
	•	ExecutionResult shows orders as “simulated” and kraken_order_id=None.
	•	With execution.validate_only=true:
	•	Ensure AddOrder payload includes validate=1 and LocalOrder status reflects “submitted/validated” without expecting real fills.

10.5 Cancel and refresh tests
	•	cancel_order:
	•	When Kraken returns success, LocalOrder becomes canceled.
	•	cancel_all:
	•	After calling, all open LocalOrders should be marked canceled (or flagged for reconciliation).
	•	refresh_from_exchange:
	•	With mock OpenOrders/ClosedOrders, ensure local open_orders set is updated correctly.

⸻

11. Phase 5 Acceptance Checklist

Phase 5 is done when:
	•	execution package exists with:
	•	ExecutionConfig,
	•	LocalOrder, ExecutionResult models,
	•	KrakenExecutionAdapter,
	•	ExecutionService / OMS implementation.
	•	execute_plan(plan):
	•	Accepts a Phase 4 ExecutionPlan,
	•	Generates correct LocalOrders based on deltas,
	•	Sends appropriate AddOrder calls to Kraken (or simulates per mode),
	•	Returns ExecutionResult summarizing what happened.
	•	Orders are persisted to execution_orders, and plan results to execution_results.
	•	No Python lists/dicts are passed directly into SQLite parameters; list-like fields go to normalized columns or JSON TEXT.
	•	Partial fills are handled gracefully (no repeated orders for the same target unless a new plan asks for them).
	•	Error handling & retry policy:
	•	Retries transient errors up to configured limits,
	•	Correctly marks orders as rejected or error for permanent failures.
	•	Cancel and dead-man switch:
	•	cancel_order and cancel_all work and update local state.
	•	If configured, dead-man switch is periodically refreshed.
	•	ExecutionService exposes:
	•	execute_plan, get_open_orders, get_recent_executions, cancel_order, cancel_all, refresh_from_exchange.
	•	Test suite covers:
	•	Adapter mapping,
	•	OMS execution for simple and multi-pair plans,
	•	Retry/error behavior,
	•	Mode switching (live, paper, dry_run),
	•	SQLite persistence integrity.

⸻

12. Quickstart (dry run)

        •       Run a single plan cycle safely via the CLI:
                •       `poetry run krakked run-once`
                •       Forces execution.mode="paper", validate_only=True, allow_live_trading=False regardless of config.
                •       Uses the default SQLite store `portfolio.db` unless a different path is provided to the portfolio store.
        •       Inspect artifacts in SQLite after the run:
                •       `execution_orders` captures each LocalOrder snapshot (requested sizes, status, and any guardrail errors).
                •       `execution_results` summarizes the plan run (success flag plus errors_json).

13. Live-mode readiness checklist

        •       Require explicit opt-in before routing real orders:
                •       Set execution.mode: "live" and execution.allow_live_trading: true (default is false).
                •       Set execution.validate_only: false so Kraken accepts live submissions (default is true for safety).
        •       Keep safety floors in place:
                •       min_order_notional_usd stays at ≥20.0 by default; tighten max_pair_notional_usd/max_total_notional_usd as needed.
        •       Validate in paper first:
                •       Run `krakked run-once` in paper/validate-only mode and review execution_orders/execution_results for sizing/tagging.
        •       Confirm persistence/reconciliation:
                •       Ensure portfolio.db (or your configured DB path) is writable so orders and results are stored for later reconciliation.

With this, Phase 5 gives Krakked a real OMS: Phase 4 decides what to do, Phase 5 ensures it’s done safely and consistently on Kraken — and that you can always look back and understand what the bot actually tried to do.
