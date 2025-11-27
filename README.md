# Krakked


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


Here you go — a Phase 2 design contract you can drop straight into a README or GitHub issue, mirroring the Phase 1 style.

---

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
   * No margin leverage:

     * `leverage_buy` and `leverage_sell` arrays are empty.
   * Respect `RegionProfile`:

     * If `supports_margin == False`, do **not** include margin‑only products, even if present.
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

You can paste this directly into a `docs/PHASE2_MARKET_DATA.md` or a GitHub issue and hand it to whoever’s implementing Phase 2.

