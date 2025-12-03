Phase 7 – Deployment, Monitoring & Operations Design Contract

1. Purpose & Scope

The Deployment, Monitoring & Ops layer is responsible for:
	•	Running the bot reliably and safely in long‑lived environments.
	•	Providing observability:
	•	Logs,
	•	Metrics,
	•	Health checks,
	•	Alerts (even if initially just logs/CLI).
	•	Managing:
	•	Deployments & upgrades (schema migrations, strategy changes),
	•	Backups & data retention,
	•	Runtime safety (kill switches, dead‑man, failure modes).

It does not:
	•	Change core trading logic (Phases 2–5).
	•	Implement new strategies (Phase 4).
	•	Implement new UI views (Phase 6).

Instead, Phase 7 wraps everything into something you can actually operate.

Ops quickstart: follow the CI gates in [`.github/workflows/ci.yml`](.github/workflows/ci.yml) (pytest, flake8, mypy, pyright, wheel build) before promoting any change, and prefer the documented `poetry install --with dev --extras tui` / `poetry run krakked ...` flows for local verification. Container builds stay reproducible via `docker build -t krakked .` and `docker run -p 8080:8080 krakked` to mirror production wiring.

⸻

2. Environments & Deployment Targets

2.1 Environments

Define at least three modes:
	1.	Dev:
	•	Running locally (laptop).
	•	execution.mode = "dry_run" or "paper" + validate_only=true.
	•	UI enabled, auth often disabled (127.0.0.1 only).
	2.	Paper:
	•	Runs on a server/VPS.
	•	execution.mode = "paper":
	•	Real market data.
	•	AddOrder with validate=1 or in‑memory simulation.
	•	All other subsystems (portfolio, strategy, risk, OMS, UI) fully active.
	3.	Live:
	•	Same as paper but:
	•	execution.mode = "live".
	•	validate_only=false.
	•	Dead‑man switch active.
	•	UI auth enabled.
	•	Stronger monitoring & backups.

Each environment may have its own config.<env>.yaml overlay.

2.2 Deployment style

For now, assume:
	•	Single host deployment:
	•	One Python process running:
	•	MarketDataAPI (WS loop),
	•	PortfolioService sync,
	•	StrategyRiskEngine cycles,
	•	ExecutionService,
	•	UI API server (and static frontend).
	•	Optionally wrapped in:
	•	A systemd service, or
	•	A Docker container with a simple docker-compose file.

Phase 7 does not require full K8s, but we design the process so containerization is trivial:
	•	All config via env vars + YAML.
	•	No hard-coded absolute paths.

⸻

3. Main Orchestrator Process

3.1 Orchestrator responsibilities

Introduce a single entrypoint (e.g. kraken_bot.main) that:
	1.	Loads AppConfig (Phase 1) including:
	•	Region,
	•	MarketData,
	•	Portfolio,
	•	Risk/Strategies,
	•	Execution,
	•	UI.
	2.	Initializes:
	•	KrakenRESTClient,
	•	MarketDataAPI,
	•	PortfolioService,
	•	StrategyRiskEngine,
	•	ExecutionService,
	•	UIServer (Phase 6 API).
	3.	Starts runtime loops:
	•	Market data: WS client already runs in its own thread/loop (Phase 2).
	•	Portfolio sync: periodic task (e.g. every N seconds).
	•	Strategy cycles: triggered on closed candles or fixed intervals.
	•	Execution: invoked when StrategyEngine returns a new ExecutionPlan.
	4.	Handles graceful shutdown:
	•	On SIGINT/SIGTERM:
	•	Stop accepting new plans,
	•	Let in‑flight executions finish or cancel them,
	•	Flush state and close DB.

3.2 Scheduling model

For Phase 7, scheduling can be simple:
	•	A small scheduler loop in the orchestrator:

while running:
    now = utc_now()

    # 1. Portfolio sync
    if now - last_portfolio_sync >= portfolio_interval:
        portfolio.sync()
        last_portfolio_sync = now

    # 2. Strategy cycle
    # for each timeframe, if a candle boundary passed → run cycle
    if time_to_run_strategy_cycle(now):
        plan = strategy_engine.run_cycle(now)
        if plan.actions:
            exec_result = execution_service.execute_plan(plan)
            # log result

    # 3. UI server runs in its own thread/loop

    sleep(loop_interval)



Phase 7 should:
	•	Document the scheduling logic clearly.
	•	Optionally provide a SchedulerConfig for intervals (portfolio_sync_sec, strategy_loop_sec).

⸻

4. Logging & Observability

4.1 Logging

Adopt structured logging across the app:
	•	Format: JSON or key‑value logs to stdout.
	•	Levels:
	•	DEBUG for dev,
	•	INFO for normal ops,
	•	WARNING for unusual but non‑fatal conditions,
	•	ERROR for failures that need attention,
	•	CRITICAL for kill switch triggers, fatal conditions.

Log context should include:
	•	plan_id for each ExecutionPlan.
	•	local_order_id and kraken_order_id for orders.
	•	strategy_id and userref where relevant.
	•	env (dev, paper, live).

Examples:
	•	Strategy decision cycle:
	•	INFO – {"event": "strategy_cycle", "timeframe": "1h", "strategies": 2, "intents": 5}
	•	Risk block:
	•	WARNING – {"event": "risk_block", "pair": "XBTUSD", "strategy_id": "trend_v1", "reason": "max_per_asset_pct"}
	•	Execution error:
	•	ERROR – {"event": "order_error", "local_id": "uuid", "kraken_error": "EOrder:Insufficient funds"}
	•	Kill switch:
	•	CRITICAL – {"event": "kill_switch_activated", "reason": "daily_drawdown_exceeded"}

Phase 7 must ensure:
	•	A central logging configuration module,
	•	All subsystems (phases 2–6) log through the same logger.

4.2 Metrics (Phase 7 baseline)

Implement a simple metrics subsystem:
	•	Internal counters/gauges exposed via:
	•	/api/system/metrics (basic),
	•	Or a Prometheus‑style endpoint (/metrics) if you want to integrate later.

Suggested metrics:
	•	Health metrics:
	•	market_data_stale_pairs_count,
	•	portfolio_drift_flag (0/1),
	•	kill_switch_active (0/1).
	•	Trading metrics:
	•	Number of ExecutionPlans processed per hour.
	•	Number of LocalOrders created per hour.
	•	Count of order_error and order_rejected.
	•	PnL metrics:
	•	Current equity,
	•	Daily realized PnL,
	•	Daily drawdown.
	•	Latency metrics:
	•	Time from strategy intent to order submit,
	•	Time for ExecutionPlan processing.

Phase 7:
	•	Doesn’t require plugging into Prometheus yet, but must:
	•	Expose metrics in a way that’s easy to hook up later,
	•	Or produce them as periodic JSON log lines.

⸻

5. Failure Modes & Safety

5.1 Startup safety checks

On startup, orchestrator should:
	1.	Validate config consistency:
	•	Unknown strategies,
	•	Overlapping risk limits,
	•	Invalid execution modes (e.g. live mode with missing secrets).
	2.	Run basic smoke checks:
	•	Can reach Kraken public endpoints?
	•	Can authenticate with private endpoints?
	•	Can load SQLite DB and schema?
	3.	If any fatal check fails:
	•	Log CRITICAL and exit.

Optional: in live mode, require a confirmation file/flag (e.g. live_mode_enabled = true in config) to avoid accidental live runs.

5.2 Runtime failures

Define behavior for:
	1.	Market data outages:
	•	If MarketDataAPI.get_data_status() shows stale data:
	•	StrategyEngine should not generate new ExecutionPlans.
	•	Execution must not be invoked.
	•	Logging should indicate market_data_unavailable.
	2.	Portfolio drift:
	•	If PortfolioService.get_equity().drift_flag=True:
	•	If risk.kill_switch_on_drift=True, StrategyEngine blocks new risk as per Phase 4.
	•	Phase 7 logs and surfaces a “DRIFT” warning in UI.
	3.	Kraken API failures:
	•	System must:
	•	Retry transient failures,
	•	Back off if rate limited,
	•	Log and alert on repeated failures.
	4.	Dead-man switch:
	•	If configured:
	•	ExecutionService must periodically refresh the deadline.
	•	If the process shuts down unexpectedly, the exchange will cancel open orders.
	5.	Kill switch:
	•	Triggered by:
	•	Daily drawdown limit,
	•	Manual UI action,
	•	Drift or fatal internal inconsistency.
	•	Behavior:
	•	Block new open/increase actions.
	•	Optionally allow reduce/close.
	•	Clearly log and surface in UI until explicitly cleared.

5.3 Graceful shutdown

On SIGINT/SIGTERM or manual UI “shutdown”:
	1.	Stop strategy cycles (no new plans).
	2.	Optionally:
	•	Cancel all open orders, or
	•	Leave them in place and rely on dead-man switch (configurable).
	3.	Flush in-memory state to DB.
	4.	Shut down UI server and WS client.

⸻

6. Data Management & Backups

6.1 DB location & rotation

SQLite DB:
        •       Default path today is the working-directory file portfolio.db (shared by SQLitePortfolioStore and the orchestrator when no override is provided).
        •       All CLI helpers accept --db-path to point at an alternate SQLite file; the default matches the in-code default (portfolio.db).
        •       Moving the database into a config dir (e.g. ~/.krakked/krakked.db) will require changes to both the code and this document when that refactor lands.
        •       Phase 7 must:
        •       Document where it is,
        •       Provide a simple maintenance script / doc for:
        •       Viewing tables,
        •       Checking integrity (PRAGMA integrity_check).

6.2 Running in production: DB maintenance commands

Standard CLI tools (all accept --db-path with the default portfolio.db):
        •       krakked db-info:
        •       Prints the resolved DB path, stored schema version (or unknown), and row counts for key tables.
        •       Exit codes: 0 on success; non-zero on errors so automation can alert.
        •       krakked db-check:
        •       Runs PRAGMA integrity_check against the SQLite file and echoes the result.
        •       Exit codes: 0 when integrity_check returns ok; non-zero otherwise.
        •       krakked db-backup:
        •       Copies the DB to a timestamped sibling file using the pattern <db-name>.YYYYMMDDHHMM.bak (e.g. portfolio.db.202401011230.bak).
        •       --keep prunes old backups, retaining only the N most recent matching files. Example: --keep 7 keeps the seven newest backups and deletes older ones.
        •       Exit codes: 0 when the backup/prune completes; non-zero if copy or cleanup fails.
        •       krakked migrate-db:
        •       Runs portfolio schema migrations to the current version and ensures required tables exist.
        •       Exit codes: 0 on a completed migration; non-zero if the schema is incompatible or migration fails.
        •       krakked db-schema-version:
        •       Ensures metadata exists and reports the stored schema version value.
        •       Exit codes: 0 on success (including the case where the schema_version row is missing); non-zero on read errors.

6.3 Backups

For live/paper environments:
        •       Daily backup of the DB file recommended:
        •       E.g. copy portfolio.db to portfolio.db.YYYYMMDD.bak.
        •       Optionally compress and rotate:
        •       Keep last N backups (7/30).

Phase 7 defines:
        •       A small backup_db() utility (Python function/CLI entrypoint),
        •       Or a documented pattern for external cron to call a script.

6.4 Log retention
        •       Logs can be:
        •       Rotated by logrotate or a similar system,
        •       Or written to daily log files krakked-YYYYMMDD.log.

Phase 7 should:
        •       Ensure logs are not kept unbounded.
        •       Document recommended rotation (e.g. 100MB per file, 7 days retained).

⸻

7. Schema Migration & Versioning

7.1 Schema versioning

By Phase 5, you already have:
	•	schema_version table and version integer.
	•	Tables:
	•	Portfolio (trades, orders, cash_flows, snapshots),
	•	Execution (execution_orders, execution_results),
	•	Possibly decisions, etc.

Phase 7 formalizes:
	•	Versioned migrations:
	•	On startup, orchestrator:
	•	Reads schema_version.
	•	Applies upgrade steps if version < CURRENT_SCHEMA_VERSION.
	•	Each step is idempotent and safe.

If a migration fails:
	•	Log CRITICAL.
	•	Exit, with instructions to restore backup or fix manually.

7.2 App version & compatibility

Introduce an app version (semver):
	•	e.g. 0.4.0 for Phase 4, 0.5.0 for Phase 5, 0.7.0 for Phase 7, etc.

Expose it via:
	•	/api/system/health and logs.

Phase 7 ensures:
	•	DB schema version and app version are compatible (e.g. app refuses to run with a newer schema it doesn’t know).

⸻

8. CI/CD & Testing Pipeline

8.1 CI pipeline

Set up a basic CI (GitHub Actions or similar) pipeline with steps:
	1.	Lint:
	•	ruff / flake8 / black --check / isort --check-only (choose your combo).
	2.	Type checking:
	•	mypy (or Pyright if you prefer).
	3.	Unit tests:
	•	pytest with coverage report.
	4.	Integration tests (offline):
	•	Run simulated cycles with:
	•	Mocked KrakenRESTClient,
	•	Real MarketDataAPI with local static files or recorded OHLC,
	•	PortfolioService, StrategyEngine, ExecutionService all wired, but:
	•	execution.mode = "dry_run".

Phase 7:
	•	Defines a Makefile or poetry scripts:
	•	make test, make lint, make ci.
	•	Optionally enforces a minimum coverage threshold.

8.2 Release artifacts

If you want to ship containers:
	•	Provide a Dockerfile that:
	•	Installs dependencies,
	•	Copies the code,
	•	Exposes the UI port,
	•	Uses kraken_bot.main as the entrypoint.

Otherwise, a poetry build package is enough for pip‑style installs.

⸻

9. Simulation & Backtesting (Phase 7+)

While not strictly required for “live ops”, Phase 7 should acknowledge and prepare for:
	•	A “simulation mode” that:
	•	Replaces MarketDataAPI real-time data with:
	•	Historical OHLC from ohlc_store or external sources.
	•	Replaces ExecutionService with:
	•	A simulated fill engine (no Kraken calls).
	•	A common interface that lets you run:
	•	A “backtest run” over historical data,
	•	Using the same strategies, risk engine, and portfolio logic.

Phase 7 scope:
	•	Define a SimulationConfig and a SimulationRunner shell (no need to fully implement it yet),
	•	Ensure production code:
	•	Can swap out market_data and execution components via dependency injection.

⸻

10. Security & Access Control

10.1 Secrets

Phase 1/3 already have encrypted secrets and/or env vars.

Phase 7 must ensure:
	•	No secrets logged,
	•	DB and config directories have restrictive permissions (700 / 600),
	•	UI server only listens on:
	•	127.0.0.1 by default in dev,
	•	Configurable interface in paper/live, with auth required.

10.2 UI/API auth

Minimal security model:
	•	If ui.auth.enabled=true:
	•	Require Authorization: Bearer <token> for all endpoints except /health.
	•	Tokens read from config or env vars.
	•	In live mode:
	•	Strongly recommend auth.enabled=true.
	•	Document that you should proxy through HTTPS (nginx, Caddy, etc.) if exposed publicly.

10.3 Dangerous operations

Actions like:
	•	flatten_all,
	•	cancel_all,
	•	switch mode to live,
	•	kill_switch off

Should:
	•	Require explicit confirmation in the UI,
	•	Be logged with IP and timestamp.

⸻

11. Phase 7 Acceptance Checklist

Phase 7 is complete when:
	•	There is a single orchestrator entrypoint (kraken_bot.main) that:
	•	Initializes all components (Phases 1–6),
	•	Runs scheduled loops for portfolio sync & strategy cycles,
	•	Integrates ExecutionService and UI server,
	•	Handles graceful shutdown.
	•	Logging is centralized, structured, and includes identifying fields (plan_id, local_order_id, strategy_id, env).
	•	Basic metrics are exposed (via HTTP or logs) for:
	•	Health,
	•	PnL,
	•	Orders,
	•	Errors.
	•	Startup includes config and connectivity checks; fatal misconfigurations abort the run.
	•	Failure modes are defined and implemented:
	•	Market data outage → no new plans,
	•	Drift + kill_switch_on_drift → no new risk,
	•	API failures → retries & backoff, not infinite loops.
	•	SQLite DB backups and log rotation are documented and (ideally) automated via a simple script or instructions.
	•	Schema versioning:
	•	schema_version is checked at startup,
	•	Migrations run when necessary,
	•	App refuses to run with incompatible schema.
	•	CI pipeline exists and runs on pushes/PRs:
	•	Lint,
	•	Type checks,
	•	Unit tests,
	•	Basic offline integration tests.
	•	UI API from Phase 6:
	•	Auth is enforced when configured,
	•	read_only mode works and blocks all mutations.
	•	Live mode safety:
	•	Requires explicit configuration flag,
	•	Kill switch can be activated manually and via risk logic,
	•	Dead-man switch is supported and wired to ExecutionService when configured.

At that point, Krakked isn’t just “a fancy script” — it’s a deployable trading system with real ops, safety rails, and a path to long-term maintenance and iteration.

Status & TODO

- [x] Orchestrator entrypoint: Single `kraken_bot.main` bootstrap initializes all services and coordinates scheduling/shutdown.
- [x] Centralized logging: Structured logging with consistent fields (plan_id, strategy_id, env) and startup diagnostics emitted at launch.
- [x] Metrics endpoint: Basic health/metrics HTTP surface for runtime checks and liveness probing.
- [x] Schema guard: Enforce schema_version checks and migrations before the bot starts accepting work.
- [ ] CI pipeline: Wire lint, type checks, unit tests, and offline integration tests into a reproducible pipeline.
