# src/kraken_bot/execution/router.py

import logging
from typing import Any, Dict, Optional

from kraken_bot.config import ExecutionConfig

from .models import LocalOrder

logger = logging.getLogger(__name__)


def round_order_size(pair_metadata: Dict[str, Any], size: float) -> float:
    """Round order volume using pair metadata decimals when available."""
    decimals = None
    if pair_metadata:
        decimals = pair_metadata.get("volume_decimals") if isinstance(pair_metadata, dict) else getattr(pair_metadata, "volume_decimals", None)

    try:
        precision = int(decimals) if decimals is not None else None
    except (TypeError, ValueError):
        precision = None

    return round(size, precision) if precision is not None else size


def round_order_price(pair_metadata: Dict[str, Any], price: float) -> float:
    """Round order price using pair metadata decimals when available."""
    decimals = None
    if pair_metadata:
        decimals = pair_metadata.get("price_decimals") if isinstance(pair_metadata, dict) else getattr(pair_metadata, "price_decimals", None)

    try:
        precision = int(decimals) if decimals is not None else None
    except (TypeError, ValueError):
        precision = None

    return round(price, precision) if precision is not None else price


def determine_order_type(order: LocalOrder, config: ExecutionConfig) -> str:
    """Selects an order type based on configuration and order context."""
    return order.order_type or config.default_order_type


def apply_slippage(order: LocalOrder, config: ExecutionConfig) -> Optional[float]:
    """
    Adjust the requested price by the configured slippage tolerance.

    For buys we cap the maximum price we are willing to pay; for sells we
    floor the minimum price we are willing to accept. Values are guarded
    against negative prices.
    """

    if order.requested_price is None:
        return None

    slippage_fraction = max(config.max_slippage_bps, 0) / 10_000
    if slippage_fraction == 0:
        return order.requested_price

    if order.side == "buy":
        adjusted = order.requested_price * (1 + slippage_fraction)
    else:
        adjusted = order.requested_price * (1 - slippage_fraction)

    adjusted_price = max(adjusted, 0.0)
    logger.debug(
        "Applying slippage",
        extra={
            "event": "apply_slippage",
            "side": order.side,
            "requested_price": order.requested_price,
            "adjusted_price": adjusted_price,
            "slippage_bps": config.max_slippage_bps,
        },
    )
    return adjusted_price


def build_order_payload(
    order: LocalOrder, config: ExecutionConfig, pair_metadata: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Construct the Kraken AddOrder payload for a given LocalOrder.
    Rounding uses pair metadata when provided, and validation/userref flags
    mirror the execution configuration and order context.
    """
    order_type = determine_order_type(order, config)
    payload: Dict[str, Any] = {
        "pair": order.pair,
        "type": order.side,
        "ordertype": order_type,
        "volume": round_order_size(pair_metadata or {}, order.requested_base_size),
    }

    slippage_price = apply_slippage(order, config)

    if order_type == "limit" and slippage_price is not None:
        payload["price"] = round_order_price(pair_metadata or {}, slippage_price)

    payload["timeinforce"] = config.time_in_force

    flags = []
    if config.post_only:
        flags.append("post")

    if flags:
        payload["oflags"] = ",".join(flags)

    if order.userref is not None:
        payload["userref"] = order.userref

    if config.validate_only or config.mode != "live":
        payload["validate"] = 1

    return payload
