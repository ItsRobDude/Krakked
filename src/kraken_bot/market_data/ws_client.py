# src/kraken_bot/market_data/ws_client.py

import asyncio
import json
import logging
import threading
import time
from typing import List, Dict, Any, Optional
from collections import defaultdict
import websockets
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
        self._websocket: Optional[websockets.WebSocketClientProtocol] = None

        # In-memory cache
        self.last_update_ts: Dict[str, float] = defaultdict(float)
        self.ticker_cache: Dict[str, Dict[str, Any]] = {}
        self.ohlc_cache: Dict[str, Dict[str, Any]] = {} # key: pair, value: {timeframe: ohlc_data}

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
        if self._thread and self._thread.is_alive():
            # The async loop will break on the next iteration
            self._thread.join(timeout=5)
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
            "req_id": int(time.time() * 1000)
        }
        await self._websocket.send(json.dumps(ticker_sub))
        logger.info(f"Subscribed to ticker for {len(self._ws_symbols)} pairs.")

        # OHLC subscriptions
        for tf in self._timeframes:
            interval = int(tf[:-1]) if tf.endswith('m') else int(tf[:-1]) * 60 if tf.endswith('h') else int(tf[:-1]) * 1440
            ohlc_sub = {
                "method": "subscribe",
                "params": {
                    "channel": "ohlc",
                    "symbol": self._ws_symbols,
                    "interval": interval
                },
                "req_id": int(time.time() * 1000) + 1
            }
            await self._websocket.send(json.dumps(ohlc_sub))
            logger.info(f"Subscribed to OHLC ({tf}) for {len(self._ws_symbols)} pairs.")

    def _get_timeframe_from_interval(self, interval: int) -> Optional[str]:
        """Maps a Kraken interval integer back to a timeframe string like '1m'."""
        # This is a reverse mapping of the one used in the OHLC fetcher
        interval_to_tf = {
            1: "1m", 5: "5m", 15: "15m", 60: "1h", 240: "4h", 1440: "1d"
        }
        return interval_to_tf.get(interval)

    async def _handle_message(self, message: str):
        """Parses an incoming message and updates the cache."""
        data = json.loads(message)

        if "channel" in data:
            channel = data["channel"]
            ws_symbol = data["symbol"]
            canonical_pair = self._get_canonical_from_ws_symbol(ws_symbol)

            if not canonical_pair:
                logger.warning(f"Received data for unknown ws_symbol: {ws_symbol}")
                return

            self.last_update_ts[canonical_pair] = time.monotonic()

            if channel == "ticker":
                self.ticker_cache[canonical_pair] = data["data"][0]
            elif channel == "ohlc":
                interval = data.get("params", {}).get("interval")
                timeframe_key = self._get_timeframe_from_interval(interval)
                if not timeframe_key:
                    logger.warning(f"Received OHLC data for unknown interval: {interval}")
                    return

                if canonical_pair not in self.ohlc_cache:
                    self.ohlc_cache[canonical_pair] = {}
                self.ohlc_cache[canonical_pair][timeframe_key] = data["data"][0]

    def _run(self):
        """The main run loop with reconnection logic."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self._connect_and_listen())

    async def _connect_and_listen(self):
        """Manages the connection and listens for messages."""
        backoff_delay = 1
        while self._running:
            try:
                async with websockets.connect(self._url) as ws:
                    self._websocket = ws
                    logger.info("WebSocket connection established.")
                    backoff_delay = 1 # Reset backoff on successful connection
                    await self._subscribe()

                    while self._running:
                        try:
                            message = await asyncio.wait_for(ws.recv(), timeout=15)
                            await self._handle_message(message)
                        except asyncio.TimeoutError:
                            # No message received, send a ping to keep connection alive
                            try:
                                await ws.ping()
                            except Exception:
                                logger.warning("WebSocket ping failed. Reconnecting.")
                                break
                        except websockets.exceptions.ConnectionClosed:
                            logger.warning("WebSocket connection closed unexpectedly.")
                            break

            except Exception as e:
                logger.error(f"WebSocket client error: {e}. Reconnecting in {backoff_delay}s...")
                await asyncio.sleep(backoff_delay)
                backoff_delay = min(backoff_delay * 2, 60) # Exponential backoff up to 60s

        logger.info("WebSocket run loop terminated.")
