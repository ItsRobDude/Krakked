## Phase 4 – Strategy & Risk Engine Design Contract

### 1. Purpose & Scope

The **Strategy & Risk Engine** is responsible for:

* Turning **market data + portfolio state** into **trading decisions**.
* Providing a **pluggable strategy framework** (multiple strategies, each with its own config).
* Enforcing **risk limits** on all strategy output:

  * Per-trade risk,
  * Aggregate exposure,
  * Per-asset / per-strategy limits,
  * Drawdown/kill-switch behavior.
* Producing a clean, explicit **action plan** that the Execution/OMS module (Phase 5) can translate into orders.

It is **not** responsible for:

* Talking to Kraken directly (no HTTP/WebSocket calls).
* Managing balances/PnL (that’s Phase 3).
* Actually placing or canceling orders (that’s Phase 5).
* UI rendering (Phase 6), though it must expose all info the UI will need.

Assumptions:

* Phase 1–3 are implemented:

  * `KrakenRESTClient` (public + private), region profile, secrets.
  * `MarketDataAPI` for universe + OHLC + latest prices.
  * `PortfolioService` for balances, PnL, positions, snapshots, drift detection, cash flows.
* Spot only (`RegionProfile.code="US_CA"`, no margin/futures).
* Strategies run on **closed candles** / discrete decision cycles, not every tick.

---

## 2. Dependencies & Configuration

### 2.1 Dependencies on previous phases

Phase 4 depends on:

1. **Phase 1 – Connection & Region**

   * `RegionProfile` for:

     * `code="US_CA"`
     * `supports_margin=False`
     * `supports_futures=False`
   * Risk engine must **respect region capabilities**:

     * No shorting or leverage logic for US_CA.

2. **Phase 2 – Market Data & Universe**

   * `MarketDataAPI`:

     * `get_universe()` / `get_pair_metadata(pair)`
     * `get_latest_price(pair)` – returns mid or last consistently.
     * `get_ohlc(pair, timeframe, lookback)` – closed candles only.
     * `get_data_status()` – for sanity checks (no strategy decisions if data is stale).

3. **Phase 3 – Portfolio & PnL Engine**

   * `PortfolioService`:

     * `initialize()` / `sync()`
     * `get_equity()` – including `equity_base`, `drift_flag`, realized/unrealized PnL.
     * `get_positions()` / `get_position(pair)`
     * `get_asset_exposure()`
     * `get_trade_history()` / `get_fee_summary()` / `get_cash_flows()` for reporting.
   * `RealizedPnLRecord.strategy_tag` and `raw_userref` for PnL attribution (strategy vs manual).

Phase 4 **only reads** from these modules; it does not mutate their internal state except by requesting snapshots/syncs.

### 2.2 Config additions (`config.yaml`)

Extend `config.yaml` with `risk` and `strategies` sections:

```yaml
risk:
  max_risk_per_trade_pct: 1.0       # % of equity at risk per trade (sizing)
  max_portfolio_risk_pct: 10.0      # max aggregate risk across all open positions
  max_open_positions: 10            # hard cap on number of simultaneously open pairs
  max_per_asset_pct: 5.0            # max % of equity in any single asset
  max_per_strategy_pct:
    trend_following_v1: 40.0
    mean_reversion_v1: 30.0

  max_daily_drawdown_pct: 10.0      # drawdown threshold to trigger kill switch
  kill_switch_on_drift: true        # stop new trades if portfolio drift_flag is set
  include_manual_positions: true    # whether manual positions count against risk limits

  volatility_lookback_bars: 20      # for ATR/range-based sizing
  min_liquidity_24h_usd: 100000.0   # minimum 24h volume per pair to allow trading

strategies:
  enabled:
    - "trend_following_v1"
  configs:
    trend_following_v1:
      type: "trend_following"
      timeframes: ["1h", "4h"]
      ma_fast: 20
      ma_slow: 50
      universe_filter: "default"    # use Phase 2 universe
      max_positions: 6
      per_trade_target_r_multiple: 2.0  # optional, for risk:reward tuning
      min_signal_confidence: 0.6
    mean_reversion_v1:
      type: "mean_reversion"
      timeframes: ["15m"]
      enabled: false                # entry exists but not active yet
```

Defaults if keys are missing:

* `max_risk_per_trade_pct = 1.0`
* `max_portfolio_risk_pct = 10.0`
* `max_open_positions = 10`
* `max_per_asset_pct = 5.0`
* `max_daily_drawdown_pct = 10.0`
* `kill_switch_on_drift = true`
* `include_manual_positions = true`
* `volatility_lookback_bars = 20`
* `min_liquidity_24h_usd` optionally defaulted to same as `UniverseConfig.min_24h_volume_usd`.

`params.max_positions` on an individual strategy is a **per-strategy** cap enforced in the strategy code (e.g., ML and mean-reversion entries). Portfolio-wide limits such as `risk.max_open_positions` still apply on top of that and are enforced by the risk engine.

Risk/strategy config must be represented in `AppConfig` as new dataclasses, similar to `UniverseConfig` and `MarketDataConfig`.

---

## 3. Core Concepts & Data Model

### 3.1 Strategy Intent

Strategies do **not** place orders; they emit **intents**:

```text
StrategyIntent:
  strategy_id: str           # e.g. "trend_following_v1"
  pair: str                  # canonical pair, e.g. "XBTUSD"
  side: "long" | "flat"      # Phase 4 only supports long/flat (no shorts)
  intent_type: "enter" | "exit" | "increase" | "reduce" | "hold"
  desired_exposure_usd: float | null   # if None, risk engine sizes it
  confidence: float          # [0.0, 1.0], strength of signal
  timeframe: str             # e.g. "1h", "4h"
  generated_at: datetime     # UTC
  metadata: dict             # free-form (e.g. indicators, scores)
```

Properties:

* Strategies can be “directional only” (no size) by leaving `desired_exposure_usd = None`.
* `confidence` can be used by risk engine to prioritize or clamp sizes.

### 3.2 Risk-Adjusted Action

The risk engine turns intents into **normalized actions**:

```text
RiskAdjustedAction:
  pair: str
  strategy_id: str
  action_type: "open" | "increase" | "reduce" | "close" | "none"
  target_base_size: float           # desired final base units (XBT, ETH, etc.)
  target_notional_usd: float        # desired final USD notional
  current_base_size: float          # from PortfolioService
  reason: str                       # human-readable explanation
  blocked: bool                     # true if action is blocked by risk limits
  blocked_reasons: list[str]        # list of violated limits, if any
  risk_limits_snapshot: dict        # optional, for logging (config values, equity, etc.)
```

This is the **contract output** of Phase 4 and the **input** to Phase 5’s Execution/OMS.

### 3.3 RiskConfig (computed view)

For internal use, risk engine will compute a richer `RiskContext`:

```text
RiskContext:
  equity_usd: float
  realized_pnl_usd: float
  unrealized_pnl_usd: float
  open_positions: list[SpotPosition]       # from PortfolioService
  asset_exposures: list[AssetExposure]     # from PortfolioService
  manual_positions: list[SpotPosition]     # derived from strategy_tag/metadata
  drift_flag: bool                         # from PortfolioService.get_equity()
  daily_drawdown_pct: float                # computed from snapshots / PnL
```

This is not exposed directly, but actions and logs are derived from it.

### 3.4 Strategy ID propagation & tagging

`strategy_id` must remain stable through the full decision pipeline: strategies emit `StrategyIntent`, the risk engine emits matching `RiskAdjustedAction` entries, and the runner can optionally persist `DecisionRecord` rows that reference the same identifier for audit/history. Each `StrategyConfig` includes an optional `userref` (numeric) field to give strategies a durable tag; set it now so Phase 5’s OMS can reuse it for Kraken order tagging and PnL attribution. OMS/userref propagation itself is deferred to Phase 5, but preconfiguring `userref` ensures consistent attribution once execution wiring is live.

---

## 4. Strategy Framework

### 4.1 Strategy interface

Define a base interface (e.g. `Strategy` abstract class):

```text
Strategy:
  id: str
  config: StrategyConfig

  def warmup(self, market_data: MarketDataAPI, portfolio: PortfolioService) -> None:
      # Optional pre-run (e.g. build indicators from history)

  def generate_intents(self, ctx: StrategyContext) -> list[StrategyIntent]:
      # Called on each decision cycle, returns intents
```

Where `StrategyContext` includes:

```text
StrategyContext:
  now: datetime
  universe: list[str]              # pairs this strategy is allowed to trade
  market_data: MarketDataAPI       # for pulling OHLC and prices
  portfolio: PortfolioService      # for current positions and exposures
  timeframe: str                   # the timeframe for this decision cycle ("1h", etc.)
```

Each concrete strategy (e.g. `TrendFollowingStrategy`) implements `generate_intents` based on its config and the current context.

### 4.2 Strategy lifecycle & scheduling

* Strategies are instantiated at startup based on `config.strategies.enabled`.

* `StrategyRunner` orchestrates strategy evaluation:

  * For each decision cycle (e.g. each closed candle per `timeframe`):

    1. Ensure `MarketDataAPI` has up-to-date OHLC for required timeframes.
    2. Ask `PortfolioService` to `sync()` and retrieve a fresh snapshot.
    3. Build `StrategyContext` per strategy/timeframe.
    4. Call `strategy.generate_intents(ctx)` → collect intents.
    5. Pass intents + portfolio snapshot into the risk engine.

* Decision frequency is driven by:

  * Which timeframes are configured for each strategy.
  * A scheduler (either internal timer or external orchestrator) — can be Phase 5/7 concern, but interface must allow this.

### 4.3 Universe & timeframe configuration

* By default, strategies operate on the Phase 2 universe.
* `StrategyConfig.universe_filter` can restrict:

  * Specific pairs,
  * Top N by volume,
  * Asset-type filters (e.g. no stablecoins).

The `StrategyRunner` precomputes per-strategy universes and timeframes based on:

* `strategies.configs[<id>].timeframes`
* `risk.min_liquidity_24h_usd`
* `UniverseConfig.include_pairs` / `exclude_pairs`.

### 4.4 Strategy tagging & portfolio integration

* When strategies emit intents and Phase 5 eventually executes trades, orders MUST carry `userref` or a comment encoding `strategy_id`.
* Phase 3 already persists `userref` and sets `RealizedPnLRecord.strategy_tag` from orders.
* Phase 4 must define a deterministic mapping:

  * e.g. `userref` = numeric ID, and `portfolio.strategy_tags` in config maps IDs → strategy names.
* This ensures PnL and exposure attribution by strategy is consistent across Strategy, Risk, and Portfolio layers.

---

## 5. Risk Engine

### 5.1 Risk limits & config

The risk engine enforces a set of **hard constraints** based on `risk` config:

1. **Max risk per trade** (`max_risk_per_trade_pct`):

   * Given `equity_usd` and a per-pair stop distance (from volatility or config), compute:

     * Max position size such that:

       * `(per_unit_risk * position_size) / equity_usd <= max_risk_per_trade_pct / 100`.

2. **Max portfolio risk** (`max_portfolio_risk_pct`):

   * Sum of all per-position risks (based on stops/volatility and size) must be ≤ this % of equity.

3. **Max open positions**:

   * Number of non-flat positions must not exceed `max_open_positions`.

4. **Per-asset exposure cap** (`max_per_asset_pct`):

   * For each asset, `value_base / equity_base <= max_per_asset_pct / 100`.

5. **Per-strategy exposure cap** (`max_per_strategy_pct`):

   * Sum of notional exposures attributable to a strategy must not exceed `max_per_strategy_pct[strategy_id] / 100` of equity.

Risk engine must compute which limits would be violated by an action and either:

* Downsize the action (e.g. smaller target size), or
* Block it and set `blocked=True` with `blocked_reasons` populated.

### 5.2 Position sizing & volatility

When a `StrategyIntent` has `desired_exposure_usd=None`, risk engine sizes the trade:

1. Get volatility metric for the pair:

   * e.g. ATR using last `risk.volatility_lookback_bars` from `MarketDataAPI.get_ohlc(pair, timeframe, ...)`.
2. Define a “per-unit risk” as some function of volatility (configurable per strategy).
3. Compute maximum position size consistent with `max_risk_per_trade_pct`.
4. Clamp by:

   * `max_per_asset_pct`,
   * `max_per_strategy_pct`,
   * `max_portfolio_risk_pct`.

Result is:

* A `target_notional_usd` and corresponding `target_base_size` for `RiskAdjustedAction`.

### 5.3 Exposure controls & conflict resolution

When multiple strategies emit intents for the same pair:

* Risk engine must aggregate or arbitrate:

  * Option A – Aggregate to a net target:

    * Sum desired exposures (with possible confidence weighting).
    * Clamp by risk constraints.
    * Produce a single `RiskAdjustedAction` with combined reasoning.

  * Option B – Priority/weighting:

    * Use priorities per strategy (configurable) to decide whose intent wins for a given pair.

Phase 4 must define a simple, deterministic policy (e.g., aggregation + clamp) and log how conflicts are resolved.

### 5.4 Drawdown & kill switch

Risk engine must monitor drawdowns using:

* `PortfolioService.get_equity()` and historical snapshots.
* `risk.max_daily_drawdown_pct`.

Behavior:

* If daily drawdown exceeds threshold:

  * Set a **kill-switch status**.
  * Block all new `action_type in {"open", "increase"}`.
  * Optionally:

    * Allow `reduce`/`close` actions to de-risk the portfolio.

Additionally:

* If `risk.kill_switch_on_drift` and `drift_flag=True` from PortfolioService:

  * Block new risk until drift is resolved (e.g., manual intervention, resync).

### 5.5 Manual positions & drift

Manual positions (not tagged with a known `strategy_id`) can be:

* Included in exposure/risk calculations (`include_manual_positions=True`), or
* Ignored for risk budgets (`include_manual_positions=False`), but still included in total equity.

Phase 4 must:

* Use `RealizedPnLRecord.strategy_tag` and/or portfolio config to classify positions as manual vs strategy-owned.
* If ignoring manual positions in risk budgets:

  * Still enforce **hard max per asset** and **max portfolio risk** across all positions, to avoid the bot overlevering around manual positions.

---

## 6. Decision Pipeline & Integration

### 6.1 High-level decision cycle

For each decision tick (e.g. “1h bar closed” event):

1. **Data & portfolio sync**

   * `PortfolioService.sync()` → updated balances, PnL, drift status.
   * `MarketDataAPI.get_data_status()` → fail fast if data is stale.
   * `PortfolioService.get_equity()` → `equity_usd`, `drift_flag`.

2. **Strategy evaluation**

   * For each enabled strategy and each of its configured timeframes that just closed a bar:

     * Build `StrategyContext`.
     * Call `strategy.generate_intents(ctx)` → collect `StrategyIntent`s.

3. **Risk evaluation**

   * Build `RiskContext` from portfolio, exposures, and risk config.
   * Pass `intents + RiskContext` into `RiskEngine`:

     * Get a list of `RiskAdjustedAction` objects.

4. **Execution plan creation (Phase 4 output)**

   * Group actions into an `ExecutionPlan`:

     ```text
     ExecutionPlan:
       plan_id: str
       generated_at: datetime
       actions: list[RiskAdjustedAction]
       metadata: dict   # e.g. equity snapshot, risk mode, etc.
     ```

   * Return this plan to the orchestrator/Phase 5. Phase 4 never talks to Kraken directly.

### 6.2 Integration with Execution (Phase 5-ready)

Phase 4’s `ExecutionPlan` is designed so that Phase 5 can:

* Compare `current_base_size` vs `target_base_size` to derive how much to buy/sell.
* Choose actual order types (market/limit/stop) and sizes that match `target_base_size` while respecting Kraken’s `PairMetadata` (lot size, decimals).
* Log and tag orders with `strategy_id` (via `userref`) to maintain attribution.

No changes are needed in Phase 4 when Phase 5 is implemented, as long as `ExecutionPlan` and `RiskAdjustedAction` remain stable.

### 6.3 Logging & audit

Risk engine and strategy runner must:

* Log at least:

  * The set of `StrategyIntent`s per cycle.
  * The resulting `RiskAdjustedAction`s, including blocked actions and reasons.
  * Any time the kill switch is activated/deactivated.
* Optionally, persist a `DecisionRecord` in the portfolio’s DB for later analysis/visualization (optional for Phase 4, can be Phase 5/6).

---

## 7. Public API of the Strategy & Risk Engine

Expose a main facade, e.g. `StrategyRiskEngine` (or similar), with:

### 7.1 Lifecycle

* `initialize()`:

  * Load risk/strategy config from `AppConfig`.
  * Construct strategies and `StrategyRunner`.
  * Validate that requested timeframes/universe subsets are compatible with `MarketDataAPI` and `PortfolioService`.
  * Optionally run `warmup()` on strategies.

* `reload_config(new_config)`:

  * Allow runtime reload of risk/strategy config without restarting the whole bot (optionally in Phase 4; required by Phase 6/ UI later).

### 7.2 Core methods

* `run_cycle(now: datetime) -> ExecutionPlan`:

  * Implements the decision cycle in §6.1.
  * `now` can be used to align with candle close times.

* `get_risk_status() -> RiskStatus`:

  ```text
  RiskStatus:
    kill_switch_active: bool
    daily_drawdown_pct: float
    drift_flag: bool
    total_exposure_pct: float
    per_asset_exposure_pct: dict[asset, float]
    per_strategy_exposure_pct: dict[strategy_id, float]
  ```

* `get_strategy_state() -> list[StrategyState]`:

  ```text
  StrategyState:
    strategy_id: str
    enabled: bool
    last_intents_at: datetime | null
    last_actions_at: datetime | null
    current_positions: list[SpotPosition]     # positions attributable to this strategy
    pnl_summary: dict                        # high-level from PortfolioService
  ```

These are used by:

* The orchestrator (Phase 5) to decide when to call `run_cycle()`.
* UI (Phase 6) to display current risk and strategy health.

---

## 8. Persistence & Metrics (Phase 4 scope)

Persistence beyond what Phase 3 already stores is **optional** in Phase 4 but recommended:

* **Decision log** (optional table/file):

  ```text
  DecisionRecord:
    id: str
    time: datetime
    execution_plan_id: str
    strategy_intents_count: int
    actions_count: int
    blocked_actions_count: int
    kill_switch_active: bool
    notes: str
  ```

* This can be implemented as:

  * A new table in the same SQLite DB, or
  * A simple log file for now, with the schema reserved for Phase 5/6.

Metrics (for Phase 7):

* Phase 4 should **expose** enough info (via `get_risk_status` / `get_strategy_state`) that a monitoring layer can derive:

  * Intent volume,
  * Action volume,
  * Blocked count,
  * Kill-switch activation frequency.

---

## 9. Testing Expectations (pytest)

Tests should focus on:

### 9.1 Strategy framework

* With a fake `MarketDataAPI` and `PortfolioService`, ensure:

  * A dummy strategy generates expected `StrategyIntent`s given a fixed context.
  * Strategies respect their configured universe/timeframes.

### 9.2 Risk math & limits

* Given synthetic `RiskContext` and intents:

  * `max_risk_per_trade_pct` correctly bounds position sizes.
  * `max_portfolio_risk_pct` prevents excessive aggregate risk.
  * `max_per_asset_pct` and `max_per_strategy_pct` limit exposures correctly.
  * Conflict resolution between two strategies on the same pair is deterministic and testable.

### 9.3 Drawdown & kill switch

* With mocked `PortfolioService.get_equity()` and snapshots:

  * Simulate a series of equity values representing a drawdown crossing `max_daily_drawdown_pct`.
  * Assert `kill_switch_active=True` and new `open/increase` actions are blocked.

### 9.4 Drift handling

* When `PortfolioService.get_equity()` reports `drift_flag=True` and `risk.kill_switch_on_drift=True`:

  * `run_cycle()` returns an `ExecutionPlan` with only `reduce`/`close` actions (or no actions),
  * No `open/increase` actions.

### 9.5 API & integration

* Ensure `run_cycle()`:

  * Calls `PortfolioService.sync()` and `MarketDataAPI.get_data_status()` exactly once per cycle.
  * Produces an `ExecutionPlan` consistent with the provided context and risk config.

* Ensure `get_risk_status()` and `get_strategy_state()` return consistent and correctly derived values from mocked inputs.

---

## 10. Phase 4 Acceptance Checklist

Phase 4 is **complete** when:

* [ ] Strategy framework exists:

  * Base `Strategy` interface.
  * At least one concrete strategy implementation (e.g. `trend_following_v1`) running on real OHLC data.
* [ ] `StrategyRunner` orchestrates strategies per timeframe and universe.
* [ ] Risk engine:

  * Enforces `max_risk_per_trade_pct`, `max_portfolio_risk_pct`, `max_open_positions`, `max_per_asset_pct`, and `max_per_strategy_pct`.
  * Uses volatility-based sizing when `desired_exposure_usd` is not provided.
  * Respects `kill_switch_on_drift` and `max_daily_drawdown_pct`.
* [ ] Risk engine produces `RiskAdjustedAction`s and groups them into an `ExecutionPlan` ready for Phase 5.
* [ ] Manual positions are correctly handled according to `include_manual_positions`.
* [ ] Strategy tagging:

  * Strategies emit `strategy_id`.
  * Orders (Phase 5) can be configured to carry `userref` such that Phase 3 can attribute PnL back to the strategy.
* [ ] Public API exposes:

  * `initialize`, `run_cycle`, `get_risk_status`, `get_strategy_state`.
* [ ] pytest suite covers:

  * Strategy intent generation,
  * Risk math and limits,
  * Conflict resolution,
  * Drawdown/kill switch behavior,
  * Drift handling,
  * Integration of `run_cycle()` with mocked `PortfolioService` and `MarketDataAPI`.

Once this is in place, Phase 5 can focus purely on **Execution & OMS**: turning `ExecutionPlan` into actual Kraken orders, while trusting that strategy & risk logic are already cleanly separated and rigorously tested.
