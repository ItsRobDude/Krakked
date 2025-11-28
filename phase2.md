
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

