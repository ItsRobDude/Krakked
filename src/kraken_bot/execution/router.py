# src/kraken_bot/execution/router.py

import logging
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, Any, Optional, Tuple
from uuid import NAMESPACE_DNS, uuid5

from kraken_bot.config import ExecutionConfig
from kraken_bot.market_data.models import PairMetadata

from .models import LocalOrder
from .userref import resolve_userref

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


def classify_volume(metadata: PairMetadata, volume: float) -> Tuple[float, bool]:
    """
    Classify whether a given raw volume is executable (not dust).
    Returns (rounded_volume, is_executable).
    """
    rounded = round_order_size(metadata, abs(volume))
    is_executable = rounded > 0 and rounded >= metadata.min_order_size
    return rounded, is_executable


def dust_reason(metadata: PairMetadata, raw_volume: float, rounded: float) -> str:
    """Return a stable explanation for why a volume is considered dust."""
    return (
        f"Dust: rounded sell volume {rounded} < min_order_size {metadata.min_order_size} "
        f"for {metadata.canonical}"
    )


def determine_order_type(order: LocalOrder, config: ExecutionConfig) -> str:
    """Selects an order type based on configuration and order context."""
    return order.order_type or config.default_order_type


def apply_slippage(order: LocalOrder, config: ExecutionConfig) -> Optional[float]:
    """
    Adjust the requested price by the configured slippage tolerance.

    Performs calculation in Decimal to avoid floating point drift before rounding.
    """
    if order.requested_price is None:
        return None

    # Use string conversion to preserve the "human" value of the float
    # e.g. Decimal(str(0.1)) gives 0.1, whereas Decimal(0.1) gives 0.1000000000000000055...
    try:
        price_dec = Decimal(str(order.requested_price))
        # max_slippage_bps is an int (e.g. 50), so this math is safe
        slippage_factor_dec = Decimal(max(config.max_slippage_bps, 0)) / Decimal(
            "10000"
        )
    except Exception:
        # Fallback if conversion fails (unlikely)
        return order.requested_price

    if slippage_factor_dec == 0:
        return order.requested_price

    if order.side == "buy":
        # Price * (1 + factor)
        adjusted_dec = price_dec * (Decimal("1") + slippage_factor_dec)
    else:
        # Price * (1 - factor)
        adjusted_dec = price_dec * (Decimal("1") - slippage_factor_dec)

    # Guard against negative prices
    adjusted_price = float(max(adjusted_dec, Decimal("0")))

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

    # Kraken market orders do not have time-in-force options. Only include
    # time-in-force / post-only flags when they are valid for the order type.
    if order_type == "limit":
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


def _resolve_limit_price(
    pair: str, market_data: Optional["MarketDataAPI"]
) -> Tuple[Optional[float], Optional[str]]:
    """Resolve a mid-price for a limit order when no explicit price is provided."""
    if not market_data:
        return None, f"Missing market data for limit order on {pair}"

    try:
        bid_ask = market_data.get_best_bid_ask(pair)
    except Exception as exc:  # pragma: no cover
        return None, f"Failed to fetch market data for {pair}: {exc}"

    if not bid_ask:
        return None, f"Invalid bid/ask data for {pair}: {bid_ask}"

    try:
        # Use simple float conversion; handled by try/except block if None/invalid
        bid = float(bid_ask.get("bid"))
        ask = float(bid_ask.get("ask"))
        return (bid + ask) / 2.0, None
    except (TypeError, ValueError):
        return None, f"Invalid bid/ask data for {pair}: {bid_ask}"


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

    requested_price: Optional[float] = None

    # Only limit orders require a price. Market orders should not be blocked by
    # missing/stale websocket bid/ask data.
    if order_type == "limit":
        requested_price = plan.metadata.get("requested_price")

        if requested_price is None:
            price, warning = _resolve_limit_price(action.pair, market_data)
            if warning:
                return None, warning
            requested_price = price

    rounded_size, is_executable = classify_volume(pair_metadata, volume)
    if not is_executable:
        raise ValueError(dust_reason(pair_metadata, volume, rounded_size))

    rounded_price: Optional[float] = None
    if requested_price is not None:
        rounded_price = round_order_price(pair_metadata, requested_price)

    # Create a unique seed string
    seed_str = f"{plan.plan_id}-{action.strategy_id}-{action.pair}-{side}"

    # Generate deterministic UUID
    local_id = str(uuid5(NAMESPACE_DNS, seed_str))

    risk_reducing = action.action_type in {"reduce", "close"}

    order = LocalOrder(
        local_id=local_id,
        plan_id=plan.plan_id,
        strategy_id=action.strategy_id,
        pair=action.pair,
        side=side,
        order_type=order_type,
        userref=resolve_userref(action.userref),
        requested_base_size=rounded_size,
        requested_price=rounded_price,
        risk_reducing=risk_reducing,
    )

    return order, None
