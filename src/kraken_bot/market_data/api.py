# src/kraken_bot/market_data/api.py

import time
import logging
from pathlib import Path
from typing import List, Dict, Optional, Any, Tuple
from kraken_bot.config import AppConfig, PairMetadata, OHLCBar
from kraken_bot.connection.rest_client import KrakenRESTClient
from kraken_bot.market_data.universe import build_universe
from kraken_bot.market_data.ohlc_store import OHLCStore, FileOHLCStore
from kraken_bot.market_data.ohlc_fetcher import backfill_ohlc
from kraken_bot.market_data.ws_client import KrakenWSClientV2
from kraken_bot.market_data.metadata_store import PairMetadataStore
from kraken_bot.market_data.exceptions import PairNotFoundError, DataStaleError

logger = logging.getLogger(__name__)

class MarketDataAPI:
    """
    The main public interface for the market data module.
    """
    def __init__(self, config: AppConfig):
        self._config = config
        self._rest_client = KrakenRESTClient()
        self._ohlc_store: OHLCStore = FileOHLCStore(config.market_data)
        metadata_path = (
            Path(config.market_data.metadata_path).expanduser()
            if config.market_data.metadata_path
            else None
        )
        self._metadata_store = PairMetadataStore(metadata_path)

        self._universe: List[PairMetadata] = []
        self._universe_map: Dict[str, PairMetadata] = {}

        self._ws_client: Optional[KrakenWSClientV2] = None
        self._ws_stale_tolerance = config.market_data.ws.get("stale_tolerance_seconds", 60)

    def initialize(self, backfill: bool = True):
        """
        Initializes the market data service: builds the universe, starts the WebSocket
        client, and optionally backfills historical data.
        """
        logger.info("Initializing MarketDataAPI...")
        # 1. Build the pair universe
        self.refresh_universe()

        # 2. Start the WebSocket client
        if self._universe:
            self._ws_client = KrakenWSClientV2(self._universe, timeframes=self._config.market_data.ws_timeframes)
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
                        store=self._ohlc_store
                    )
        logger.info("MarketDataAPI initialized.")

    def shutdown(self):
        """Gracefully shuts down the WebSocket client."""
        if self._ws_client:
            self._ws_client.stop()
            logger.info("MarketDataAPI shutdown complete.")

    def refresh_universe(self):
        """Re-fetches the asset pairs and rebuilds the universe."""
        self._universe = build_universe(
            self._rest_client,
            self._config.region,
            self._config.universe
        )

        if self._universe:
            self._metadata_store.save(self._universe)
        else:
            cached = self._metadata_store.load()
            if cached:
                logger.warning("Falling back to cached pair metadata due to empty universe response.")
                self._universe = cached

        self._universe_map = {p.canonical: p for p in self._universe}
        logger.info(f"Universe refreshed. Contains {len(self._universe)} pairs.")

    def get_universe(self) -> List[str]:
        """Returns the canonical symbols for all pairs in the universe."""
        return [p.canonical for p in self._universe]

    def get_universe_metadata(self) -> List[PairMetadata]:
        """Returns the full metadata objects for all pairs in the universe."""
        return self._universe

    def get_pair_metadata(self, pair: str) -> PairMetadata:
        if pair not in self._universe_map:
            raise PairNotFoundError(pair)
        return self._universe_map[pair]

    def get_ohlc(self, pair: str, timeframe: str, lookback: int) -> List[OHLCBar]:
        if pair not in self._universe_map:
            raise PairNotFoundError(pair)
        return self._ohlc_store.get_bars(pair, timeframe, lookback)

    def get_ohlc_since(self, pair: str, timeframe: str, since_ts: int) -> List[OHLCBar]:
        if pair not in self._universe_map:
            raise PairNotFoundError(pair)
        return self._ohlc_store.get_bars_since(pair, timeframe, since_ts)

    def backfill_ohlc(self, pair: str, timeframe: str, since: Optional[int] = None) -> int:
        """
        Backfills historical OHLC data for the given pair and timeframe.
        Returns the number of bars fetched.
        """
        if pair not in self._universe_map:
            raise PairNotFoundError(pair)

        pair_meta = self._universe_map[pair]
        return backfill_ohlc(
            pair_metadata=pair_meta,
            timeframe=timeframe,
            since=since,
            client=self._rest_client,
            store=self._ohlc_store
        )

    def _ticker_freshness(self, pair: str) -> Tuple[bool, float]:
        """
        Returns whether ticker data for the pair is fresh alongside the current
        staleness value (monotonic seconds since last update). A missing client
        or missing update yields a staleness of -1.
        """
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
        is_fresh, stale_time = self._ticker_freshness(pair)
        if not is_fresh:
            raise DataStaleError(pair, stale_time, self._ws_stale_tolerance)

    def _check_ohlc_staleness(self, pair: str, timeframe: str):
        if not self._ws_client:
            raise DataStaleError(pair, -1, self._ws_stale_tolerance) # No client running

        last_update = self._ws_client.last_ohlc_update_ts.get(pair, {}).get(timeframe)
        if not last_update:
            raise DataStaleError(pair, -1, self._ws_stale_tolerance) # No updates yet

        stale_time = time.monotonic() - last_update
        if stale_time > self._ws_stale_tolerance:
            raise DataStaleError(pair, stale_time, self._ws_stale_tolerance)

    def _get_fallback_timeframes(self) -> List[str]:
        """Combines configured timeframes for fallback lookups without duplicates."""
        timeframes: List[str] = []
        seen = set()
        for tf in self._config.market_data.ws_timeframes + self._config.market_data.backfill_timeframes:
            if tf not in seen:
                seen.add(tf)
                timeframes.append(tf)
        return timeframes

    def _get_cached_price_from_store(self, pair: str) -> Optional[float]:
        for timeframe in self._get_fallback_timeframes():
            bars = self._ohlc_store.get_bars(pair, timeframe, 1)
            if bars:
                return bars[-1].close
        return None

    def _get_rest_ticker_price(self, pair: str) -> Optional[float]:
        try:
            result = self._rest_client.get_public("Ticker", params={"pair": pair})
        except Exception as exc:
            logger.warning("REST ticker fallback failed for %s: %s", pair, exc)
            return None

        if not result:
            return None

        ticker_values = next(iter(result.values()), None)
        if not ticker_values:
            return None

        bid = ticker_values.get("b", [None])[0] if isinstance(ticker_values.get("b"), list) else None
        ask = ticker_values.get("a", [None])[0] if isinstance(ticker_values.get("a"), list) else None
        last_trade = ticker_values.get("c", [None])[0] if isinstance(ticker_values.get("c"), list) else None

        try:
            if bid is not None and ask is not None:
                return (float(bid) + float(ask)) / 2
            if last_trade is not None:
                return float(last_trade)
        except (TypeError, ValueError):
            logger.warning("Unexpected ticker payload for %s: %s", pair, ticker_values)
            return None

        return None

    def get_latest_price(self, pair: str) -> Optional[float]:
        is_fresh, stale_time = self._ticker_freshness(pair)

        if is_fresh:
            ticker = self._ws_client.ticker_cache.get(pair)
            if ticker:
                # Return mid-price (avg of best bid and ask)
                return (float(ticker["bid"]) + float(ticker["ask"])) / 2

        fallback_price = self._get_cached_price_from_store(pair)
        if fallback_price is None:
            fallback_price = self._get_rest_ticker_price(pair)

        if fallback_price is not None:
            return fallback_price

        raise DataStaleError(pair, stale_time, self._ws_stale_tolerance)

    def get_best_bid_ask(self, pair: str) -> Optional[Dict[str, float]]:
        self._check_ticker_staleness(pair)
        ticker = self._ws_client.ticker_cache.get(pair)
        if ticker:
            return {"bid": float(ticker["bid"]), "ask": float(ticker["ask"])}
        return None

    def get_live_ohlc(self, pair: str, timeframe: str) -> Optional[OHLCBar]:
        self._check_ohlc_staleness(pair, timeframe)
        ohlc_data = self._ws_client.ohlc_cache.get(pair, {}).get(timeframe)
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

    def get_data_status(self) -> "ConnectionStatus":
        from kraken_bot.config import ConnectionStatus

        # 1. Check REST API reachability
        rest_ok = False
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

    def get_subscription_status(self) -> Dict[str, Dict[str, Dict[str, Any]]]:
        """Exposes the current WebSocket subscription status map."""
        if not self._ws_client:
            return {}
        return self._ws_client.subscription_status
