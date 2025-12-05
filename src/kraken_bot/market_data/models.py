from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PairMetadata:
    canonical: str
    base: str
    quote: str
    rest_symbol: str
    ws_symbol: str
    raw_name: str
    price_decimals: int
    volume_decimals: int
    lot_size: float
    min_order_size: float
    status: str
    liquidity_24h_usd: float | None = None


@dataclass
class OHLCBar:
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class ConnectionStatus:
    rest_api_reachable: bool
    websocket_connected: bool
    streaming_pairs: int
    stale_pairs: int
    subscription_errors: int
