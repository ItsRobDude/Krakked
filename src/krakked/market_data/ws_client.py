# src/krakked/market_data/ws_client.py

import asyncio
import json
import logging
import threading
import time
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed
from websockets.protocol import State

from krakked.config import PairMetadata

logger = logging.getLogger(__name__)

KRAKEN_WS_V2_URL = "wss://ws.kraken.com/v2"
WS_SYMBOL_ALIASES = {
    "BTC": "XBT",
    "XBT": "BTC",
    "DOGE": "XDG",
    "XDG": "DOGE",
    "USD": "ZUSD",
    "ZUSD": "USD",
}


class KrakenWSClientV2:
    """
    Handles the connection to the Kraken WebSocket API v2, subscribes to channels,
    and maintains an in-memory cache of the latest market data.
    """

    def __init__(
        self,
        pairs: List[PairMetadata],
        timeframes: List[str] = ["1m"],
        on_candle_closed: Optional[Callable[[str, str, Dict[str, Any]], None]] = None,
    ):
        self._url = KRAKEN_WS_V2_URL
        self._pairs = pairs
        self._ws_symbols = [p.ws_symbol for p in self._pairs]
        self._canonical_by_ws_symbol: Dict[str, str] = {}
        self._timeframes = timeframes
        self._live_ohlc_timeframes = timeframes[:1]
        self._on_candle_closed = on_candle_closed
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._main_task: Optional[asyncio.Task] = None
        self._websocket: Optional[ClientConnection] = None

        # In-memory cache
        self.last_ticker_update_ts: Dict[str, float] = defaultdict(float)
        self.last_ohlc_update_ts: Dict[str, Dict[str, float]] = defaultdict(dict)
        self.ticker_cache: Dict[str, Dict[str, Any]] = {}
        # ohlc_cache structure: {pair: {timeframe: latest_ohlc_data_dict}}
        self.ohlc_cache: Dict[str, Dict[str, Any]] = {}

        # Track last closed timestamp to detect rollovers
        # {pair: {timeframe: last_seen_endtime_str}}
        self._last_candle_endtime: Dict[str, Dict[str, str]] = defaultdict(dict)

        self.subscription_status: Dict[str, Dict[str, Dict[str, Any]]] = defaultdict(
            dict
        )
        self._pending_subscriptions: Dict[int, Dict[str, Any]] = {}
        self._next_req_id = int(time.time() * 1000)
        for pair in self._pairs:
            for symbol_variant in self._iter_ws_symbol_variants(pair.ws_symbol):
                self._canonical_by_ws_symbol[symbol_variant] = pair.canonical

    def _allocate_req_id(self) -> int:
        self._next_req_id += 1
        return self._next_req_id

    @property
    def is_connected(self) -> bool:
        """Returns True if the WebSocket connection is open."""
        return (
            self._websocket is not None and self._websocket.state is State.OPEN
        )

    def start(self):
        """Starts the WebSocket client in a separate thread."""
        if self._running:
            logger.warning("WebSocket client is already running.")
            return

        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("WebSocket client started.")

    def stop(self):
        """Stops the WebSocket client."""
        self._running = False
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._request_shutdown)
        if self._thread and self._thread.is_alive():
            # Allow a short grace period for the background thread to exit after
            # cancellation is requested.
            self._thread.join(timeout=2)
        logger.info("WebSocket client stopped.")

    def _get_canonical_from_ws_symbol(self, ws_symbol: str) -> Optional[str]:
        for symbol_variant in self._iter_ws_symbol_variants(ws_symbol):
            canonical = self._canonical_by_ws_symbol.get(symbol_variant)
            if canonical:
                return canonical
        return None

    def _iter_ws_symbol_variants(self, ws_symbol: str) -> set[str]:
        variants = {ws_symbol}
        if "/" not in ws_symbol:
            return variants

        base, quote = ws_symbol.split("/", 1)
        alias_bases = {base, WS_SYMBOL_ALIASES.get(base, base)}
        alias_quotes = {quote, WS_SYMBOL_ALIASES.get(quote, quote)}
        for alias_base in alias_bases:
            for alias_quote in alias_quotes:
                variants.add(f"{alias_base}/{alias_quote}")
        return {variant for variant in variants if variant}

    async def _subscribe(self):
        """Subscribes to ticker and OHLC channels for all pairs."""
        if not self._websocket:
            return

        chunk_size = 50

        # Ticker subscription
        for i in range(0, len(self._ws_symbols), chunk_size):
            chunk = self._ws_symbols[i : i + chunk_size]
            req_id = self._allocate_req_id()
            self._pending_subscriptions[req_id] = {
                "channel": "ticker",
                "symbol": chunk,
            }
            ticker_sub = {
                "method": "subscribe",
                "params": {
                    "channel": "ticker",
                    "symbol": chunk,
                },
                "req_id": req_id,
            }
            await self._websocket.send(json.dumps(ticker_sub))
        logger.info(f"Subscribed to ticker for {len(self._ws_symbols)} pairs.")

        # OHLC subscriptions
        if len(self._timeframes) > 1:
            logger.info(
                "Kraken WS v2 accepts one live OHLC interval per symbol per connection; using %s for streaming and relying on persisted/backfilled bars for slower intervals.",
                self._live_ohlc_timeframes[0],
            )

        for tf in self._live_ohlc_timeframes:
            interval = (
                int(tf[:-1])
                if tf.endswith("m")
                else int(tf[:-1]) * 60 if tf.endswith("h") else int(tf[:-1]) * 1440
            )
            for i in range(0, len(self._ws_symbols), chunk_size):
                chunk = self._ws_symbols[i : i + chunk_size]
                req_id = self._allocate_req_id()
                self._pending_subscriptions[req_id] = {
                    "channel": "ohlc",
                    "symbol": chunk,
                    "interval": interval,
                }
                ohlc_sub = {
                    "method": "subscribe",
                    "params": {
                        "channel": "ohlc",
                        "symbol": chunk,
                        "interval": interval,
                    },
                    "req_id": req_id,
                }
                await self._websocket.send(json.dumps(ohlc_sub))
            logger.info(f"Subscribed to OHLC ({tf}) for {len(self._ws_symbols)} pairs.")

    def _get_timeframe_from_interval(self, interval: int) -> Optional[str]:
        """Maps a Kraken interval integer back to a timeframe string like '1m'."""
        # This is a reverse mapping of the one used in the OHLC fetcher
        interval_to_tf = {1: "1m", 5: "5m", 15: "15m", 60: "1h", 240: "4h", 1440: "1d"}
        return interval_to_tf.get(interval)

    def _record_subscription_status(
        self,
        *,
        channel: str,
        status: str,
        ws_symbols: Any,
        req_id: Any,
        error_message: Optional[str],
    ) -> None:
        if not isinstance(ws_symbols, list):
            ws_symbols = [ws_symbols]

        recorded_pairs = []
        display_names = []
        for ws_symbol in ws_symbols:
            canonical_pair = (
                self._get_canonical_from_ws_symbol(ws_symbol) if ws_symbol else None
            )
            pair_key = canonical_pair or ws_symbol or "unknown"
            status_record = {
                "status": status,
                "message": error_message,
                "req_id": req_id,
            }
            self.subscription_status[pair_key][channel] = status_record
            recorded_pairs.append(pair_key)
            display_names.append(ws_symbol or canonical_pair or "unknown")

        pair_display = ", ".join(display_names) if display_names else "unknown"
        if status == "subscribed":
            logger.info("Subscribed to %s for %s.", channel, pair_display)
        else:
            logger.error(
                "Subscription to %s for %s failed: %s",
                channel,
                pair_display,
                error_message or "unknown error",
            )

    def _extract_ws_symbol(self, data: dict[str, Any], payload: list[Any]) -> Optional[str]:
        ws_symbol = data.get("symbol")
        if isinstance(ws_symbol, str) and ws_symbol:
            return ws_symbol
        if payload and isinstance(payload[0], dict):
            nested_symbol = payload[0].get("symbol")
            if isinstance(nested_symbol, str) and nested_symbol:
                return nested_symbol
        return None

    def _extract_ohlc_interval(
        self, data: dict[str, Any], candle_data: dict[str, Any]
    ) -> Optional[int]:
        interval = data.get("interval")
        if isinstance(interval, int):
            return interval

        params_interval = data.get("params", {}).get("interval")
        if isinstance(params_interval, int):
            return params_interval

        candle_interval = candle_data.get("interval")
        if isinstance(candle_interval, int):
            return candle_interval

        return None

    def _extract_candle_marker(self, candle_data: dict[str, Any]) -> Optional[str]:
        for key in ("interval_begin", "endtime", "timestamp"):
            value = candle_data.get(key)
            if value is not None:
                return str(value)
        return None

    async def _handle_message(self, message: str):
        """Parses an incoming message and updates the cache."""
        data = json.loads(message)

        if data.get("method") == "subscribe":
            req_id = data.get("req_id")
            pending = (
                self._pending_subscriptions.pop(req_id, {})
                if isinstance(req_id, int)
                else {}
            )
            result = data.get("result") or {}
            channel = result.get("channel") or pending.get("channel") or "unknown"
            success = bool(data.get("success"))
            status = "subscribed" if success else "error"
            error_message = data.get("error") or data.get("errorMessage")
            self._record_subscription_status(
                channel=channel,
                status=status,
                ws_symbols=result.get("symbol") or pending.get("symbol"),
                req_id=req_id,
                error_message=error_message,
            )
            return

        if "event" in data:
            event_type = data.get("event")
            if event_type == "subscriptionStatus":
                self._record_subscription_status(
                    channel=data.get("channel") or "unknown",
                    status=data.get("status") or "error",
                    ws_symbols=data.get("symbol"),
                    req_id=data.get("req_id"),
                    error_message=data.get("errorMessage"),
                )
            else:
                logger.debug(f"Unhandled event message type: {event_type}")
            return

        if "channel" in data:
            channel = data["channel"]
            payload = data.get("data")

            if channel in {"status", "heartbeat"}:
                return

            if not isinstance(payload, list) or not payload:
                logger.debug(
                    "Ignoring channel message without data payload",
                    extra={
                        "event": "ws_channel_missing_data",
                        "channel": channel,
                        "symbol": data.get("symbol"),
                    },
                )
                return

            ws_symbol = self._extract_ws_symbol(data, payload)

            if not isinstance(ws_symbol, str) or not ws_symbol:
                logger.debug(
                    "Ignoring channel message without symbol",
                    extra={"event": "ws_channel_missing_symbol", "channel": channel},
                )
                return

            canonical_pair = self._get_canonical_from_ws_symbol(ws_symbol)

            if not canonical_pair:
                logger.warning(f"Received data for unknown ws_symbol: {ws_symbol}")
                return

            if channel == "ticker":
                if not isinstance(payload[0], dict):
                    logger.debug(
                        "Ignoring malformed ticker payload",
                        extra={
                            "event": "ws_ticker_payload_invalid",
                            "symbol": ws_symbol,
                        },
                    )
                    return
                self.ticker_cache[canonical_pair] = payload[0]
                self.last_ticker_update_ts[canonical_pair] = time.monotonic()
            elif channel == "ohlc":
                candle_data = payload[-1]
                if not isinstance(candle_data, dict):
                    logger.debug(
                        "Ignoring malformed OHLC payload",
                        extra={
                            "event": "ws_ohlc_payload_invalid",
                            "symbol": ws_symbol,
                        },
                    )
                    return

                interval = self._extract_ohlc_interval(data, candle_data)
                timeframe_key = self._get_timeframe_from_interval(interval)
                if not timeframe_key:
                    logger.warning(
                        f"Received OHLC data for unknown interval: {interval}"
                    )
                    return

                # Check for candle rollover
                # Use the candle's interval marker to detect rollovers.
                current_endtime = self._extract_candle_marker(candle_data)
                last_endtime = self._last_candle_endtime[canonical_pair].get(
                    timeframe_key
                )

                # If we have a new endtime, the previous candle (if any) is closed.
                # However, we only have the 'current' update message.
                # To capture the *closed* state of the previous candle, we should have cached it.
                # BUT, WS streams updates. The update with the NEW endtime is for the NEW candle.
                # The FINAL state of the OLD candle was the last update we received with `last_endtime`.

                if last_endtime and current_endtime != last_endtime:
                    # Previous candle closed. Retrieve its last known state from cache.
                    last_candle = self.ohlc_cache.get(canonical_pair, {}).get(
                        timeframe_key
                    )
                    if last_candle and self._on_candle_closed:
                        # Ensure we are persisting the candle associated with last_endtime
                        if last_candle.get("endtime") == last_endtime:
                            try:
                                self._on_candle_closed(
                                    canonical_pair, timeframe_key, last_candle
                                )
                            except Exception as exc:
                                logger.error(
                                    f"Error in on_candle_closed callback: {exc}"
                                )

                # Update trackers
                if current_endtime:
                    self._last_candle_endtime[canonical_pair][
                        timeframe_key
                    ] = current_endtime

                if canonical_pair not in self.ohlc_cache:
                    self.ohlc_cache[canonical_pair] = {}
                self.ohlc_cache[canonical_pair][timeframe_key] = candle_data
                self.last_ohlc_update_ts[canonical_pair][
                    timeframe_key
                ] = time.monotonic()

    def _run(self):
        """The main run loop with reconnection logic."""
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            self._main_task = loop.create_task(self._connect_and_listen())
            loop.run_until_complete(self._main_task)
        except asyncio.CancelledError:
            logger.debug("WebSocket run loop cancelled during shutdown.")
        except RuntimeError as exc:
            error_message = str(exc)
            is_loop_shutdown = (
                "Event loop is closed" in error_message
                or "cannot schedule new futures after shutdown" in error_message
            )

            if is_loop_shutdown and not self._running:
                logger.debug(
                    "Event loop shut down after stop request; exiting run loop quietly."
                )
            elif is_loop_shutdown:
                logger.debug("Event loop closed during shutdown: %s", exc)
            else:
                logger.error("WebSocket run loop error: %s", exc)
        finally:
            pending = [
                task
                for task in asyncio.all_tasks(loop)
                if task is not asyncio.current_task(loop) and not task.done()
            ]
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
            try:
                loop.close()
            except RuntimeError:
                logger.debug("Event loop already closed during cleanup.")

    async def _connect_and_listen(self):
        """Manages the connection and listens for messages."""
        backoff_delay = 1
        max_backoff = 60
        try:
            while self._running:
                try:
                    async with connect(self._url) as ws:
                        self._websocket = ws
                        logger.info("WebSocket connection established.")
                        backoff_delay = 1  # Reset backoff on successful connection
                        self.subscription_status.clear()
                        await self._subscribe()

                        while self._running:
                            try:
                                message = await asyncio.wait_for(ws.recv(), timeout=5)
                                if isinstance(message, bytes):
                                    message = message.decode("utf-8")
                                await self._handle_message(message)
                            except asyncio.TimeoutError:
                                # No message received, send a ping to keep connection alive
                                try:
                                    await ws.ping()
                                except Exception:
                                    logger.warning(
                                        "WebSocket ping failed. Reconnecting."
                                    )
                                    break
                            except ConnectionClosed:
                                logger.warning(
                                    "WebSocket connection closed unexpectedly."
                                )
                                break

                except asyncio.CancelledError:
                    logger.debug("WebSocket listener cancelled; closing connection.")
                    raise
                except Exception as e:
                    # Avoid noisy logs when the process is shutting down
                    if self._running:
                        logger.error(f"WebSocket client error: {e}.")
                    else:
                        logger.debug(f"WebSocket client exiting during shutdown: {e}.")

                if not self._running:
                    break

                logger.info(f"Reconnecting in {backoff_delay}s...")
                await asyncio.sleep(backoff_delay)
                backoff_delay = min(
                    backoff_delay * 2, max_backoff
                )  # Exponential backoff up to max_backoff
        finally:
            if self._websocket and self._websocket.state is not State.CLOSED:
                await self._websocket.close()

        logger.info("WebSocket run loop terminated.")

    def _request_shutdown(self):
        if self._main_task and not self._main_task.done():
            self._main_task.cancel()
        if self._websocket and self._websocket.state is not State.CLOSED:
            asyncio.create_task(self._websocket.close())
