# src/kraken_bot/market_data/ws_client.py

import asyncio
import json
import logging
import threading
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional

from websockets.exceptions import ConnectionClosed
from websockets.legacy.client import WebSocketClientProtocol, connect

from kraken_bot.config import PairMetadata

logger = logging.getLogger(__name__)

KRAKEN_WS_V2_URL = "wss://ws.kraken.com/v2"


class KrakenWSClientV2:
    """
    Handles the connection to the Kraken WebSocket API v2, subscribes to channels,
    and maintains an in-memory cache of the latest market data.
    """

    def __init__(self, pairs: List[PairMetadata], timeframes: List[str] = ["1m"]):
        self._url = KRAKEN_WS_V2_URL
        self._pairs = pairs
        self._ws_symbols = [p.ws_symbol for p in self._pairs]
        self._timeframes = timeframes
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._main_task: Optional[asyncio.Task] = None
        self._websocket: Optional[WebSocketClientProtocol] = None

        # In-memory cache
        self.last_ticker_update_ts: Dict[str, float] = defaultdict(float)
        self.last_ohlc_update_ts: Dict[str, Dict[str, float]] = defaultdict(dict)
        self.ticker_cache: Dict[str, Dict[str, Any]] = {}
        self.ohlc_cache: Dict[str, Dict[str, Any]] = (
            {}
        )  # key: pair, value: {timeframe: ohlc_data}
        self.subscription_status: Dict[str, Dict[str, Dict[str, Any]]] = defaultdict(
            dict
        )

    @property
    def is_connected(self) -> bool:
        """Returns True if the WebSocket connection is open."""
        return self._websocket is not None and self._websocket.open

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
        for p in self._pairs:
            if p.ws_symbol == ws_symbol:
                return p.canonical
        return None

    async def _subscribe(self):
        """Subscribes to ticker and OHLC channels for all pairs."""
        if not self._websocket:
            return

        # Ticker subscription
        ticker_sub = {
            "method": "subscribe",
            "params": {
                "channel": "ticker",
                "symbol": self._ws_symbols,
            },
            "req_id": int(time.time() * 1000),
        }
        await self._websocket.send(json.dumps(ticker_sub))
        logger.info(f"Subscribed to ticker for {len(self._ws_symbols)} pairs.")

        # OHLC subscriptions
        for tf in self._timeframes:
            interval = (
                int(tf[:-1])
                if tf.endswith("m")
                else int(tf[:-1]) * 60 if tf.endswith("h") else int(tf[:-1]) * 1440
            )
            ohlc_sub = {
                "method": "subscribe",
                "params": {
                    "channel": "ohlc",
                    "symbol": self._ws_symbols,
                    "interval": interval,
                },
                "req_id": int(time.time() * 1000) + 1,
            }
            await self._websocket.send(json.dumps(ohlc_sub))
            logger.info(f"Subscribed to OHLC ({tf}) for {len(self._ws_symbols)} pairs.")

    def _get_timeframe_from_interval(self, interval: int) -> Optional[str]:
        """Maps a Kraken interval integer back to a timeframe string like '1m'."""
        # This is a reverse mapping of the one used in the OHLC fetcher
        interval_to_tf = {1: "1m", 5: "5m", 15: "15m", 60: "1h", 240: "4h", 1440: "1d"}
        return interval_to_tf.get(interval)

    async def _handle_message(self, message: str):
        """Parses an incoming message and updates the cache."""
        data = json.loads(message)

        if "event" in data:
            event_type = data.get("event")
            if event_type == "subscriptionStatus":
                status = data.get("status")
                channel = data.get("channel") or "unknown"
                ws_symbols = data.get("symbol")
                req_id = data.get("req_id")
                error_message = data.get("errorMessage")

                if not isinstance(ws_symbols, list):
                    ws_symbols = [ws_symbols]

                recorded_pairs = []
                display_names = []
                for ws_symbol in ws_symbols:
                    canonical_pair = (
                        self._get_canonical_from_ws_symbol(ws_symbol)
                        if ws_symbol
                        else None
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
                    logger.info(f"Subscribed to {channel} for {pair_display}.")
                else:
                    logger.error(
                        f"Subscription to {channel} for {pair_display} failed: {error_message or 'unknown error'}"
                    )
            else:
                logger.debug(f"Unhandled event message type: {event_type}")
            return

        if "channel" in data:
            channel = data["channel"]
            ws_symbol = data["symbol"]
            canonical_pair = self._get_canonical_from_ws_symbol(ws_symbol)

            if not canonical_pair:
                logger.warning(f"Received data for unknown ws_symbol: {ws_symbol}")
                return

            if channel == "ticker":
                self.ticker_cache[canonical_pair] = data["data"][0]
                self.last_ticker_update_ts[canonical_pair] = time.monotonic()
            elif channel == "ohlc":
                interval = data.get("params", {}).get("interval")
                timeframe_key = self._get_timeframe_from_interval(interval)
                if not timeframe_key:
                    logger.warning(
                        f"Received OHLC data for unknown interval: {interval}"
                    )
                    return

                if canonical_pair not in self.ohlc_cache:
                    self.ohlc_cache[canonical_pair] = {}
                self.ohlc_cache[canonical_pair][timeframe_key] = data["data"][0]
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
            if self._websocket and not self._websocket.closed:
                await self._websocket.close()

        logger.info("WebSocket run loop terminated.")

    def _request_shutdown(self):
        if self._main_task and not self._main_task.done():
            self._main_task.cancel()
        if self._websocket and not self._websocket.closed:
            asyncio.create_task(self._websocket.close())
