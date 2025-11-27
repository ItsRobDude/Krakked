Here you go homie — full **Phase 3** rewrite, wired up for Phase 4 and incorporating all the tweaks we talked about.

---

## Phase 3 – Portfolio & PnL Engine Design Contract

### 1. Purpose & Scope

The **Portfolio & PnL Engine** is responsible for:

* Tracking **all balances and positions** in the Kraken account (spot only for US/CA).
* Computing **realized** and **unrealized** PnL in a base currency (USD).
* Providing **time‑stamped portfolio snapshots** and basic performance stats.
* Exposing a clean, stable API for:

  * **Risk engine (Phase 4)** – position sizing, max risk checks, drawdown rules.
  * **Strategy logic (Phase 4+)** – which needs up‑to‑date exposure and PnL.
  * **UI/dashboard (later phases)** – portfolio views, performance charts.

It is **not** responsible for:

* Placing or canceling orders (that’s execution / OMS).
* Strategy decisions (signals, entry/exit logic).
* Advanced analytics (Sharpe, TWR/MWR, factor attribution – those can be added later).

Assumptions:

* **Spot only**, no leverage, no futures (`RegionProfile(code="US_CA", supports_margin=False, supports_futures=False)`). 
* The bot may co‑exist with **manual trading** on the same account; the engine must handle both and keep equity reporting correct.

Phase 3 must deliver a **clean contract** the Phase 4 risk/strategy engine can trust: if Phase 3 says “equity is X” and “this position is Y% of equity,” Phase 4 treats that as ground truth.

---

## 2. Dependencies & Configuration

### 2.1 Dependencies on previous phases

Phase 3 depends on:

1. **Phase 1 – Connection & Region** 

   * `KrakenRESTClient` for private endpoints:

     * `Balance`, `TradeBalance`, `OpenOrders`, `ClosedOrders`, `TradesHistory`, optionally `Ledgers`.
   * `RegionProfile`:

     * `code="US_CA"`
     * `supports_margin=False`
     * `supports_futures=False`
     * `supports_staking` as defined in Phase 1.
     * `default_quote="USD"`.

2. **Phase 2 – Market Data & Universe** 

   * `MarketDataAPI`:

     * `get_universe()` / `get_pair_metadata(pair)`
     * `get_latest_price(pair)` (mid or last; but **consistent** and documented)
     * `get_ohlc(pair, timeframe, lookback)` (closed candles only)
     * `get_data_status()` for health checks.

Phase 3 **reads but does not modify** any Phase 1/2 config; it assumes those modules are already configured and healthy.

### 2.2 Config additions (`config.yaml`)

Extend `config.yaml` with a `portfolio` section:

```yaml
portfolio:
  base_currency: "USD"           # what PnL & equity are reported in

  valuation_pairs:               # optional overrides for how to price certain assets
    BTC: "XBTUSD"
    ETH: "ETHUSD"

  include_assets: []             # optional whitelist of asset codes to track (e.g. ["XBT", "ETH"])
  exclude_assets: []             # optional blacklist (asset codes)

  cost_basis_method: "wac"       # "wac" (weighted avg cost) or "fifo" (reserved for future)
  track_manual_trades: true      # whether to include manual trades in PnL attribution

  snapshot_retention_days: 30    # how long to keep detailed snapshots (for history)

  # Optional: threshold for reconciliation alerts (in base_currency)
  reconciliation_tolerance: 1.0  # e.g. $1 tolerance before raising a drift alert
```

Defaults if keys are missing:

* `base_currency = "USD"`
* `cost_basis_method = "wac"`
* `track_manual_trades = true`
* `snapshot_retention_days = 30`
* `reconciliation_tolerance = 1.0` (or similar small value)

### 2.3 Time & timezone conventions

* All internal timestamps are stored as **UTC**.
* Any “period” PnL (daily/weekly) in later phases will be derived from UTC timestamps; user‑facing local time conversion is a **UI concern**, not Phase 3’s problem.

---

## 3. Core Data Model

### 3.1 Asset balances (spot)

Basic representation of Kraken balances:

```text
AssetBalance:
  asset: str            # e.g. "USD", "XBT", "ETH" (normalized)
  free: float           # available balance
  reserved: float       # in open orders
  total: float          # free + reserved
```

Normalization:

* Map Kraken’s internal asset codes to user‑friendly ones:

  * e.g., `XXBT → XBT`, `ZUSD → USD`.
* Apply `portfolio.include_assets` / `exclude_assets` filters if provided.

### 3.2 Positions (pair-level view)

Even in spot, it’s useful to think in “positions” by **pair**:

```text
SpotPosition:
  pair: str                   # canonical pair: "XBTUSD"
  base_asset: str             # "XBT"
  quote_asset: str            # "USD"

  base_size: float            # net base units held "because of" this pair
  avg_entry_price: float      # cost basis in quote per base unit (e.g. USD per XBT)
  realized_pnl_base: float    # realized PnL in quote/base_currency from this pair
  fees_paid_base: float       # total fees for this pair converted to base_currency
```

Notes:

* Positions describe **how much of each asset you hold and at what cost**, but grouped by pair gives a natural mapping to strategies & Phase 4 risk logic.
* Internally, the engine maintains both:

  * an **asset-level view** (how many BTC, how many ETH, etc.), and
  * a **pair-level view** (what was done in `XBTUSD`, `ETHUSD`, etc.).

### 3.3 PnL records

Per‑trade realized PnL record:

```text
RealizedPnLRecord:
  trade_id: str
  order_id: str | null
  pair: str
  time: datetime (UTC)
  side: "buy" | "sell"
  base_delta: float            # positive for net buy, negative for net sell
  quote_delta: float           # change in quote currency (incl. fees where applicable)
  fee_asset: str
  fee_amount: float
  pnl_quote: float             # realized PnL in quote (USD) for this trade
  strategy_tag: str | null     # e.g. "trend_v1", or "manual"
```

These records allow:

* Auditable PnL math.
* Later **strategy‑level attribution** in Phase 4+.

### 3.4 Portfolio snapshot

The engine can emit snapshots of the entire portfolio state:

```text
PortfolioSnapshot:
  timestamp: datetime (UTC)

  equity_base: float           # total equity in base_currency
  cash_base: float             # base_currency cash balance (e.g. USD)
  asset_valuations: list of:
    asset: str
    amount: float
    value_base: float          # value in base currency
    source_pair: str | null    # valuation pair used (e.g. "XBTUSD")

  realized_pnl_base_total: float
  unrealized_pnl_base_total: float
  realized_pnl_base_by_pair: dict[pair, float]
  unrealized_pnl_base_by_pair: dict[pair, float]
```

Snapshots are used for:

* UI dashboards and charts (later phases).
* Simple performance‑over‑time views.
* Sanity checks for Phase 4 risk (e.g., equity trending down too fast).

### 3.5 Source of Truth & Reconciliation

**Source of truth:**

* **Asset‑level balances**, derived from Kraken’s `Balance` and normalized, are the **canonical source of truth** for holdings.
* Pair‑level `SpotPosition` objects are a **projection** built from the trade history and cost basis logic.

Reconciliation:

* Phase 3 MUST support periodic reconciliation between:

  * Asset balances reconstructed from the internal trade/cash‑flow log, and
  * Live balances from `Balance`.
* If the difference for any asset (or total equity) exceeds `portfolio.reconciliation_tolerance`, the engine:

  * Flags a **“portfolio drift”** condition.
  * Logs the discrepancy with details (per asset).
  * Surfaces drift status through a simple API flag (so Phase 4 and the UI know the portfolio is “dirty”).

This sets up Phase 4 to react appropriately (e.g., stop opening new risk if the accounting is suspect).

---

## 4. Data Sources & Ingestion

### 4.1 Initial sync from Kraken

On first run (or when the engine is initialized):

1. **Balances**

   * Call `Balance` to get all asset balances.
   * Normalize asset codes.
   * Apply `include_assets` / `exclude_assets` filters.

2. **Trade history**

   * Call `TradesHistory` (paginated or with a `start` timestamp).
   * For each trade:

     * Parse: trade ID, order ID, pair, price, volume, cost, fee, fee asset, timestamp, side.
     * Store into an internal **trade log**.

3. **Closed orders (optional)**

   * Call `ClosedOrders` to cross‑check trades vs orders.
   * This is especially useful for:

     * Linking trades to strategies via `userref` or comments.
     * Confirming fills and partial fills.

4. **Ledgers (optional)**

   * Call `Ledgers` for deeper validation of:

     * Deposits/withdrawals,
     * Fee entries,
     * Non‑trade movements.
   * This can be built as a stricter bookkeeping layer in a “Phase 3.5”.

The result of initial sync:

* Asset balances and their normalized form.
* A local trade log sufficient to reconstruct positions and cost basis.
* (Optionally) a mapping from trades to orders/ledgers for more precise accounting.

### 4.2 Incremental updates

In normal operation:

* The engine periodically pulls **new trades**:

  * From `TradesHistory` using:

    * Either a `start` timestamp,
    * Or an offset / high‑water mark based on last seen trade ID or timestamp.
* For each new trade:

  * Update cost basis and positions.
  * Create/update `RealizedPnLRecord`s as needed.
  * Update fee totals per asset and per pair.

The engine maintains a persistent **“last synced” marker** (timestamp and/or last trade ID) to avoid double‑processing trades and to keep polling cheap.

If Phase 4 later introduces WebSocket trade streams, ingestion can be upgraded to event‑driven; Phase 3’s public contract remains the same.

### 4.3 Non‑Trade Balance Changes & Cash Flows

Not all balance changes come from trades. The engine must handle:

* Fiat deposits/withdrawals (e.g. USD).
* Crypto deposits/withdrawals (on‑chain or internal).
* Staking rewards, fee credits, dust conversions, etc.

Phase 3 behavior:

* Represent these as **cash‑flow events**, not trading PnL:

  ```text
  CashFlowRecord:
    id: str
    time: datetime (UTC)
    asset: str
    amount: float          # positive = inflow, negative = outflow
    type: str              # "deposit" | "withdrawal" | "reward" | "adjustment" | ...
    note: str | null
  ```

* These flows:

  * **Change equity**.
  * Are **not** considered trading PnL, so later performance metrics can separate “returned capital” vs “trading gains/losses”.

* Phase 3 only needs to:

  * Detect and record them (when determinable from Ledgers/balance deltas).
  * Expose them via an API so upper layers can build proper performance metrics.

---

## 5. Cost Basis & PnL Logic

### 5.1 Cost basis method

Config option:

* `portfolio.cost_basis_method`:

  * `"wac"` – **Weighted Average Cost** (Phase 3 default).
  * `"fifo"` – reserved for future phases.

Phase 3 implements **only WAC**:

* When you **buy** more of an asset:

  * New average cost =
    `(old_cost_value + new_cost) / (old_qty + new_qty)`
* When you **sell**:

  * Realized PnL =
    `(sell_price - avg_cost) * qty_sold` − fees (in base currency).

This cost basis is maintained at the **asset level** but is also reflected in pair‑level positions for convenience and risk/strategy queries.

### 5.1.1 Numeric precision

To avoid phantom PnL and impossible order sizes:

* The engine MUST respect per‑pair precision from `get_pair_metadata(pair)` (price decimals, volume decimals, lot size).
* Internally, it SHOULD either:

  * Use a decimal‑like type with asset/pair‑specific precision, or
  * Use floats but centralize rounding rules:

    * e.g., 8 decimals for crypto quantities,
    * per‑pair decimals for prices.

All persisted quantities/prices (cost basis, sizes, PnL, valuations) must be rounded through a **single precision utility layer**, not ad‑hoc.

### 5.2 Handling fees

Fees may be charged in:

* Base asset (e.g., BTC),
* Quote asset (e.g., USD),
* Other asset (e.g., staked token, fee credits).

Phase 3 rules:

1. For each trade, record:

   * `fee_asset`, `fee_amount`.
2. Convert fees to base currency (`portfolio.base_currency`, typically USD) using:

   * The trade’s own price when fee asset is base or quote, or
   * `MarketDataAPI.get_latest_price("<fee_asset><base_currency>")` when fee asset is a third asset.
3. Subtract the fee (in base currency) from realized PnL for that trade.

Fee totals per asset and per pair should be tracked so Phase 4/5 can show “fees as % of gross PnL” etc.

### 5.3 Realized vs unrealized PnL

For each asset/pair:

* **Realized PnL**:

  * Sum of PnL from all trades where the position size decreased (you sold some of what you held).
  * Includes all trade‑related fees (converted into base currency).

* **Unrealized PnL**:

  * For each open position:

    * `unrealized = (current_price - avg_entry_price) * position_size`
  * For Phase 3:

    * Assume all fees are realized when the trade happens, so unrealized PnL is purely price delta vs cost basis.

**Consistency requirement:**

* Over time, **change in equity** ≈
  `realized_pnl_total + unrealized_pnl_total + net_cash_flows`,
  up to rounding and any explicitly unvalued assets.

### 5.4 Valuation in base currency (USD)

For each asset held:

1. Determine the appropriate **valuation pair**:

   * From `portfolio.valuation_pairs` if set.
   * Else, default to `<asset><base_currency>` if that pair exists in the universe (e.g., `ETHUSD`).
2. Use `MarketDataAPI.get_latest_price(pair)` to get price in base currency.
3. Compute: `value_base = amount * price`.

Special case – base currency itself:

* Asset = base currency (e.g., `USD`):

  * `value_base = amount` directly.

If an asset has no known valuation pair:

* Log a warning.
* Either:

  * Exclude it from equity, or
  * Value it at 0 and mark it as **“unvalued”**.
* The default for Phase 3 can be: value = 0 + “unvalued” flag; UI can show these explicitly.

---

## 6. Public API of the Portfolio Engine

Expose a cohesive `PortfolioService` interface that Phase 4 (risk/strategy) and the UI can depend on.

### 6.1 Lifecycle

* `initialize()`:

  * Load config.
  * Perform initial sync (balances + historical trades).
  * Build internal state: asset balances, positions, cost basis, PnL records.
  * Optionally produce an initial snapshot.

* `sync()`:

  * Fetch new trades since last sync.
  * Update balances, positions, cost basis, realized PnL, and fees.
  * Detect & record cash‑flow events (deposits/withdrawals/etc.) where possible.
  * Perform reconciliation vs `Balance` and update drift status.
  * Return a summary (e.g., new_trades_count, updated_pairs, drift_detected).

### 6.2 Query methods

1. **Equity and PnL**

   * `get_equity()`:

     * Returns an object containing:

       * `equity_base` (float)
       * `cash_base`
       * `realized_pnl_base_total`
       * `unrealized_pnl_base_total`
       * `drift_flag` (bool) – whether reconciliation is currently out of tolerance.

   * `get_pnl_summary(period="all", include_manual=None)`:

     * For Phase 3:

       * `period = "all"` only (daily/weekly breakdowns can come later).
     * `include_manual`:

       * `None` → follow `track_manual_trades` behavior.
       * `True` → include manual trades in PnL.
       * `False` → exclude manual trades from PnL summary.
     * Returns:

       * Realized/unrealized PnL in base currency, optionally broken down by pair.

2. **Positions & exposure**

   * `get_positions()`:

     * Returns list of `SpotPosition` with:

       * `pair`, `base_size`, `avg_entry_price`,
       * `unrealized_pnl_base`, `current_value_base`.

   * `get_position(pair)`:

     * Returns `SpotPosition` for given canonical pair.
     * If no position exists, raises a well‑defined `PositionNotFoundError`.

   * `get_asset_exposure()`:

     * Returns list of:

       ```text
       AssetExposure:
         asset: str
         amount: float
         value_base: float
         percentage_of_equity: float
       ```

   These APIs are what Phase 4 will use to:

   * Decide per‑trade risk (X% of equity).
   * Enforce per‑asset caps and total exposure caps.

3. **Trade, fee & cash‑flow info**

   * `get_trade_history(pair=None, limit=None, include_manual=None)`:

     * Returns recent trades from the internal log.
     * Optional `pair` filter and manual/bot filtering.

   * `get_fee_summary()`:

     * Returns aggregated fees by:

       * asset,
       * pair,
       * and total in base currency.

   * `get_cash_flows(asset=None, limit=None)`:

     * Returns recent `CashFlowRecord`s (deposits, withdrawals, etc.).

4. **Snapshots**

   * `create_snapshot()`:

     * Creates a `PortfolioSnapshot` for “now” and persists it.

   * `get_snapshots(since=None, limit=None)`:

     * Returns historical snapshots (subject to `snapshot_retention_days`).

---

## 7. Persistence & Storage

### 7.1 Abstraction

Define a persistence interface, e.g. `PortfolioStore`:

```text
PortfolioStore:
  save_state(state)                 # positions, balances, PnL aggregates, high-water marks
  load_state()

  append_trades(trades)
  get_trades(pair=None, limit=None, since=None)

  save_cash_flows(records)
  get_cash_flows(asset=None, limit=None, since=None)

  save_snapshot(snapshot)
  get_snapshots(since=None, limit=None)
  prune_snapshots(older_than_ts)
```

Phase 3 implementation:

* Start with a **local store**:

  * Lightweight SQLite, or
  * Structured file‑based (e.g., Parquet/CSV for trades, JSON/Parquet for snapshots).
* The rest of the engine only talks to `PortfolioStore`, never raw DB/files.

### 7.2 Snapshot retention

On startup and/or periodically:

* Call `prune_snapshots(older_than_ts)` where `older_than_ts` = now − `snapshot_retention_days`.
* Ensure pruning is safe and doesn’t affect core state (snapshots are “views”, not the canonical ledger).

---

## 8. Handling Manual vs Bot Trades (Phase 4‑ready)

Phase 3 doesn’t need full strategy attribution yet, but it must be **ready** for Phase 4’s “who did what” questions.

Trade tagging:

* When ingesting trades:

  * Look at `userref` and/or order comments/metadata.
  * If they match a known bot/strategy tag (e.g. `KRKKD:trend_v1`), store that in `RealizedPnLRecord.strategy_tag`.
  * If no known tag is present, set `strategy_tag = "manual"` (or equivalent).

Config: `portfolio.track_manual_trades`:

* If `true` (default):

  * All trades (bot + manual) are:

    * Included in balances, cost basis, and PnL.
  * Higher‑level PnL queries can still filter by strategy_tag.

* If `false`:

  * **Balances & cost basis still include all trades**. This is non‑negotiable; otherwise equity would be wrong.
  * PnL attribution / summaries **default** to excluding `strategy_tag="manual"` unless explicitly requested with `include_manual=True`.

Additionally:

* The engine SHOULD log and surface events where manual trades or cash flows significantly change equity or exposure, so Phase 4 and the UI can show:

  * “External activity: manual trade/transfer changed your portfolio by X%.”

---

## 9. Testing Expectations (pytest)

Tests should focus on **math correctness**, **deterministic behavior**, and **reconciliation**, using mocks instead of live Kraken where possible.

### 9.1 Cost basis & PnL math

Unit tests:

* Simple buy → sell sequences:

  * 1 buy, 1 sell → realized PnL matches formula.
  * Multiple buys, partial sells → weighted average cost updates correctly.
* Fees in base vs quote vs 3rd asset:

  * Confirm fee conversion to base currency is correct.
  * Confirm fees are subtracted from realized PnL.

### 9.2 Portfolio building from synthetic trades

Given a synthetic set of trades:

* Build positions and PnL with the engine.
* Assert:

  * Final `base_size` per asset matches expectation.
  * `avg_entry_price` correct under WAC.
  * Realized & unrealized PnL match hand‑calculated values.

### 9.3 Valuation with mocked MarketDataAPI

With a mocked `MarketDataAPI`:

* `get_latest_price` returns fixed values.
* Confirm:

  * `equity_base`,
  * asset exposures,
  * unrealized PnL
    are all computed correctly.

### 9.4 Persistence

With a temp `PortfolioStore` implementation:

* Save state; reload; verify:

  * Positions, balances, PnL aggregates are identical.
  * Trade log is preserved.
* Create multiple snapshots; enforce `snapshot_retention_days`; verify:

  * Only snapshots within the retention window survive.

### 9.5 Error behavior

* Missing valuation pair:

  * Engine logs/flags and:

    * either excludes asset from equity, or
    * values it at 0 and marks it “unvalued”.
* `get_position(pair)` for unknown pair:

  * Raises `PositionNotFoundError` (or similar), not a generic error.
* Market data stale or unavailable:

  * PnL and valuations fail with explicit errors or partial results clearly marked.

### 9.6 Reconciliation & Drift Detection

* Use a synthetic scenario where:

  * You create trades and cash flows → compute internal balances & equity.
  * Provide a mocked `Balance` response matching the internal state:

    * Reconciliation passes; `drift_flag=False`.
* Then introduce a discrepancy:

  * Mock `Balance` with an extra 0.01 BTC (for example).
  * If the discrepancy exceeds `reconciliation_tolerance`:

    * Engine sets `drift_flag=True`.
    * Exposes drift status via `get_equity()` and logs a warning.

This ensures Phase 4 can trust the drift flag when deciding whether to keep trading.

---

## 10. Phase 3 Acceptance Checklist

Phase 3 is **done** when:

* [ ] Engine can perform an initial sync from Kraken:

  * Balances (normalized),
  * Trade history,
  * Construct positions and cost basis using WAC.
* [ ] Engine supports incremental sync:

  * Pulls only new trades,
  * Updates positions and realized PnL without double‑counting.
* [ ] Realized and unrealized PnL are computed correctly in base currency (USD), including fees.
* [ ] Asset balances are valued in base currency using `MarketDataAPI`, with clear behavior for unvalued assets.
* [ ] Non‑trade balance changes (deposits, withdrawals, rewards) are represented as `CashFlowRecord`s and included in equity.
* [ ] Public API exposes at least:

  * `initialize`, `sync`,
  * `get_equity`, `get_positions`, `get_position(pair)`,
  * `get_asset_exposure`, `get_trade_history`, `get_fee_summary`,
  * `get_cash_flows`,
  * snapshot creation & retrieval.
* [ ] A storage backend is implemented behind a `PortfolioStore` abstraction.
* [ ] Manual vs bot trades are tagged and filterable via `strategy_tag`, and `track_manual_trades` behaves as specified.
* [ ] Reconciliation vs `Balance` is implemented, with a `drift_flag` when discrepancies exceed tolerance.
* [ ] The pytest suite covers:

  * Cost basis + PnL math (including fees),
  * Portfolio building from synthetic trades,
  * Valuation using mocked prices,
  * Persistence and snapshot retention,
  * Manual vs bot trade filtering,
  * Reconciliation & drift detection,
  * Clear error behavior when data is missing or inconsistent.

At that point, Phase 4 (Risk & Strategy Engine) can safely sit on top of this, assuming:

* Equity & exposure numbers are correct,
* It can see when the portfolio is “clean” vs “drifting,” and
* It can filter PnL and exposures by strategy and manual activity as needed. 
