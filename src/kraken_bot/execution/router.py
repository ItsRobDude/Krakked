# src/kraken_bot/execution/router.py

from typing import Any, Dict

from kraken_bot.config import ExecutionConfig

from .models import LocalOrder


def round_order_size(pair_metadata: Dict[str, Any], size: float) -> float:
    """Placeholder for volume rounding logic based on pair metadata."""
    raise NotImplementedError("Size rounding will be implemented in a later phase")


def round_order_price(pair_metadata: Dict[str, Any], price: float) -> float:
    """Placeholder for price rounding logic based on pair metadata."""
    raise NotImplementedError("Price rounding will be implemented in a later phase")


def determine_order_type(order: LocalOrder, config: ExecutionConfig) -> str:
    """Selects an order type based on configuration and order context."""
    return order.order_type or config.default_order_type


def build_order_payload(order: LocalOrder, config: ExecutionConfig) -> Dict[str, Any]:
    """
    Construct the Kraken AddOrder payload for a given LocalOrder.
    Actual rounding and validation logic will be filled in during the full execution implementation.
    """
    payload: Dict[str, Any] = {
        "pair": order.pair,
        "type": order.side,
        "ordertype": determine_order_type(order, config),
        "volume": order.requested_base_size,
    }

    if order.requested_price is not None:
        payload["price"] = order.requested_price

    if order.userref is not None:
        payload["userref"] = order.userref

    payload["validate"] = 1 if config.validate_only or config.mode != "live" else 0
    return payload
