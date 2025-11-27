

## Phase 3 – Portfolio & PnL Engine Design Contract

### 1. Purpose & Scope

The **Portfolio & PnL Engine** is responsible for:

* Tracking **all balances and positions** in the Kraken account (spot only for US/CA).
* Computing **realized** and **unrealized** PnL in a base currency (USD).
* Providing **time‑stamped portfolio snapshots** and basic performance stats.
* Exposing a clean API for:

  * Risk engine (Phase 4),
  * Strategy logic (Phase 4+),
  * UI/dashboard (later phases).

It is **not** responsible for:

* Placing or canceling orders (that’s execution / OMS).
* Strategy decisions (buy/sell signals).
* Complex performance analytics (Sharpe, etc. can come later).

It assumes:

* **Spot only**, no leverage, no futures (US_CA profile).
* The bot may co‑exist with manual trading on the same account; the engine should be able to handle both.

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
     * `default_quote="USD"`

2. **Phase 2 – Market Data & Universe**

   * `MarketDataAPI`:

     * `get_universe()` / `get_pair_metadata(pair)`
     * `get_latest_price(pair)` (mid or last; but consistent)
     * `get_ohlc(pair, timeframe, lookback)` (closed candles only)
     * `get_data_status()` for health checks

### 2.2 Config additions (`config.yaml`)

Extend `config.yaml` with a `portfolio` section:

```yaml
portfolio:
  base_currency: "USD"           # what PnL & equity are reported in
  valuation_pairs:               # optional overrides for how to price certain assets
    BTC: "XBTUSD"
    ETH: "ETHUSD"

  include_assets: []             # optional whitelist of asset codes to track (e.g. ["XBT", "ETH"])
  exclude_assets: []             # optional blacklist

  cost_basis_method: "wac"       # "wac" (weighted avg cost) or "fifo" (reserved for future)
  track_manual_trades: true      # whether to include trades not tagged as bot trades

  snapshot_retention_days: 30    # how long to keep detailed snapshots (for history)
```

Defaults if keys are missing:

* `base_currency = "USD"`
* `cost_basis_method = "wac"`
* `track_manual_trades = true`
* `snapshot_retention_days = 30`

---

## 3. Core Data Model

### 3.1 Asset balances (spot)

Basic representation of Kraken balances:

```text
AssetBalance:
  asset: str            # e.g. "ZUSD", "XXBT", "XETH"
  free: float           # available balance
  reserved: float       # in open orders
  total: float          # free + reserved
```

Phase 3 should normalize:

* Map Kraken’s internal asset codes to user‑friendly ones:

  * e.g., `XXBT → XBT`, `ZUSD → USD`.

### 3.2 Positions (pair-level view)

Even in spot, it’s useful to think in “positions” by pair:

```text
SpotPosition:
  pair: str                   # canonical pair: "XBTUSD"
  base_asset: str             # "XBT"
  quote_asset: str            # "USD"

  base_size: float            # net amount of base asset held "because of" this pair
  avg_entry_price: float      # USD price per base unit (cost basis)
  realized_pnl_base: float    # realized PnL in quote (USD) from this pair
  fees_paid_base: float       # total fees in base and/or quote converted to USD
```

Notes:

* For a pure spot US account, you can treat “positions” as describing **how much of each asset you hold and at what cost**, but organizing them by pair gives a natural mapping to strategies and PnL.
* Internally, you will likely maintain both:

  * an **asset-level view** (how many BTC, how many ETH…), and
  * a **pair-level view** (what was done in XBTUSD, ETHUSD, etc.).

### 3.3 PnL records

To audit and summarize PnL, define a simple record:

```text
RealizedPnLRecord:
  trade_id: str
  pair: str
  time: datetime
  side: "buy" | "sell"
  base_delta: float            # positive for net buy, negative for net sell
  quote_delta: float           # USD change (including fees)
  fee_asset: str
  fee_amount: float
  pnl_quote: float             # realized PnL in quote (USD) for this trade
  strategy_tag: str | null     # optional, for later strategy attribution
```

### 3.4 Portfolio snapshot

The engine should be able to create snapshots like:

```text
PortfolioSnapshot:
  timestamp: datetime

  equity_base: float           # total equity in base_currency (USD)
  cash_base: float             # USD (or base currency) cash balance
  asset_valuations: list of:
    asset: str
    amount: float
    value_base: float          # value in base currency
    source_pair: str | null    # which pair price was used (e.g. "XBTUSD")

  realized_pnl_base_total: float
  unrealized_pnl_base_total: float
  realized_pnl_base_by_pair: dict[pair, float]
  unrealized_pnl_base_by_pair: dict[pair, float]
```

Snapshots are used for:

* UI dashboards.
* Performance over time (Phase 5+).

---

## 4. Data Sources & Ingestion

### 4.1 Initial sync from Kraken

On first run (or when the engine is initialized):

1. **Balances**

   * Call `Balance` to get all asset balances.
   * Normalize asset codes (e.g., `XXBT → XBT`, etc.).
   * Filter using `portfolio.include_assets` / `exclude_assets` if configured.

2. **Trade history**

   * Call `TradesHistory` (possibly paginated or with `start` timestamp).
   * For each trade:

     * Parse trade ID, pair, price, volume, cost, fee, fee asset, timestamp.
     * Determine direction (buy vs sell).
     * Store it in a local “trade log” store.

3. **Closed orders (optional)**

   * Call `ClosedOrders` to cross-check fills vs orders.
   * Useful later when you want to attach strategy tags (via `userref` or `comment`).

4. **Ledgers (optional for now)**

   * Use `Ledgers` to further validate balances/fees if necessary.
   * This can be Phase 3.5 if you want extra strict bookkeeping.

### 4.2 Incremental updates

In normal operation:

* The engine periodically pulls **new trades**:

  * Using `TradesHistory` with `start` or `ofs` based on last seen trade.
* For each new trade:

  * Update cost basis and position (see cost basis below).
  * Update realized PnL records.
  * Update fee totals.

The engine should maintain a simple “last synced timestamp or trade id” to avoid reprocessing old trades.

(If you later add WebSocket order/trade streams, this ingestion can be event‑driven instead of purely polling.)

---

## 5. Cost Basis & PnL Logic

### 5.1 Cost basis method

Config option:

* `portfolio.cost_basis_method`:

  * `"wac"` – **Weighted Average Cost** (Phase 3 default).
  * `"fifo"` – reserved for future; not required in Phase 3.

For Phase 3, implement only **weighted average cost**:

* When you **buy** more of an asset:

  * New average cost = `(old_cost_value + new_cost) / (old_qty + new_qty)`.
* When you **sell**:

  * Realized PnL = `(sell_price - avg_cost) * qty_sold` − fees (expressed in USD).

### 5.2 Handling fees

Fees may be charged in:

* The base asset (e.g., BTC),
* The quote asset (USD),
* Or another asset (Kraken fee credits, etc.).

Phase 3 rules:

1. Record fees from each trade:

   * `fee_asset`, `fee_amount`.
2. Convert fees to base currency (USD) using:

   * The trade’s own price if fee asset = base or quote, or
   * Latest price for fee asset’s USD pair from `MarketDataAPI` if it’s a third asset.
3. Subtract total fee (in USD) from PnL for that trade.

### 5.3 Realized vs unrealized PnL

For each asset (or pair):

* **Realized PnL**:

  * Sum of PnL from all trades where the position size in that asset decreased (you sold some of what you held).
  * Include fees.

* **Unrealized PnL**:

  * For each open position:

    * `unrealized = (current_price - avg_entry_price) * position_size`.
  * Fee impact on unrealized PnL:

    * For Phase 3: assume fees are fully realized at trade time; unrealized PnL is purely price delta vs cost.

### 5.4 Valuation in base currency (USD)

For each asset held:

1. Identify the relevant **valuation pair**:

   * From `portfolio.valuation_pairs` if set.
   * Else, default to `<asset><base_currency>` if present in universe (e.g., `ETHUSD`).
2. Use `MarketDataAPI.get_latest_price(pair)` to get price in USD.
3. `value_base = amount * price`.

Special case: base currency itself (USD):

* Value is just the balance (no FX).

Phase 3 can assume every tracked asset has at least one USD pair available in the universe. If not, it should log a warning and either:

* Exclude that asset from equity calculations, or
* Use a placeholder value of 0 and mark it as “unvalued” (to be decided; default can be 0 with a warning).

---

## 6. Public API of the Portfolio Engine

Expose a cohesive `PortfolioService` (conceptually; naming can differ) with roughly this behavior.

### 6.1 Lifecycle

* `initialize()`:

  * Loads config.
  * Performs initial sync (balances + past trades).
  * Builds internal positions and PnL state.
  * Optionally creates an initial snapshot.

* `sync()`:

  * Fetches new trades since last sync.
  * Updates positions, realized PnL, and fees.
  * Returns a summary of what changed (e.g., number of new trades, pairs touched).

### 6.2 Query methods

1. **Equity and PnL**

   * `get_equity()`:

     * Returns current total equity in base currency, plus:

       * `cash_base`,
       * `realized_pnl_base_total`,
       * `unrealized_pnl_base_total`.

   * `get_pnl_summary(period=None)`:

     * For now, Phase 3 can support:

       * `period = "all"` : all-time PnL (default).
       * Later, daily/weekly breakdowns can be added.
     * Returns realized/unrealized PnL in base currency, optionally by pair.

2. **Positions & exposure**

   * `get_positions()`:

     * Returns a list of `SpotPosition` objects, including:

       * pair,
       * base_size,
       * avg_entry_price,
       * unrealized_pnl_base,
       * current_value_base.

   * `get_position(pair)`:

     * Returns single `SpotPosition` for given canonical pair, or raises if no position.

   * `get_asset_exposure()`:

     * Returns exposures by asset:

       * `asset`, `amount`, `value_base`, `percentage_of_equity`.

3. **Trade & fee info**

   * `get_trade_history(pair=None, limit=None)`:

     * Returns the most recent trades, optionally filtered by pair.
     * At minimum, uses internal stored trade log (not required to call Kraken every time).

   * `get_fee_summary()`:

     * Total fees paid in base currency, optionally by asset or by pair.

4. **Snapshots**

   * `create_snapshot()`:

     * Forces a snapshot at the current moment.
   * `get_snapshots(since=None, limit=None)`:

     * Returns historical snapshots (limited by `snapshot_retention_days`).

---

## 7. Persistence & Storage

### 7.1 Abstraction

Define a persistence abstraction, e.g. `PortfolioStore`, to avoid hard‑wiring storage:

```text
PortfolioStore:
  save_state(state)
  load_state()
  append_trades(trades)
  get_trades(...)
  save_snapshot(snapshot)
  get_snapshots(...)
```

Phase 3 implementation:

* **Simple local store**:

  * Either:

    * a lightweight SQLite DB, or
    * a structured file‑based store (e.g., trades in Parquet/CSV, snapshots in JSON).
* The important part: the rest of the engine talks to the interface, not to the raw DB/files.

### 7.2 Snapshot retention

On startup or periodically, the engine should:

* Delete snapshots older than `snapshot_retention_days` (if configured), to keep storage manageable.

---

## 8. Handling Manual vs Bot Trades (Forward-looking, but Phase 3-aware)

Phase 3 doesn’t need full strategy attribution yet, but it should be **ready** for it:

* When ingesting trades, look for:

  * Kraken’s `userref` field or `order description` that contains a known bot tag (e.g. `KRKKD`).
* Store an optional `strategy_tag` or `source_tag` on `RealizedPnLRecord`.
* Config option `portfolio.track_manual_trades`:

  * If `true` (default), include all trades in portfolio & PnL.
  * If `false`, only include trades with bot tags; others may still affect balances, but you might either:

    * ignore them for PnL, or
    * warn about “untracked manual changes”.

For now, Phase 3 can **store** the tags but doesn’t need complex logic; that comes later with strategy/risk attribution.

---

## 9. Testing Expectations (pytest)

Tests should focus on **math correctness** and **deterministic behavior** rather than live Kraken calls.

### 9.1 Cost basis & PnL math

Unit tests for:

* Simple buy → sell sequences:

  * 1 buy, 1 sell → realized PnL matches formula.
  * Multiple buys then partial sells → weighted average cost updated correctly.
* Fees in base vs quote assets:

  * Confirm fees are included and converted correctly in PnL.

### 9.2 Portfolio building from synthetic trades

Given a set of synthetic trades (mocked `TradesHistory`):

* Build positions and PnL using the engine.
* Assert:

  * Final base_size matches expected.
  * avg_entry_price matches expected.
  * realized & unrealized PnL match hand‑calculated values.

### 9.3 Valuation with mocked MarketDataAPI

* Use a mocked `MarketDataAPI` where `get_latest_price` returns known values.
* Confirm:

  * Equity,
  * Asset exposures,
  * Unrealized PnL
    are computed correctly.

### 9.4 Persistence

* With a fake `PortfolioStore` backend (or a temp SQLite / files):

  * Save state, reload, and ensure positions and PnL are identical.
  * Snapshot retention removes old snapshots as configured.

### 9.5 Error behavior

* Missing valuation pair for an asset → engine logs/flags and either:

  * excludes asset from equity, or
  * values it at 0 while marking it as “unvalued”.
* Unknown pair requested in `get_position(pair)` → raises a clear `PositionNotFoundError` (or similar).

---

## 10. Phase 3 Acceptance Checklist

Phase 3 is **done** when:

* [ ] Engine can perform an initial sync from Kraken:

  * Balances,
  * Trade history,
  * Constructing positions and cost basis.
* [ ] Engine supports incremental sync:

  * Pulls new trades,
  * Updates positions and realized PnL without double‑counting.
* [ ] Realized and unrealized PnL are computed correctly in base currency (USD), including fees.
* [ ] Asset balances are valued using prices from `MarketDataAPI`, with a clear fallback behavior when no price is available.
* [ ] Public API exposes:

  * `get_equity`, `get_positions`, `get_position(pair)`,
  * `get_asset_exposure`, `get_trade_history`, `get_fee_summary`,
  * Snapshot creation & retrieval.
* [ ] A storage backend is implemented and swappable (via a `PortfolioStore`‑style abstraction).
* [ ] pytest suite covers:

  * Cost basis and PnL math, including fees,
  * Portfolio building from synthetic trades,
  * Valuation using mocked prices,
  * Persistence correctness and snapshot retention,
  * Clear error behavior when data is missing or inconsistent.

