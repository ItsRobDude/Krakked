# src/kraken_bot/execution/router.py

import logging
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, Any, Optional, Tuple
from uuid import NAMESPACE_DNS, uuid5

from kraken_bot.config import ExecutionConfig
from kraken_bot.market_data.models import PairMetadata

from .models import LocalOrder

if TYPE_CHECKING:
    from kraken_bot.market_data.api import MarketDataAPI
    from kraken_bot.strategy.models import ExecutionPlan, RiskAdjustedAction

logger = logging.getLogger(__name__)


def round_order_size(metadata: PairMetadata, size: float) -> float:
    """Round order volume using PairMetadata volume precision (FLOOR)."""
    try:
        d_size = Decimal(str(size))
        quantizer = Decimal("1." + "0" * metadata.volume_decimals)
        return float(d_size.quantize(quantizer, rounding=ROUND_FLOOR))
    except Exception:
        return size


def round_order_price(metadata: PairMetadata, price: float) -> float:
    """Round order price using PairMetadata price precision (HALF_UP)."""
    try:
        d_price = Decimal(str(price))
        quantizer = Decimal("1." + "0" * metadata.price_decimals)
        return float(d_price.quantize(quantizer, rounding=ROUND_HALF_UP))
    except Exception:
        return price


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
    order: LocalOrder,
    config: ExecutionConfig,
    pair_metadata: PairMetadata,
) -> dict[str, Any]:
    """
    Construct the Kraken AddOrder payload for a given LocalOrder.
    Rounding uses pair metadata when provided, and validation/userref flags
    mirror the execution configuration and order context.
    """
    order_type = determine_order_type(order, config)
    payload: dict[str, Any] = {
        "pair": pair_metadata.rest_symbol,
        "type": order.side,
        "ordertype": order_type,
        "volume": str(round_order_size(pair_metadata, order.requested_base_size)),
    }

    slippage_price = apply_slippage(order, config)

    if order_type == "limit" and slippage_price is not None:
        payload["price"] = str(round_order_price(pair_metadata, slippage_price))

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


def build_order_from_plan_action(
    action: "RiskAdjustedAction",
    plan: "ExecutionPlan",
    pair_metadata: PairMetadata,
    config: ExecutionConfig,
    market_data: Optional["MarketDataAPI"] = None,
) -> Tuple[Optional[LocalOrder], Optional[str]]:
    """
    Build a :class:`LocalOrder` from a plan action using live market data.

    Returns a tuple of (order, warning). When market data is unavailable for a
    required limit price, the order will be ``None`` and the warning will
    describe the failure.
    """

    delta = action.target_base_size - action.current_base_size
    side = "buy" if delta > 0 else "sell"
    volume = abs(delta)

    order_type = plan.metadata.get("order_type") or config.default_order_type

    requested_price: Optional[float] = plan.metadata.get("requested_price")
    bid_ask = None
    if market_data:
        try:
            bid_ask = market_data.get_best_bid_ask(action.pair)
        except Exception as exc:  # pragma: no cover - passthrough for data errors
            warning = f"Failed to fetch market data for {action.pair}: {exc}"
            return None, warning

    if bid_ask:
        try:
            bid_value = bid_ask.get("bid")
            ask_value = bid_ask.get("ask")

            if bid_value is None or ask_value is None:
                warning = f"Invalid bid/ask data for {action.pair}: {bid_ask}"
                return None, warning

            bid = float(bid_value)
            ask = float(ask_value)
            requested_price = (bid + ask) / 2
        except (TypeError, ValueError):
            warning = f"Invalid bid/ask data for {action.pair}: {bid_ask}"
            return None, warning

    if order_type == "limit" and requested_price is None:
        warning = f"Missing market data for limit order on {action.pair}"
        return None, warning

    rounded_size = round_order_size(pair_metadata, volume)
    if rounded_size <= 0 or rounded_size < pair_metadata.min_order_size:
        raise ValueError(
            f"Requested size {volume} too small for pair "
            f"{pair_metadata.canonical} (min={pair_metadata.min_order_size})"
        )

    rounded_price: Optional[float] = None
    if requested_price is not None:
        rounded_price = round_order_price(pair_metadata, requested_price)

    # Create a unique seed string
    seed_str = f"{plan.plan_id}-{action.strategy_id}-{action.pair}-{side}"

    # Generate deterministic UUID
    local_id = str(uuid5(NAMESPACE_DNS, seed_str))

    order = LocalOrder(
        local_id=local_id,
        plan_id=plan.plan_id,
        strategy_id=action.strategy_id,
        pair=action.pair,
        side=side,
        order_type=order_type,
        userref=int(action.userref) if action.userref is not None else None,
        requested_base_size=rounded_size,
        requested_price=rounded_price,
    )

    return order, None
