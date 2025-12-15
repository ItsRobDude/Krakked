from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PairMetadata:
    """
    Metadata describing a trading pair in the Kraken universe.

    @param canonical - The internal normalized ID (e.g., "XBTUSD").
    @param base - The base asset (e.g., "XBT").
    @param quote - The quote asset (e.g., "USD").
    @param rest_symbol - The symbol used for REST API calls (e.g., "XXBTZUSD").
    @param ws_symbol - The symbol used for WebSocket subscriptions (e.g., "XBT/USD").
    @param raw_name - The raw key from the AssetPairs response (e.g., "XXBTZUSD" or "XBTUSD").
    @param price_decimals - Precision for price formatting (pair_decimals).
    @param volume_decimals - Precision for volume formatting (lot_decimals).
    @param lot_size - Minimum lot multiplier (usually 1).
    @param min_order_size - Minimum order volume (ordermin).
    @param status - Trading status (e.g., "online", "cancel_only").
    @param liquidity_24h_usd - Estimated 24h volume in USD (optional).
    """
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
