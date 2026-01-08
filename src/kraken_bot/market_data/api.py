# src/kraken_bot/market_data/api.py

import logging
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from kraken_bot.config import AppConfig
from kraken_bot.connection.rate_limiter import RateLimiter
from kraken_bot.connection.rest_client import KrakenRESTClient
from kraken_bot.market_data.exceptions import DataStaleError, PairNotFoundError
from kraken_bot.market_data.metadata_store import PairMetadataStore
from kraken_bot.market_data.models import ConnectionStatus, OHLCBar, PairMetadata
from kraken_bot.market_data.ohlc_fetcher import backfill_ohlc
from kraken_bot.market_data.ohlc_store import FileOHLCStore, OHLCStore
from kraken_bot.market_data.universe import build_universe
from kraken_bot.market_data.ws_client import KrakenWSClientV2

logger = logging.getLogger(__name__)

# Common asset aliases for human-friendly inputs
ASSET_ALIASES = {
    "BTC": "XBT",
    "DOGE": "XDG",
    "ZUSD": "USD",
}


@dataclass
class MarketDataStatus:
    """Aggregated health indicator for market data streams."""

    health: str  # healthy | stale | unavailable
    max_staleness: Optional[float] = None
    reason: Optional[str] = None
    stale_pairs: Optional[List[str]] = None


def validate_pairs_with_client(client: KrakenRESTClient, pairs: List[str]) -> List[str]:
    """
    Validates a list of pair names against Kraken's asset pairs.
    Returns a list of invalid pairs.
    Raises exception if validation cannot be performed (e.g. API unavailable).
    """
    try:
        # We fetch all asset pairs to check existence
        resp = client.get_public("AssetPairs")
        if not resp:
            from kraken_bot.connection.exceptions import ServiceUnavailableError

            raise ServiceUnavailableError("Empty response from AssetPairs")

        known_pairs = resp
        if "result" in resp:
            known_pairs = resp["result"]

        known_keys = set(known_pairs.keys())
        known_altnames = {
            v.get("altname") for v in known_pairs.values() if isinstance(v, dict)
        }

        invalid = []
        for pair in pairs:
            if pair not in known_keys and pair not in known_altnames:
                slashless = pair.replace("/", "")
                if slashless not in known_keys and slashless not in known_altnames:
                    invalid.append(pair)
        return invalid

    except Exception as e:
        logger.error(f"Error validating universe pairs: {e}")
        # Fail closed - re-raise so caller knows we couldn't validate
        # We wrap in ServiceUnavailableError if it's not already one of our types
        from kraken_bot.connection.exceptions import (
            KrakenAPIError,
            ServiceUnavailableError,
        )

        if isinstance(e, KrakenAPIError):
            raise e
        raise ServiceUnavailableError(f"Validation unavailable: {e}") from e


class MarketDataAPI:
    """
    The main public interface for the market data module.
    """

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.shutdown()

    def __del__(self):
        try:
            ws_client = getattr(self, "_ws_client", None)
            if ws_client and getattr(ws_client, "_running", False):
                self.shutdown()
        except Exception:
            # Avoid raising exceptions during garbage collection
            pass

    def __init__(
        self,
        config: AppConfig,
        rest_client: Optional[KrakenRESTClient] = None,
        rate_limiter: Optional[RateLimiter] = None,
    ):
        self._config = config
        self._rest_client: Optional[KrakenRESTClient] = rest_client or KrakenRESTClient(
            rate_limiter=rate_limiter
        )
        self._ohlc_store: OHLCStore = FileOHLCStore(config.market_data)
        metadata_path = (
            Path(config.market_data.metadata_path).expanduser()
            if config.market_data.metadata_path
            else None
        )
        self._metadata_store = PairMetadataStore(metadata_path)

        self._universe: List[PairMetadata] = []
        self._universe_map: Dict[str, PairMetadata] = {}
        self._alias_map: Dict[str, PairMetadata] = {}
        self._asset_map: Dict[str, str] = {}  # raw_asset -> canonical_base
        self._valuation_map: Dict[str, str] = {}  # canonical_base -> canonical_pair

        self._ws_client: Optional[KrakenWSClientV2] = None
        self._ws_stale_tolerance = config.market_data.ws.get(
            "stale_tolerance_seconds", 60
        )

        # Instance-specific cache for normalize_pair to avoid global state
        # and support proper cache clearing per instance.
        self._normalize_pair_cached = lru_cache(maxsize=2048)(
            self._normalize_pair_logic
        )

        self._normalize_asset_cached = lru_cache(maxsize=2048)(
            self._normalize_asset_logic
        )

    def initialize(self, backfill: bool = True):
        """
        Initializes the market data service: builds the universe, starts the WebSocket
        client, and optionally backfills historical data.
        """
        logger.info("Initializing MarketDataAPI...")
        assert self._rest_client is not None
        # 1. Build the pair universe
        self.refresh_universe()

        # 2. Start the WebSocket client
        if self._universe:
            self._ws_client = KrakenWSClientV2(
                self._universe,
                timeframes=self._config.market_data.ws_timeframes,
                on_candle_closed=self._on_candle_closed,
            )
            self._ws_client.start()
            logger.info("WebSocket client started.")
        else:
            logger.warning("No pairs in universe, WebSocket client not started.")

        # 3. Backfill historical data
        if backfill:
            for pair_meta in self._universe:
                for timeframe in self._config.market_data.backfill_timeframes:
                    backfill_ohlc(
                        pair_metadata=pair_meta,
                        timeframe=timeframe,
                        client=self._rest_client,
                        store=self._ohlc_store,
                    )
        logger.info("MarketDataAPI initialized.")

    def _on_candle_closed(
        self, pair: str, timeframe: str, candle_data: Dict[str, Any]
    ) -> None:
        """Callback to persist a closed candle from the WS stream."""
        try:
            bar = OHLCBar(
                timestamp=int(float(candle_data["timestamp"])),
                open=float(candle_data["open"]),
                high=float(candle_data["high"]),
                low=float(candle_data["low"]),
                close=float(candle_data["close"]),
                volume=float(candle_data["volume"]),
            )
            # Append to persistent store
            self._ohlc_store.append_bars(pair, timeframe, [bar])
            logger.debug(f"Persisted closed candle for {pair} {timeframe}")
        except Exception as exc:
            logger.error(
                f"Failed to persist closed candle for {pair} {timeframe}: {exc}"
            )

    def shutdown(self):
        """Gracefully shuts down the WebSocket client."""
        if self._ws_client:
            self._ws_client.stop()

        # Shutdown OHLC store worker if supported
        shutdown_store = getattr(self._ohlc_store, "shutdown", None)
        if callable(shutdown_store):
            shutdown_store()

        logger.info("MarketDataAPI shutdown complete.")

    def refresh_universe(self):
        """Re-fetches the asset pairs and rebuilds the universe."""
        # Clear the cache to prevent stale mappings persist across universe updates
        self._normalize_pair_cached.cache_clear()
        self._normalize_asset_cached.cache_clear()

        assert self._rest_client is not None
        self._universe = build_universe(
            self._rest_client, self._config.region, self._config.universe
        )

        if self._universe:
            self._metadata_store.save(self._universe)
        else:
            cached = self._metadata_store.load()
            if cached:
                logger.warning(
                    "Falling back to cached pair metadata due to empty universe response."
                )
                self._universe = cached

        self._universe_map = {p.canonical: p for p in self._universe}

        # Build dynamic alias map and asset maps
        self._alias_map = {}
        self._asset_map = {}
        self._valuation_map = {}

        # Pre-seed common assets to ensure they exist even if not in universe
        # This is a safe fallback for base assets (like ZUSD/USD)
        self._asset_map["ZUSD"] = "USD"
        self._asset_map["USD"] = "USD"
        self._asset_map["XBT"] = "XBT"
        self._asset_map["XXBT"] = "XBT"

        for p in self._universe:
            # Add standard identifiers
            self._alias_map[p.canonical] = p
            self._alias_map[p.raw_name] = p
            self._alias_map[p.rest_symbol] = p
            if p.ws_symbol:
                self._alias_map[p.ws_symbol] = p

            # Add slash-less variants (e.g., XBT/USD -> XBTUSD) if applicable
            slashless_ws = p.ws_symbol.replace("/", "") if p.ws_symbol else ""
            if slashless_ws:
                self._alias_map[slashless_ws] = p

            slashless_rest = p.rest_symbol.replace("/", "") if p.rest_symbol else ""
            if slashless_rest:
                self._alias_map[slashless_rest] = p

            # --- Asset Identity Resolution ---
            # Attempt to derive canonical base/quote from ws_symbol (e.g., "XBT/USD")
            # This is more reliable than raw pair string parsing.
            canonical_base = ""
            if p.ws_symbol and "/" in p.ws_symbol:
                parts = p.ws_symbol.split("/")
                if len(parts) == 2:
                    canonical_base = parts[0]
                    # Map raw base asset code (from 'base' field in API) to canonical base
                    if p.base:
                        self._asset_map[p.base] = canonical_base
                    # Ensure identity mapping for the canonical base itself
                    self._asset_map[canonical_base] = canonical_base

            # Fallback if we couldn't derive from ws_symbol, use what we have if available
            if not canonical_base and p.base:
                # If we can't be sure, we map raw to itself, but hopefully alias map covers it
                if p.base not in self._asset_map:
                    # Strip X/Z prefix heuristic only as a last resort if not in alias?
                    # No, user explicitly said NOT to do string surgery.
                    # If we don't have a ws_symbol with slash, we might check ASSET_ALIASES
                    # or just treat the raw base as canonical if it looks reasonable.
                    # But for now, let's assume ws_symbol is robust for USD pairs.
                    pass

        # --- Valuation Mapping ---
        # Two-pass approach to prioritize "clean" spot pairs over marked ones (e.g. .M, .F)
        # Pass 1: Clean pairs
        for p in self._universe:
            if p.quote != "USD":
                continue

            # Check for markers in the canonical name or ws_symbol
            is_clean = "." not in p.canonical and (
                not p.ws_symbol or "." not in p.ws_symbol
            )
            if not is_clean:
                continue

            # Resolve canonical base asset
            canonical_base = ""
            if p.ws_symbol and "/" in p.ws_symbol:
                parts = p.ws_symbol.split("/")
                canonical_base = parts[0]

            target_asset = canonical_base or p.base
            if target_asset:
                self._valuation_map[target_asset] = p.canonical
                # Also map the raw asset if different
                if p.base and p.base != target_asset:
                    self._valuation_map[p.base] = p.canonical

        # Pass 2: Marked pairs (fill gaps only)
        for p in self._universe:
            if p.quote != "USD":
                continue

            # Resolve canonical base asset
            canonical_base = ""
            if p.ws_symbol and "/" in p.ws_symbol:
                parts = p.ws_symbol.split("/")
                canonical_base = parts[0]

            target_asset = canonical_base or p.base
            if target_asset and target_asset not in self._valuation_map:
                self._valuation_map[target_asset] = p.canonical

            if p.base and p.base not in self._valuation_map:
                self._valuation_map[p.base] = p.canonical

        # Explicitly ensure USD values to itself (identity)
        self._valuation_map["USD"] = "USD"  # Special case: USD is valued as 1.0 USD

        logger.info(
            f"Universe refreshed. Contains {len(self._universe)} pairs. "
            f"Mapped {len(self._asset_map)} assets."
        )

    def normalize_pair(self, pair: str) -> str:
        """
        Normalize a pair string (e.g., 'BTC/USD', 'XBTUSD') to its canonical form (e.g., 'XBTUSD').
        Uses a dynamic alias index and falls back to asset aliasing for robustness.
        """
        return self._normalize_pair_cached(pair.strip().upper())

    def _normalize_pair_logic(self, pair: str) -> str:
        """Internal logic for normalize_pair, wrapped by LRU cache."""
        # Note: pair is assumed to be stripped and upper-cased by the caller (normalize_pair)
        # to ensure efficient caching key usage.

        # 1. Direct lookup in alias map
        if pair in self._alias_map:
            return self._alias_map[pair].canonical

        # 2. Try removing slashes
        slashless = pair.replace("/", "")
        if slashless in self._alias_map:
            return self._alias_map[slashless].canonical

        # 3. Apply asset aliases (human-friendly -> Kraken canonical)
        # Split pair (assuming / separator or 3-char split if no slash?)
        # Kraken pairs can be messy. If there is a slash, it's easy.
        if "/" in pair:
            base, quote = pair.split("/", 1)
            base = ASSET_ALIASES.get(base, base)
            quote = ASSET_ALIASES.get(quote, quote)
            # Try reconstructed with slash and without
            candidates = [f"{base}/{quote}", f"{base}{quote}"]
            for c in candidates:
                if c in self._alias_map:
                    return self._alias_map[c].canonical
        else:
            # Heuristic: if starts with alias key
            for alias, target in ASSET_ALIASES.items():
                if pair.startswith(alias):
                    # Replace prefix
                    replaced = target + pair[len(alias) :]
                    if replaced in self._alias_map:
                        return self._alias_map[replaced].canonical

        # 4. Return original if resolution fails (caller will likely fail on lookup)
        return pair

    def normalize_asset(self, asset: str) -> str:
        """
        Normalize an asset code (e.g., 'XXBT', 'ZUSD') to its canonical human-readable form (e.g., 'XBT', 'USD').
        Uses the universe-derived mapping to avoid unsafe string stripping.
        """
        return self._normalize_asset_cached(asset)

    def _normalize_asset_logic(self, asset: str) -> str:
        asset_clean = asset.strip().upper()
        # Direct map lookup
        if asset_clean in self._asset_map:
            return self._asset_map[asset_clean]

        # Note: We intentionally do NOT use ASSET_ALIASES here to avoid flip-flopping
        # (e.g. DOGE <-> XDG). Asset normalization should settle on one stable
        # representation derived from the universe.

        # Fallback: return as-is (better than guessing incorrectly)
        return asset_clean

    def get_valuation_pair(self, asset: str) -> Optional[str]:
        """
        Return the canonical USD pair used to value the given asset.
        Returns None if no valuation pair exists in the universe.
        """
        # Normalize first to ensure we look up the canonical asset
        canonical_asset = self.normalize_asset(asset)

        # Check map
        if canonical_asset in self._valuation_map:
            return self._valuation_map[canonical_asset]

        # Try lookup by raw asset just in case
        if asset in self._valuation_map:
            return self._valuation_map[asset]

        return None

    def get_universe(self) -> List[str]:
        """Returns the canonical symbols for all pairs in the universe."""
        return [p.canonical for p in self._universe]

    def get_universe_metadata(self) -> List[PairMetadata]:
        """Returns the full metadata objects for all pairs in the universe."""
        return self._universe

    def get_pair_metadata(self, pair: str) -> PairMetadata:
        canonical = self.normalize_pair(pair)
        if canonical not in self._universe_map:
            raise PairNotFoundError(pair)
        return self._universe_map[canonical]

    def get_pair_metadata_or_raise(self, pair: str) -> PairMetadata:
        """Return metadata for ``pair`` or raise a :class:`ValueError` if missing."""

        try:
            metadata = self.get_pair_metadata(pair)
        except PairNotFoundError as exc:
            raise ValueError(f"Missing PairMetadata for pair={pair}") from exc

        if metadata is None:
            raise ValueError(f"Missing PairMetadata for pair={pair}")

        return metadata

    def get_ohlc(self, pair: str, timeframe: str, lookback: int) -> List[OHLCBar]:
        canonical = self.normalize_pair(pair)
        if canonical not in self._universe_map:
            raise PairNotFoundError(pair)
        return self._ohlc_store.get_bars(canonical, timeframe, lookback)

    def get_ohlc_since(self, pair: str, timeframe: str, since_ts: int) -> List[OHLCBar]:
        canonical = self.normalize_pair(pair)
        if canonical not in self._universe_map:
            raise PairNotFoundError(pair)
        return self._ohlc_store.get_bars_since(canonical, timeframe, since_ts)

    def backfill_ohlc(
        self, pair: str, timeframe: str, since: Optional[int] = None
    ) -> int:
        """
        Backfills historical OHLC data for the given pair and timeframe.
        Returns the number of bars fetched.
        """
        canonical = self.normalize_pair(pair)
        if canonical not in self._universe_map:
            raise PairNotFoundError(pair)

        pair_meta = self._universe_map[canonical]
        return backfill_ohlc(
            pair_metadata=pair_meta,
            timeframe=timeframe,
            since=since,
            client=self._rest_client,
            store=self._ohlc_store,
        )

    def _ticker_freshness(self, pair: str) -> Tuple[bool, float]:
        """
        Returns whether ticker data for the pair is fresh alongside the current
        staleness value (monotonic seconds since last update). A missing client
        or missing update yields a staleness of -1.
        """
        # Note: pair should be canonical here, but we rely on public methods to normalize
        if not self._ws_client:
            return False, -1

        last_update = self._ws_client.last_ticker_update_ts.get(pair)
        if not last_update:
            return False, -1

        stale_time = time.monotonic() - last_update
        if stale_time > self._ws_stale_tolerance:
            return False, stale_time

        return True, stale_time

    def _check_ticker_staleness(self, pair: str):
        # pair is expected to be canonical when calling this internal helper from normalized public methods
        is_fresh, stale_time = self._ticker_freshness(pair)
        if not is_fresh:
            raise DataStaleError(pair, stale_time, self._ws_stale_tolerance)

    def _check_ohlc_staleness(self, pair: str, timeframe: str):
        # pair is expected to be canonical
        if not self._ws_client:
            raise DataStaleError(
                pair, -1, self._ws_stale_tolerance
            )  # No client running

        last_update = self._ws_client.last_ohlc_update_ts.get(pair, {}).get(timeframe)
        if not last_update:
            raise DataStaleError(pair, -1, self._ws_stale_tolerance)  # No updates yet

        stale_time = time.monotonic() - last_update
        if stale_time > self._ws_stale_tolerance:
            raise DataStaleError(pair, stale_time, self._ws_stale_tolerance)

    def _get_rest_ticker_price(self, pair: str) -> Optional[float]:
        """Fetch fallback price via REST Ticker (Mid-price or Last)."""
        # pair is expected to be canonical
        assert self._rest_client is not None
        try:
            result = self._rest_client.get_public("Ticker", params={"pair": pair})
        except Exception as exc:
            logger.warning("REST ticker fallback failed for %s: %s", pair, exc)
            return None

        # The key in result depends on what we passed. If we passed XBTUSD, we get XBTUSD or XXBTZUSD.
        # Grab the first value regardless of key to match flexibly.
        ticker_data = next(iter(result.values())) if result else None
        if not ticker_data:
            return None

        def _get_val(key: str) -> Optional[float]:
            """Safely extract float from list fields like 'b': ['50000.0', '1', '1.0']"""
            try:
                val_list = ticker_data.get(key)
                return float(val_list[0]) if val_list and len(val_list) > 0 else None
            except (ValueError, TypeError, IndexError, AttributeError):
                return None

        bid, ask = _get_val("b"), _get_val("a")
        if bid is not None and ask is not None:
            return (bid + ask) / 2

        return _get_val("c")

    def get_latest_price(self, pair: str) -> Optional[float]:
        canonical = self.normalize_pair(pair)

        # Special case: identity valuation
        if canonical == "USD":
            return 1.0

        is_fresh, stale_time = self._ticker_freshness(canonical)

        if is_fresh and self._ws_client:
            ticker = self._ws_client.ticker_cache.get(canonical)
            if ticker:
                # Return mid-price (avg of best bid and ask)
                return (float(ticker["bid"]) + float(ticker["ask"])) / 2

        # Fallback to REST ticker
        fallback_price = self._get_rest_ticker_price(canonical)
        if fallback_price is not None:
            return fallback_price

        raise DataStaleError(canonical, stale_time, self._ws_stale_tolerance)

    def get_best_bid_ask(self, pair: str) -> Optional[Dict[str, float]]:
        canonical = self.normalize_pair(pair)
        self._check_ticker_staleness(canonical)
        if not self._ws_client:
            return None
        ticker = self._ws_client.ticker_cache.get(canonical)
        if ticker:
            return {"bid": float(ticker["bid"]), "ask": float(ticker["ask"])}
        return None

    def get_live_ohlc(self, pair: str, timeframe: str) -> Optional[OHLCBar]:
        canonical = self.normalize_pair(pair)
        self._check_ohlc_staleness(canonical, timeframe)
        assert self._ws_client is not None
        ohlc_data = self._ws_client.ohlc_cache.get(canonical, {}).get(timeframe)
        if ohlc_data:
            return OHLCBar(
                timestamp=int(float(ohlc_data["timestamp"])),
                open=float(ohlc_data["open"]),
                high=float(ohlc_data["high"]),
                low=float(ohlc_data["low"]),
                close=float(ohlc_data["close"]),
                volume=float(ohlc_data["volume"]),
            )
        return None

    def get_data_status(self) -> ConnectionStatus:
        from kraken_bot.config import ConnectionStatus

        # 1. Check REST API reachability
        rest_ok = False
        assert self._rest_client is not None
        try:
            # A lightweight endpoint to check connectivity
            self._rest_client.get_public("SystemStatus")
            rest_ok = True
        except Exception:
            rest_ok = False

        # 2. Check WebSocket status
        ws_connected = self._ws_client.is_connected if self._ws_client else False

        streaming_count = 0
        stale_count = 0
        subscription_errors = 0
        if ws_connected:
            for pair_meta in self._universe:
                try:
                    self._check_ticker_staleness(pair_meta.canonical)
                    streaming_count += 1
                except DataStaleError:
                    stale_count += 1
        else:
            stale_count = len(self._universe)

        if self._ws_client:
            for pair_status in self._ws_client.subscription_status.values():
                for status_record in pair_status.values():
                    if status_record.get("status") != "subscribed":
                        subscription_errors += 1

        return ConnectionStatus(
            rest_api_reachable=rest_ok,
            websocket_connected=ws_connected,
            streaming_pairs=streaming_count,
            stale_pairs=stale_count,
            subscription_errors=subscription_errors,
        )

    def get_health_status(self) -> MarketDataStatus:
        """Summarize market data freshness into a simple health indicator."""

        try:
            connection_status = self.get_data_status()
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.error("Failed to fetch market data connection status: %s", exc)
            return MarketDataStatus(health="unavailable", reason=str(exc))

        if not connection_status.rest_api_reachable:
            return MarketDataStatus(health="unavailable", reason="rest_unreachable")
        if not connection_status.websocket_connected:
            return MarketDataStatus(
                health="unavailable", reason="websocket_disconnected"
            )

        max_staleness: Optional[float] = None
        stale_detected = False
        stale_pairs: List[str] = []

        for pair_meta in self._universe:
            is_fresh, stale_time = self._ticker_freshness(pair_meta.canonical)
            if stale_time >= 0:
                max_staleness = (
                    stale_time
                    if max_staleness is None
                    else max(max_staleness, stale_time)
                )
            if not is_fresh:
                stale_detected = True
                stale_pairs.append(pair_meta.canonical)

        if stale_detected:
            return MarketDataStatus(
                health="stale",
                max_staleness=max_staleness,
                reason="data_stale",
                stale_pairs=stale_pairs,
            )

        return MarketDataStatus(health="healthy", max_staleness=max_staleness or 0.0)

    def get_subscription_status(self) -> Dict[str, Dict[str, Dict[str, Any]]]:
        """Exposes the current WebSocket subscription status map."""
        if not self._ws_client:
            return {}
        return self._ws_client.subscription_status

    def validate_pairs(self, pairs: List[str]) -> List[str]:
        """
        Validates a list of pair names against Kraken's asset pairs.
        Returns a list of invalid pairs.
        Raises exception if validation cannot be performed (e.g. API unavailable).
        """
        if not self._rest_client:
            from kraken_bot.connection.exceptions import ServiceUnavailableError

            raise ServiceUnavailableError("No REST client available for validation")

        return validate_pairs_with_client(self._rest_client, pairs)
