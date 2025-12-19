# src/kraken_bot/execution/adapter.py

import logging
import time
from datetime import UTC, datetime
from typing import Any, Callable, Dict, Optional, Protocol

from kraken_bot.config import ExecutionConfig
from kraken_bot.connection.exceptions import RateLimitError, ServiceUnavailableError
from kraken_bot.connection.rate_limiter import RateLimiter
from kraken_bot.connection.rest_client import KrakenRESTClient
from kraken_bot.logging_config import structured_log_extra
from kraken_bot.market_data.models import PairMetadata

from .exceptions import ExecutionError, OrderCancelError, OrderRejectedError
from .models import LocalOrder
from .router import build_order_payload

logger = logging.getLogger(__name__)


class ExecutionAdapter(Protocol):
    """Protocol defining the interface for execution adapters."""

    client: Optional[KrakenRESTClient]
    config: ExecutionConfig

    def submit_order(
        self,
        order: LocalOrder,
        pair_metadata: PairMetadata,
        latest_price: Optional[float] = None,
    ) -> LocalOrder:
        """
        Submit an order for execution.

        Depending on the adapter implementation and configuration, this may:
        - Send a live order to Kraken (Live mode)
        - Send a validate-only order to Kraken (Paper mode)
        - Simulate an order fill locally (Dry Run / Simulation)

        Args:
            order: The local order object containing order details.
            pair_metadata: Metadata for the trading pair (min size, decimals, etc.).
            latest_price: Optional latest known price for notional calculations.

        Returns:
            The updated LocalOrder object with status, IDs, and any errors.
        """
        ...

    def cancel_order(self, order: LocalOrder) -> None:
        """
        Cancel a specific order.

        Args:
            order: The local order object to cancel. Must have a kraken_order_id
                   if canceling a live/paper order.

        Raises:
            OrderCancelError: If cancellation fails.
        """
        ...

    def cancel_all_orders(self) -> None:
        """
        Cancel all open orders for the account.

        This acts as a "panic" button to clear all working orders.

        Raises:
            OrderCancelError: If the bulk cancellation fails.
        """
        ...


class KrakenExecutionAdapter:
    """Adapter for live and paper execution against the Kraken API.

    Handles:
      - Live execution: Real orders with ``validate=0`` (if allowed).
      - Paper execution: Validation calls with ``validate=1``.
      - Retry logic, backoff, and error mapping.
      - Dead-man switch heartbeat handling.
    """

    def __init__(
        self, client: KrakenRESTClient, config: Optional[ExecutionConfig] = None
    ):
        self.client: Optional[KrakenRESTClient] = client
        self.config = config or ExecutionConfig()

        if self.config.mode == "live" and not self.config.validate_only:
            if getattr(self.config, "allow_live_trading", False):
                logger.warning(
                    "Live trading mode ENABLED; orders will be transmitted to Kraken.",
                    extra=structured_log_extra(event="live_trading_enabled"),
                )
            else:
                logger.warning(
                    "Live trading requested but allow_live_trading is False; orders will be rejected.",
                    extra=structured_log_extra(event="live_trading_blocked"),
                )

    def submit_order(
        self,
        order: LocalOrder,
        pair_metadata: PairMetadata,
        latest_price: Optional[float] = None,
    ) -> LocalOrder:
        """
        Prepare and submit an order to Kraken.

        The payload construction is delegated to routing helpers and the actual
        REST call is handled here. Validates volume and notional constraints
        before submission.

        In 'paper' mode (or validate_only=True), the order is sent with validate=True.
        In 'live' mode, the order is executed if allow_live_trading is enabled.
        """
        payload: Dict[str, Any] = build_order_payload(order, self.config, pair_metadata)
        order.raw_request = payload

        assert self.client is not None

        rounded_volume = float(payload["volume"])
        if rounded_volume < pair_metadata.min_order_size:
            order.status = "rejected"
            order.last_error = (
                f"Order volume {rounded_volume} below minimum "
                f"{pair_metadata.min_order_size} for {pair_metadata.canonical}"
            )
            logger.error(
                order.last_error,
                extra=structured_log_extra(
                    event="order_rejected_min_volume",
                    plan_id=order.plan_id,
                    strategy_id=order.strategy_id,
                    pair=order.pair,
                    local_order_id=order.local_id,
                    volume=rounded_volume,
                ),
            )
            return order

        price_for_notional = (
            payload.get("price") or latest_price or order.requested_price
        )

        # Notional checks: Only enforce if NOT risk reducing
        if self.config.min_order_notional_usd > 0 and not order.risk_reducing:
            if price_for_notional is None:
                order.status = "rejected"
                order.last_error = (
                    "Unable to verify minimum notional: price unavailable"
                )
                logger.error(
                    order.last_error,
                    extra=structured_log_extra(
                        event="order_rejected_missing_price",
                        plan_id=order.plan_id,
                        strategy_id=order.strategy_id,
                        pair=order.pair,
                        local_order_id=order.local_id,
                    ),
                )
                return order

            notional = float(payload["volume"]) * float(price_for_notional)
            if notional < self.config.min_order_notional_usd:
                order.status = "rejected"
                order.last_error = f"Order notional ${notional:.2f} below minimum ${self.config.min_order_notional_usd:.2f}"
                logger.error(
                    order.last_error,
                    extra=structured_log_extra(
                        event="order_rejected_min_notional",
                        plan_id=order.plan_id,
                        strategy_id=order.strategy_id,
                        pair=order.pair,
                        local_order_id=order.local_id,
                        notional=notional,
                    ),
                )
                return order

        should_validate = payload.get("validate") == 1
        live_trading_allowed = (
            self.config.mode == "live"
            and not self.config.validate_only
            and getattr(self.config, "allow_live_trading", False)
        )

        if live_trading_allowed and not getattr(
            self.config, "paper_tests_completed", False
        ):
            order.status = "rejected"
            order.last_error = "Live trading blocked: paper_tests_completed is False"
            logger.error(
                order.last_error,
                extra=structured_log_extra(
                    event="order_rejected_paper_tests_incomplete",
                    plan_id=order.plan_id,
                    strategy_id=order.strategy_id,
                    pair=order.pair,
                    local_order_id=order.local_id,
                ),
            )
            return order

        if self.config.dead_man_switch_seconds > 0:
            if live_trading_allowed:
                if hasattr(self.client, "cancel_all_orders_after"):
                    try:
                        self.client.cancel_all_orders_after(
                            self.config.dead_man_switch_seconds
                        )
                        logger.info(
                            "Dead man switch heartbeat set",
                            extra=structured_log_extra(
                                event="dead_man_switch_set",
                                timeout_seconds=self.config.dead_man_switch_seconds,
                            ),
                        )
                    except (
                        Exception
                    ) as exc:  # pragma: no cover - passthrough for client errors
                        logger.warning(
                            "Failed to refresh dead man switch heartbeat",
                            extra=structured_log_extra(
                                event="dead_man_switch_error",
                                timeout_seconds=self.config.dead_man_switch_seconds,
                                error=str(exc),
                            ),
                        )
                else:
                    logger.warning(
                        "Dead man switch configured but client does not support cancel_all_orders_after",
                        extra=structured_log_extra(
                            event="dead_man_switch_unavailable",
                            timeout_seconds=self.config.dead_man_switch_seconds,
                        ),
                    )
            else:
                logger.info(
                    "Dead man switch configured but live trading is disabled; skipping",
                    extra=structured_log_extra(
                        event="dead_man_switch_skipped",
                        mode=self.config.mode,
                        validate_only=self.config.validate_only,
                        allow_live_trading=getattr(
                            self.config, "allow_live_trading", False
                        ),
                    ),
                )

        if not should_validate and not live_trading_allowed:
            order.status = "rejected"
            order.last_error = "Live trading disabled by configuration"
            logger.error(
                order.last_error,
                extra=structured_log_extra(
                    event="order_rejected_live_guard",
                    plan_id=order.plan_id,
                    strategy_id=order.strategy_id,
                    pair=order.pair,
                    local_order_id=order.local_id,
                    mode=self.config.mode,
                ),
            )
            return order

        attempts = 0
        backoff_seconds = self.config.retry_backoff_seconds
        while True:
            try:
                resp = self.client.add_order(payload)
                order.raw_response = resp
                break
            except (RateLimitError, ServiceUnavailableError) as exc:
                attempts += 1
                if attempts > self.config.max_retries:
                    order.status = "error"
                    order.last_error = str(exc)
                    logger.error(
                        "Order submission retries exhausted",
                        extra=structured_log_extra(
                            event="order_retry_exhausted",
                            plan_id=order.plan_id,
                            strategy_id=order.strategy_id,
                            pair=order.pair,
                            local_order_id=order.local_id,
                            retries=attempts - 1,
                            error=order.last_error,
                        ),
                    )
                    raise ExecutionError(f"Failed to submit order: {exc}") from exc

                sleep_seconds = backoff_seconds * (
                    self.config.retry_backoff_factor ** (attempts - 1)
                )
                logger.warning(
                    "Transient error submitting order; retrying",
                    extra=structured_log_extra(
                        event="order_retry",
                        plan_id=order.plan_id,
                        strategy_id=order.strategy_id,
                        pair=order.pair,
                        local_order_id=order.local_id,
                        attempt=attempts,
                        sleep_seconds=sleep_seconds,
                        error=str(exc),
                    ),
                )
                time.sleep(sleep_seconds)
            except Exception as exc:  # pragma: no cover - passthrough for client errors
                order.status = "error"
                order.last_error = str(exc)
                logger.error(
                    "Order submission error",
                    extra=structured_log_extra(
                        event="order_submit_error",
                        plan_id=order.plan_id,
                        strategy_id=order.strategy_id,
                        pair=order.pair,
                        local_order_id=order.local_id,
                        error=order.last_error,
                    ),
                )
                raise ExecutionError(f"Failed to submit order: {exc}") from exc

        errors = resp.get("error") or []
        if errors:
            order.status = "rejected"
            order.last_error = "; ".join(errors)
            logger.error(
                "Order rejected by Kraken",
                extra=structured_log_extra(
                    event="order_rejected",
                    plan_id=order.plan_id,
                    strategy_id=order.strategy_id,
                    pair=order.pair,
                    local_order_id=order.local_id,
                    errors=errors,
                    order_type=order.order_type,
                ),
            )
            raise OrderRejectedError(order.last_error)

        if self.config.validate_only or self.config.mode != "live":
            order.status = "validated"
            logger.info(
                "Order validated only",
                extra=structured_log_extra(
                    event="order_validated",
                    plan_id=order.plan_id,
                    strategy_id=order.strategy_id,
                    pair=order.pair,
                    local_order_id=order.local_id,
                    mode=self.config.mode,
                ),
            )
            return order

        txids = resp.get("txid") or []
        if txids:
            order.kraken_order_id = txids[0]
            order.status = "open"
            logger.info(
                "Order accepted by Kraken",
                extra=structured_log_extra(
                    event="order_submitted",
                    plan_id=order.plan_id,
                    strategy_id=order.strategy_id,
                    pair=order.pair,
                    local_order_id=order.local_id,
                    kraken_order_id=order.kraken_order_id,
                    mode=self.config.mode,
                ),
            )
            return order

        order.status = "error"
        order.last_error = "Missing transaction id in Kraken response"
        raise ExecutionError(order.last_error)

    def cancel_order(self, order: LocalOrder) -> None:
        """
        Cancel a single order identified by its Kraken order id.

        Raises:
            ExecutionError: If the order has no Kraken order ID.
            OrderCancelError: If the API call fails or returns errors.
        """
        if not order.kraken_order_id:
            raise ExecutionError("Cannot cancel order without a Kraken order id")

        assert self.client is not None
        try:
            resp = self.client.cancel_order(order.kraken_order_id)
        except Exception as exc:  # pragma: no cover - passthrough for client errors
            raise OrderCancelError(f"Failed to cancel order: {exc}") from exc

        errors = resp.get("error") or []
        if errors:
            raise OrderCancelError("; ".join(errors))

    def cancel_all_orders(self) -> None:
        """Cancel all open orders for the authenticated Kraken account."""
        assert self.client is not None
        try:
            resp = self.client.cancel_all_orders()
        except Exception as exc:  # pragma: no cover - passthrough for client errors
            raise OrderCancelError(f"Failed to cancel all orders: {exc}") from exc

        errors = resp.get("error") or []
        if errors:
            raise OrderCancelError("; ".join(errors))


class DryRunExecutionAdapter:
    """Offline execution adapter for dry-run mode.

    Performs NO network calls to Kraken. It validates local constraints (e.g. min
    order volume) and marks orders as 'validated' (if validate_only=True) or
    'filled' (if acting as a mock).
    """

    def __init__(
        self,
        config: Optional[ExecutionConfig] = None,
        rate_limiter: Optional[RateLimiter] = None,
    ):
        self.config = config or ExecutionConfig()
        # Initialize client but do NOT use it for network calls.
        self.client: Optional[KrakenRESTClient] = KrakenRESTClient(
            rate_limiter=rate_limiter
        )

    def submit_order(
        self,
        order: LocalOrder,
        pair_metadata: PairMetadata,
        latest_price: Optional[float] = None,
    ) -> LocalOrder:
        """
        Simulate order submission without network calls.

        Validates local constraints (min size, min notional) and immediately
        fills the order at the requested price (or latest price) if successful.
        If validate_only is True, marks as 'validated' instead of 'filled'.
        """
        payload: Dict[str, Any] = build_order_payload(order, self.config, pair_metadata)
        order.raw_request = payload

        rounded_volume = float(payload["volume"])
        if rounded_volume < pair_metadata.min_order_size:
            order.status = "rejected"
            order.last_error = (
                f"Order volume {rounded_volume} below minimum "
                f"{pair_metadata.min_order_size} for {pair_metadata.canonical}"
            )
            logger.error(
                order.last_error,
                extra=structured_log_extra(
                    event="order_rejected_min_volume",
                    plan_id=order.plan_id,
                    strategy_id=order.strategy_id,
                    pair=order.pair,
                    local_order_id=order.local_id,
                    volume=rounded_volume,
                ),
            )
            return order

        price_for_notional = (
            payload.get("price") or latest_price or order.requested_price
        )

        # Notional checks: Only enforce if NOT risk reducing
        if self.config.min_order_notional_usd > 0 and not order.risk_reducing:
            if price_for_notional is None:
                order.status = "rejected"
                order.last_error = (
                    "Unable to verify minimum notional: price unavailable"
                )
                logger.error(
                    order.last_error,
                    extra=structured_log_extra(
                        event="order_rejected_missing_price",
                        plan_id=order.plan_id,
                        strategy_id=order.strategy_id,
                        pair=order.pair,
                        local_order_id=order.local_id,
                    ),
                )
                return order

            notional = float(payload["volume"]) * float(price_for_notional)
            if notional < self.config.min_order_notional_usd:
                order.status = "rejected"
                order.last_error = f"Order notional ${notional:.2f} below minimum ${self.config.min_order_notional_usd:.2f}"
                logger.error(
                    order.last_error,
                    extra=structured_log_extra(
                        event="order_rejected_min_notional",
                        plan_id=order.plan_id,
                        strategy_id=order.strategy_id,
                        pair=order.pair,
                        local_order_id=order.local_id,
                        notional=notional,
                    ),
                )
                return order

        if self.config.validate_only:
            order.status = "validated"
            return order

        order.kraken_order_id = order.kraken_order_id or f"dry-{order.local_id}"
        order.status = "filled"
        order.cumulative_base_filled = order.requested_base_size
        order.avg_fill_price = price_for_notional
        order.raw_response = {
            "result": "success",
            "txid": [order.kraken_order_id],
            "filled": order.cumulative_base_filled,
            "avg_fill_price": order.avg_fill_price,
            "dry_run": True,
        }
        return order

    def cancel_order(self, order: LocalOrder) -> None:
        """Locally mark the order as canceled."""
        order.status = "canceled"

    def cancel_all_orders(self) -> None:
        """No-op for dry run adapter as there is no remote state to clear."""
        return None


class SimulationExecutionAdapter:
    """Internal simulation adapter for backtesting or integration tests.

    Provides a callback hook for fill logic.
    """

    def __init__(
        self,
        config: Optional[ExecutionConfig] = None,
        fill_callback: Optional[Callable[[LocalOrder], None]] = None,
    ):
        self.config = config or ExecutionConfig()
        self.client: Optional[KrakenRESTClient] = None
        self._fill_callback = fill_callback

    def submit_order(
        self,
        order: LocalOrder,
        pair_metadata: PairMetadata,
        latest_price: Optional[float] = None,
    ) -> LocalOrder:
        """
        Submit a simulated order.

        Immediately fills the order and triggers the configured fill callback.
        Useful for backtesting or integration tests where deterministic behavior
        is required.
        """
        price = order.requested_price
        order.kraken_order_id = order.kraken_order_id or f"sim-{order.local_id}"
        order.status = "filled"
        order.cumulative_base_filled = order.requested_base_size
        order.avg_fill_price = price
        order.updated_at = datetime.now(UTC)
        order.raw_response = {
            "result": "success",
            "txid": [order.kraken_order_id],
            "filled": order.cumulative_base_filled,
            "avg_fill_price": order.avg_fill_price,
            "simulated": True,
        }

        if self._fill_callback:
            try:
                self._fill_callback(order)
            except Exception:  # pragma: no cover - defensive logging
                logger.exception("Simulation fill callback failed")

        return order

    def cancel_order(self, order: LocalOrder) -> None:
        """Locally mark the order as canceled and update timestamp."""
        order.status = "canceled"
        order.updated_at = datetime.now(UTC)

    def cancel_all_orders(self) -> None:
        """No-op for simulation adapter."""
        return None


def get_execution_adapter(
    client: Optional[KrakenRESTClient],
    config: ExecutionConfig,
    rate_limiter: Optional[RateLimiter] = None,
) -> ExecutionAdapter:
    """Factory to create the appropriate execution adapter.

    Mapping:
      - mode='live'     -> KrakenExecutionAdapter (real orders)
      - mode='paper'    -> KrakenExecutionAdapter (validate-only orders)
      - mode='dry_run'  -> DryRunExecutionAdapter (offline)
      - mode='simulation' -> SimulationExecutionAdapter (with callback hooks)

    Default fall-through for unknown modes is DryRunExecutionAdapter.
    """
    if config.mode == "live":
        if client is None:
            raise ExecutionError("Live execution requires a KrakenRESTClient")
        return KrakenExecutionAdapter(client=client, config=config)

    if config.mode == "paper":
        if client is None:
            # Paper mode now requires a client for validation calls.
            # If strictly no client is available, one might fall back to dry-run,
            # but that masks the user intent of 'paper' (verify with Kraken).
            # We raise an error to enforce proper setup.
            raise ExecutionError(
                "Paper execution mode requires a KrakenRESTClient for validation calls."
            )
        return KrakenExecutionAdapter(client=client, config=config)

    if config.mode == "simulation":
        return SimulationExecutionAdapter(config=config)

    # mode='dry_run' or any other string defaults to offline/dry-run behavior.
    return DryRunExecutionAdapter(config=config, rate_limiter=rate_limiter)
