# src/kraken_bot/execution/adapter.py

import logging
import time
from typing import Any, Dict, Optional, Protocol

from kraken_bot.config import ExecutionConfig
from kraken_bot.connection.exceptions import RateLimitError, ServiceUnavailableError
from kraken_bot.connection.rate_limiter import RateLimiter
from kraken_bot.connection.rest_client import KrakenRESTClient
from kraken_bot.logging_config import structured_log_extra

from .exceptions import ExecutionError, OrderCancelError, OrderRejectedError
from .models import LocalOrder
from .router import build_order_payload

logger = logging.getLogger(__name__)


class ExecutionAdapter(Protocol):
    client: KrakenRESTClient
    config: ExecutionConfig

    def submit_order(self, order: LocalOrder) -> LocalOrder: ...

    def cancel_order(self, order: LocalOrder) -> None: ...

    def cancel_all_orders(self) -> None: ...


class KrakenExecutionAdapter:
    def __init__(
        self, client: KrakenRESTClient, config: Optional[ExecutionConfig] = None
    ):
        self.client = client
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

    def submit_order(self, order: LocalOrder) -> LocalOrder:
        """
        Prepare and submit an order to Kraken. The payload construction is delegated
        to routing helpers and the actual REST call is handled here.
        """
        payload: Dict[str, Any] = build_order_payload(order, self.config)
        order.raw_request = payload

        price_for_notional = payload.get("price") or order.requested_price
        if price_for_notional is not None:
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

        if self.config.dead_man_switch_seconds > 0:
            if live_trading_allowed:
                payload["expiretm"] = f"+{self.config.dead_man_switch_seconds}"
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
        """Cancel a single order identified by its Kraken order id."""
        if not order.kraken_order_id:
            raise ExecutionError("Cannot cancel order without a Kraken order id")

        try:
            resp = self.client.cancel_order(order.kraken_order_id)
        except Exception as exc:  # pragma: no cover - passthrough for client errors
            raise OrderCancelError(f"Failed to cancel order: {exc}") from exc

        errors = resp.get("error") or []
        if errors:
            raise OrderCancelError("; ".join(errors))

    def cancel_all_orders(self) -> None:
        """Cancel all open orders for the authenticated Kraken account."""
        try:
            resp = self.client.cancel_all_orders()
        except Exception as exc:  # pragma: no cover - passthrough for client errors
            raise OrderCancelError(f"Failed to cancel all orders: {exc}") from exc

        errors = resp.get("error") or []
        if errors:
            raise OrderCancelError("; ".join(errors))


class PaperExecutionAdapter:
    def __init__(
        self,
        config: Optional[ExecutionConfig] = None,
        rate_limiter: Optional[RateLimiter] = None,
    ):
        self.config = config or ExecutionConfig()
        self.client = KrakenRESTClient(rate_limiter=rate_limiter)

    def submit_order(self, order: LocalOrder) -> LocalOrder:
        payload: Dict[str, Any] = build_order_payload(order, self.config)
        order.raw_request = payload

        price_for_notional = payload.get("price") or order.requested_price
        if price_for_notional is not None:
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

        order.kraken_order_id = order.kraken_order_id or f"paper-{order.local_id}"
        order.status = "filled"
        order.cumulative_base_filled = order.requested_base_size
        order.avg_fill_price = price_for_notional
        order.raw_response = {
            "result": "success",
            "txid": [order.kraken_order_id],
            "filled": order.cumulative_base_filled,
            "avg_fill_price": order.avg_fill_price,
        }
        return order

    def cancel_order(self, order: LocalOrder) -> None:
        order.status = "canceled"

    def cancel_all_orders(self) -> None:
        return None


def get_execution_adapter(
    client: Optional[KrakenRESTClient],
    config: ExecutionConfig,
    rate_limiter: Optional[RateLimiter] = None,
) -> ExecutionAdapter:
    if config.mode == "live":
        if client is None:
            raise ExecutionError("Live execution requires a KrakenRESTClient")
        return KrakenExecutionAdapter(client=client, config=config)

    return PaperExecutionAdapter(config=config, rate_limiter=rate_limiter)
