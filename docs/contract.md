# Krakked Contract (Phases 1-7)

This consolidated contract collects the individual phase design documents for quick reference. Each section mirrors the original phase file with no behavioral changes.

## Phase 1

Kraken Connection Module – Phase 1 Design Contract
1. Purpose & Scope

The Kraken Connection Module is responsible for:

Managing API credentials securely (load, store, validate).

Providing a clean interface for Kraken REST API (public + private).

Handling region profile and capability flags (e.g., US_CA, supports_margin = false).

Enforcing basic safety rules (no secret leakage, no unauthorized features).

It is not responsible for:

Strategy logic, risk logic, or order sizing.

User interface.

Long‑term data storage (beyond its own config/secrets).

This module should be usable as a standalone library by later phases.

2. Configuration & Secrets Design

Config directory

Use an OS‑specific configuration directory:

Linux: ~/.config/kraken_bot/

macOS: ~/Library/Application Support/kraken_bot/

Windows: %APPDATA%\kraken_bot\

All module‑owned files live under this directory.

Files

Secrets file (encrypted)

Path: <config_dir>/secrets.enc

Contents: API key + API secret (and any encryption metadata like salt/nonce).

Must be encrypted at rest using a master password derived key.

Module is responsible for setting the most restrictive file permissions the OS allows (user‑only if possible).

Non‑secret config file

Path: <config_dir>/config.yaml (or .toml / .json, but one format only).

Contents include:

region (e.g., "US_CA").

capabilities flags (e.g., supports_margin, supports_futures, supports_staking).

Any other non‑sensitive behavior toggles relevant to the connection module (e.g., base URL overrides for testing).

Secret vs non‑secret

Secrets file: only API key, API secret, and encryption metadata.

Config file: region, capabilities, and other non‑sensitive settings.

3. Credential Loading & Precedence

The module exposes a clear concept of “credentials” (key + secret), and loads them with this precedence:

Environment variables

If both env vars for key and secret are present, they take precedence over everything else.

If only one is present, treat as incomplete → do not use.

Encrypted secrets file

If env vars are not usable, and the secrets file exists:

Prompt for master password.

Decrypt the file.

If decryption succeeds, use these credentials.

If decryption fails, surface a clear error (“Wrong password or corrupted secrets file”) and do not produce fake credentials.

No credentials available

If neither env vars nor a valid secrets file is available:

Signal “no credentials present” in a defined way that the calling code can detect (e.g., a specific exception or return status).

This can be used to trigger a first‑time setup flow.

4. First‑Time Setup & Credential Validation

On first run (or when no credentials are available), the calling code can invoke an interactive setup that uses the module’s APIs.

Required behavior for setup:

User enters API key and API secret.

The module performs a validation call to a private Kraken endpoint (e.g., a read‑only balance endpoint).

If the API call shows an authentication problem:

Example: invalid key, malformed signature, permissions missing.

The module must:

Return an error state clearly indicating an auth problem.

Not save the credentials.

If the API call fails due to a network or service problem:

Example: timeout, DNS failure, Kraken downtime.

The module must:

Distinguish this from auth failure (i.e., not label it “invalid key”).

Allow the caller to decide:

Retry validation, or

Save as “unvalidated” if desired (flag exposed in the response).

If validation succeeds:

The module encrypts and saves the credentials to secrets.enc.

It records enough metadata to know that this credential set has been validated at least once.

Key requirement:
The module must never silently persist unvalidated credentials after an apparent auth failure. Auth errors must block saving unless the caller explicitly overrides.

5. Region & Capability Profile

The module is responsible for exposing a region profile and basic capabilities.

Region and capabilities are stored in the non‑secret config file.

Minimum required fields:

region: string; e.g., "US_CA".

capabilities:

supports_margin: boolean.

supports_futures: boolean.

supports_staking: boolean.

The module provides a way to read this profile as a structured object.

The profile is read‑only from the module’s perspective in Phase 1:

It can assume the file is present and/or provide sensible defaults if missing.

Writing/updating the region file can be part of a later phase, but the read path must be stable.

6. REST API Interface & Error Handling

The module abstracts Kraken REST into a small, consistent surface:

A public call method (e.g., “perform a public GET with method and params”).

A private call method (e.g., “perform a signed POST with method and data”).

Signature generation

The module generates Kraken’s required API‑Sign header correctly, based on:

URL path.

Nonce.

Request body data.

This is part of what will be unit tested.

Error handling

If Kraken returns an error list, the module:

Interprets common categories (auth, rate limit, general).

Raises/returns structured errors that clearly differentiate:

Auth issues.

Rate‑limit or throttling issues.

Service/other errors.

Under no circumstances should any API key or secret appear in:

Exception messages.

Logs produced by this module.

7. Testing Expectations

A pytest test suite is part of the deliverable for Phase 1.

Required test coverage:

Credential loading

Env vars present: env credentials are used.

Only secrets file present: decrypted credentials are used.

Both present: env wins.

Invalid/missing env vars: handled cleanly.

Corrupted secrets file or wrong master password: clean, explicit failure.

Encryption/decryption

Given known test credentials and a test password:

Encrypt → decrypt results in identical values.

Wrong password does not silently produce wrong credentials.

API signature generation

Golden test:

Known input (URL path, nonce, data) → expected exact signature string.

Edge cases:

Empty payload case.

Parameter ordering is handled consistently.

Logging / secrecy

Tests verifying that logging/exception messages from this module do not include the raw API key or secret.

Config reading

Config file missing → defaults are applied and well‑defined.

Config file present → region and capabilities are read correctly.

Integration tests that actually call Kraken are optional in this phase and may use mocks/stubs instead. The key is correctness of local behavior.

8. Minimal Project Structure

A simple, extensible structure is enough for Phase 1:

pyproject.toml

Declares dependencies and test tooling (pytest, crypto lib, HTTP client, etc.).

src/kraken_bot/

connection.py (or equivalent) – main REST + auth interface.

secrets.py – encryption, decryption, secrets loading logic.

config.py – config directory resolution and region/capabilities loading.

tests/

test_secrets.py

test_config.py

test_connection_signing_and_loading.py

Naming can vary slightly, but the responsibilities must stay clearly separated.

9. Acceptance Checklist for Phase 1

The module is “done” for Phase 1 if all of the following are true:

Given valid env vars, the module can:

Load credentials.

Generate a valid signature for a known test case.

Given valid credentials and no env vars, the module can:

Perform an interactive setup flow:

Validate credentials against a private endpoint.

Encrypt and save them.

Load them on next run via secrets file + master password.

Given invalid credentials, the module:

Detects auth errors during validation.

Does not save them by default.

Region and capabilities are:

Readable from a non‑secret config file.

Available as a structured profile to callers.

Secrets are:

Never written in plaintext to disk.

Never logged or included in exception messages.

The pytest suite:

Runs successfully.

Covers the main behaviors listed in the Testing section.

If all of that is true, you have the “secure, tested Kraken connection module” that the later phases can safely build on.


## Phase 2

## Phase 2 – Market Data & Pair Universe Design Contract

### 1. Purpose & Scope

The **Market Data & Pair Universe module** is responsible for:

* Discovering and maintaining the list of **tradable USD spot pairs** (the “universe”) for a US/CA account.
* Providing **historical OHLC** and **real‑time streaming data** for those pairs.
* Doing this in a **rate‑limit‑safe** and **fault‑tolerant** way.
* Exposing a clean, stable API that later phases (strategies, portfolio/PnL, UI) can rely on.

It is **not** responsible for:

* Strategy logic or trade decisions.
* PnL and portfolio accounting.
* Order placement or execution.

It **depends on Phase 1** for:

* Region profile (`RegionProfile`) loaded from config.
* Config directory discovery & `config.yaml` handling.
* REST client for Kraken public endpoints.

---

### 2. Dependencies on Phase 1 & Configuration

#### 2.1 Region profile

Phase 1 exposes a `RegionProfile` object, conceptually:

```python
@dataclass
class RegionProfile:
    code: str                   # e.g. "US_CA"
    supports_margin: bool
    supports_futures: bool
    supports_staking: bool
    default_quote: str = "USD"
```

For the current project, we assume:

```python
RegionProfile(
    code="US_CA",
    supports_margin=False,
    supports_futures=False,
    supports_staking=False,
    default_quote="USD",
)
```

The Phase 2 module **reads but does not modify** the region profile. It uses:

* `code` (for future region‑specific constraints).
* `supports_margin`, `supports_futures` (to avoid margin/futures products).
* `default_quote` (usually `"USD"`).

#### 2.2 `config.yaml` structure

Phase 2 expects the global config file to live in the same config directory as Phase 1, e.g.:

* Linux: `~/.config/kraken_bot/config.yaml`
* macOS: `~/Library/Application Support/kraken_bot/config.yaml`
* Windows: `%APPDATA%\kraken_bot\config.yaml`

Expected structure (example):

```yaml
region:
  code: "US_CA"
  capabilities:
    supports_margin: false
    supports_futures: false
    supports_staking: false
  default_quote: "USD"

universe:
  include_pairs: []              # optional: e.g. ["XBTUSD", "ETHUSD"]
  exclude_pairs: []              # optional: e.g. ["DOGEUSD"]
  min_24h_volume_usd: 100000.0   # optional liquidity floor

market_data:
  ws:
    stale_tolerance_seconds: 60  # max age for “fresh” WS data

  ohlc_store:
    backend: "parquet"           # or "csv" etc.
    root_dir: "~/.local/share/kraken_bot/ohlc"
```

Phase 2 uses:

* `universe.*` for overrides and liquidity filtering.
* `market_data.ws.stale_tolerance_seconds` to decide when data is stale.
* `market_data.ohlc_store.*` to configure the storage backend.

If keys are missing, the module should apply sensible defaults and document them.

---

### 3. Pair Universe & Metadata

#### 3.1 Data source

* Use Kraken **Get Tradable Asset Pairs** (`/0/public/AssetPairs`) via the Phase 1 connection.
* Optionally enrich with **Get Asset Info** (`/0/public/Assets`) if needed for asset metadata.

#### 3.2 Filtering rules (USD spot universe)

The module builds an internal **pair universe** as follows:

1. Start from all entries in `AssetPairs`.
2. Filter to **USD spot pairs**:

   * Quote asset maps to USD (usually `ZUSD` in the response).
   * `status == "online"` only (ignore `cancel_only`, `post_only` for now).
   * `aclass` indicates a spot asset (not futures/margin aclass values).
   * Include even if `leverage_buy`/`leverage_sell` arrays are populated; leverage settings are **not** used to exclude USD spot pairs.
   * Respect `RegionProfile` for margin/futures constraints at **execution/risk enforcement time**, not at universe construction:

     * Phase 2 may surface pairs that Kraken labels as margin‑capable, but order placement/risk checks (later phases) must enforce `supports_margin`/`supports_futures`.
3. Extract and store metadata for each pair:

   * `raw_name`: the AssetPairs key (e.g. `"XXBTZUSD"`).
   * `canonical`: internal canonical name (see below; e.g. `"XBTUSD"`).
   * `base` and `quote` assets.
   * `rest_symbol`: the symbol used for REST calls (altname).
   * `ws_symbol`: the symbol used for WebSocket subscriptions (wsname).
   * Decimals & lot info (price precision, volume precision, min order size).
   * Status.

#### 3.3 Canonical pair name

* Use the **`altname` field as the canonical internal name**, e.g.:

  * `altname = "XBTUSD"` → `canonical = "XBTUSD"`.
* For each pair, Phase 2 must store:

```python
@dataclass
class PairMetadata:
    canonical: str      # "XBTUSD"
    base: str           # "XBT"
    quote: str          # "USD"
    rest_symbol: str    # "XBTUSD" (altname)
    ws_symbol: str      # "XBT/USD" (wsname)
    raw_name: str       # "XXBTZUSD"
    price_decimals: int
    volume_decimals: int
    lot_size: float
    status: str         # "online", etc.
```

Everywhere else in the code, pairs are referred to by `canonical` (e.g. `"XBTUSD"`). REST/WebSocket specifics are localized to this module.

#### 3.4 Universe overrides & liquidity filtering

After building the base universe:

* Apply **include/exclude** overrides from config:

  * `universe.exclude_pairs`: remove these `canonical` symbols if present.
  * `universe.include_pairs`: ensure these `canonical` symbols are included if they exist in AssetPairs.

* If `universe.min_24h_volume_usd` is set and > 0:

  * Use Kraken ticker or OHLC volume data to estimate 24h USD volume.
  * Drop pairs whose estimated 24h USD volume is below this threshold.

The result is a **deduplicated list of canonical pair names** + metadata, exposed as “the universe”.

#### 3.5 Universe refresh

The module must provide a way to **refresh the universe**:

* Re-fetch `AssetPairs`.
* Reapply all filters and overrides.
* Update the in‑memory universe structure.

Scheduling of refreshes (e.g. hourly) is left to higher layers.

---

### 4. Historical OHLC Data (REST)

#### 4.1 Source & timeframes

* Use Kraken **Get OHLC Data** (`/0/public/OHLC`).

* Supported timeframes (minimum set):

  * `"1m"`, `"5m"`, `"15m"`, `"1h"`, `"4h"`, `"1d"`

* Map these higher‑level timeframe labels to Kraken’s `interval` integers internally.

#### 4.2 Handling the “running” candle

**Design decision (fixed):**

* `get_ohlc(pair, timeframe, lookback)` must **return only fully closed candles**.
* The “running” last candle from Kraken (which may still be forming) is **excluded** from historical data.
* Live/in‑progress candles are handled via WebSocket (see `get_live_ohlc` below).

This makes historical OHLC deterministic and suitable for backtesting and indicator calculations.

#### 4.3 Local storage abstraction

Phase 2 defines an **OHLC store interface**, e.g.:

* Append/update bars for (pair, timeframe).
* Retrieve bars by lookback count and/or timestamp.

The concrete implementation for Phase 2:

* **File‑based backend** (behind the abstraction):

  * Directory layout e.g.:

    * `<root_dir>/<timeframe>/<pair>.parquet`
      (or `.csv`, controlled by `market_data.ohlc_store.backend`)

  * `root_dir` from `config.yaml` (`market_data.ohlc_store.root_dir`), with `~` expansion.

The storage backend details are **not exposed** to callers; they only see Python structures.

#### 4.4 Public historical API

The module must expose functions along the lines of:

* `backfill_ohlc(pair, timeframe, since=None)`

  * Uses REST to populate the local store, possibly via multiple calls.
* `get_ohlc(pair, timeframe, lookback)`

  * Returns a list of closed OHLC bars ordered by time (oldest → newest).
* `get_ohlc_since(pair, timeframe, since_ts)`

  * Returns closed bars with timestamp ≥ `since_ts`.

Error behavior must be defined and consistent:

* Unknown pair → specific “unknown pair” error.
* Not enough data → return fewer bars or an explicit error (documented).
* Rate‑limit / network problems → clear exceptions, not silent failures.

---

### 5. Real‑Time Market Data (WebSocket v2)

#### 5.1 WebSocket API version

The project standard for Phase 2 is:

* **Kraken WebSocket API v2**.

The WS client is designed specifically around v2 payloads and channels.

#### 5.2 Channels & subscriptions

At minimum, subscribe to:

* **Ticker** channel:

  * For L1 price data: best bid/ask, last trade, volume, etc.
* **OHLC** channel:

  * For live candle updates on chosen timeframes (e.g. 1m, 5m, 1h).

(Deeper order book channels can be added later.)

#### 5.3 Symbol translation

The WS client uses `PairMetadata.ws_symbol` (e.g. `XBT/USD`) for subscriptions.

The module must ensure correct mapping:

* Internal canonical (`"XBTUSD"`) → `ws_symbol` for WS.
* For incoming messages, map back to canonical using stored metadata.

#### 5.4 Connection management

The WS client must:

* Establish an initial v2 WebSocket connection.
* Subscribe to required channels for all pairs in the universe (in batches if needed).
* Handle:

  * Connection drops.
  * Subscription errors/rejections.
  * Reconnects with exponential backoff.
* After reconnect, resubscribe to all previously subscribed channels.

Log key events (connect, disconnect, retry, subscribe, resubscribe) in a controlled way.

#### 5.5 In‑memory cache & staleness

The module maintains an in‑memory cache keyed by canonical pair:

* Latest **ticker** snapshot:

  * Price(s), bid/ask, volume, timestamp.
* Current **live candle** per timeframe:

  * It may be a running candle (incomplete).

Each cache entry records a `last_update_ts` timestamp.

**Staleness rule:**

* Configuration key: `market_data.ws.stale_tolerance_seconds` (default: `60`).
* When a caller requests:

  * `get_latest_price(pair)`
  * `get_best_bid_ask(pair)`
  * `get_live_ohlc(pair, timeframe)`

  The module must:

  * Compute `now - last_update_ts`.
  * If this exceeds `stale_tolerance_seconds`:

    * Raise a defined “data stale” error (custom exception), or
    * Return a clearly marked stale status (depending on chosen API shape).

Stale data must **not** be silently treated as fresh.

#### 5.6 Public live API

The module should expose functions like:

* `get_latest_price(pair)` → last traded or mid price (document choice).
* `get_best_bid_ask(pair)` → current bid/ask.
* `get_live_ohlc(pair, timeframe)` → latest live candle (may be incomplete).

If data hasn’t arrived yet or is stale, it must signal that explicitly.

---

### 6. Rate Limiting & Reliability

#### 6.1 REST throttling

All REST calls in this module (AssetPairs, OHLC, ticker used for liquidity) must go through a **shared rate limiter**, which:

* Enforces a conservative upper bound (e.g. ~1 request per second baseline).
* Queues/delays calls when needed.
* Distinguishes rate‑limit errors (e.g. API throttling) from other errors.

#### 6.2 Error categories

For REST:

* Network/HTTP problems → network errors.
* Kraken `error` field → categorized:

  * Rate limit / throttling.
  * Service unavailable.
  * Other API errors.

For WebSocket:

* Connection loss → triggers reconnect with backoff.
* Subscription failure → logged and surfaced via health/status.
* Malformed messages → safely ignored with logging, not crash the process.

#### 6.3 Health / status API

The module should expose a simple health/status summary, e.g.:

* `get_data_status()` → object including:

  * REST reachable / not.
  * WS connected / not.
  * Number of pairs with recent ticker updates.
  * Per‑pair staleness flags optional.

---

### 7. Public API of the Module (Phase 2)

From the perspective of other modules, Phase 2 should expose a small, clear interface:

* **Universe & metadata**

  * `get_universe()` → list of canonical pair names (e.g. `["XBTUSD", "ETHUSD", ...]`).
  * `get_pair_metadata(pair)` → `PairMetadata` for a given canonical symbol.

* **Historical data**

  * `backfill_ohlc(pair, timeframe, since=None)`
  * `get_ohlc(pair, timeframe, lookback)`
  * `get_ohlc_since(pair, timeframe, since_ts)`

  (All return closed candles only.)

* **Live data**

  * `get_latest_price(pair)`
  * `get_best_bid_ask(pair)`
  * `get_live_ohlc(pair, timeframe)`

* **Health**

  * `get_data_status()` (as described above).

This set is enough for Phase 3 (portfolio & PnL) and Phase 4 (strategy/risk) to operate without knowing about REST vs WS details.

---

### 8. Testing Expectations (pytest)

Phase 2 must come with a pytest suite that covers core behavior with mocks (no live network required by default).

**Required tests:**

1. **Universe building**

   * Given a mocked `AssetPairs` response, ensure:

     * Only USD (`default_quote`) spot pairs are included.
     * Margin pairs (with leverage fields) are excluded.
     * `status != "online"` pairs are excluded.
     * Config `universe.exclude_pairs` removes pairs.
     * Config `universe.include_pairs` adds pairs if present in AssetPairs.
     * Canonical naming and `PairMetadata` are correctly built (altname, wsname, raw_name, etc.).

2. **Liquidity filtering**

   * Given mocked ticker/volume data, ensure:

     * Pairs below `min_24h_volume_usd` are excluded.

3. **OHLC handling**

   * For mocked OHLC responses:

     * Only closed candles are returned by `get_ohlc`.
     * Running last candle is excluded.
     * Bars are deduplicated and ordered by timestamp.

4. **OHLC store backend**

   * File‑based backend:

     * `append_bars` then `get_bars` returns the same logical bars.
     * Handles multiple calls without duplication.

5. **WebSocket message handling (unit level)**

   * Given sample WS ticker and OHLC messages:

     * In‑memory caches update correctly.
     * `get_latest_price`, `get_best_bid_ask`, `get_live_ohlc` return expected values.
     * Staleness logic fires when `last_update_ts` is older than `stale_tolerance_seconds`.

6. **Rate limiting**

   * Given a burst of REST calls in tests:

     * Throttler enforces the configured max rate.

**Optional (nice‑to‑have):**

* Integration tests that hit live Kraken (guarded by env var / marker) to validate:

  * Universe building against real `AssetPairs`.
  * OHLC fetching against real `OHLC` responses.

---

### 9. Minimal Project Structure Additions

Phase 2 adds to the existing Phase 1 structure:

```text
src/
  kraken_trader/
    connection/              # Phase 1
    secrets.py               # Phase 1
    config.py                # Phase 1

    market_data/             # Phase 2
      __init__.py
      universe.py            # AssetPairs → PairMetadata + universe building
      ohlc_store.py          # OHLCStore interface + FileOHLCStore implementation
      ws_client.py           # WS v2 client + message handling
      api.py                 # High-level functions: get_universe, get_ohlc, get_latest_price, etc.

tests/
  test_universe.py
  test_ohlc_store.py
  test_ws_handling.py
  test_rate_limit.py
```

Names can vary slightly, but responsibilities should match this layout.

---

### 10. Phase 2 Acceptance Checklist

Phase 2 is considered complete when:

* [ ] The module builds a USD spot **universe** from `AssetPairs` with region and config overrides applied.
* [ ] Each universe entry has complete `PairMetadata` with canonical, REST, WS symbols, and trading parameters.
* [ ] Historical OHLC can be backfilled and retrieved via the OHLC store, returning **closed candles only**.
* [ ] The WebSocket v2 client streams ticker + OHLC data, updates in‑memory caches, and recovers from disconnects.
* [ ] Live data APIs respect `stale_tolerance_seconds` and clearly signal stale or missing data.
* [ ] A small, well‑defined public API is exposed: universe, metadata, OHLC, live prices, and data status.
* [ ] The pytest suite passes and covers universe filtering, OHLC loading, WS message handling, and rate limiting.


## Phase 3

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


## Phase 4

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


## Phase 5

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

With this, Phase 5 gives Krakked a real OMS: Phase 4 decides what to do, Phase 5 ensures it’s done safely and consistently on Kraken — and that you can always look back and understand what the bot actually tried to do.


## Phase 6

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


## Phase 7

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
	•	Lives in the config dir (Phase 1/3), e.g. ~/.krakked/krakked.db.
	•	Phase 7 must:
	•	Document where it is,
	•	Provide a simple maintenance script / doc for:
	•	Viewing tables,
	•	Checking integrity (PRAGMA integrity_check).

6.2 Backups

For live/paper environments:
	•	Daily backup of the DB file recommended:
	•	E.g. copy krakked.db to krakked.db.YYYYMMDD.bak.
	•	Optionally compress and rotate:
	•	Keep last N backups (7/30).

Phase 7 defines:
	•	A small backup_db() utility (Python function/CLI entrypoint),
	•	Or a documented pattern for external cron to call a script.

6.3 Log retention
	•	Logs can be:
	•	Rotated by logrotate or a similar system,
	•	Or written to daily log files krakked-YYYYMMDD.log.

Phase 7 should:
	•	Ensure logs are not kept unbounded.
	•	Document recommended rotation (e.g. 100MB per file, 7 days retained).

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


