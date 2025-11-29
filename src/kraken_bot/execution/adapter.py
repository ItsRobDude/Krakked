# src/kraken_bot/execution/adapter.py

import logging
from typing import Any, Dict, Optional

from kraken_bot.config import ExecutionConfig
from kraken_bot.connection.rest_client import KrakenRESTClient

from .exceptions import ExecutionError, OrderCancelError, OrderRejectedError
from .models import LocalOrder
from .router import build_order_payload

logger = logging.getLogger(__name__)


class KrakenExecutionAdapter:
    def __init__(self, client: KrakenRESTClient, config: Optional[ExecutionConfig] = None):
        self.client = client
        self.config = config or ExecutionConfig()

        if self.config.mode == "live" and not self.config.validate_only:
            if getattr(self.config, "allow_live_trading", False):
                logger.warning(
                    "Live trading mode ENABLED; orders will be transmitted to Kraken.",
                    extra={"event": "live_trading_enabled"},
                )
            else:
                logger.warning(
                    "Live trading requested but allow_live_trading is False; orders will be rejected.",
                    extra={"event": "live_trading_blocked"},
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
                order.last_error = (
                    f"Order notional ${notional:.2f} below minimum ${self.config.min_order_notional_usd:.2f}"
                )
                logger.error(
                    order.last_error,
                    extra={"event": "order_rejected_min_notional", "notional": notional},
                )
                return order

        should_validate = payload.get("validate") == 1
        live_trading_allowed = (
            self.config.mode == "live"
            and not self.config.validate_only
            and getattr(self.config, "allow_live_trading", False)
        )

        if not should_validate and not live_trading_allowed:
            order.status = "rejected"
            order.last_error = "Live trading disabled by configuration"
            logger.error(
                order.last_error,
                extra={"event": "order_rejected_live_guard", "mode": self.config.mode},
            )
            return order

        try:
            resp = self.client.add_order(payload)
            order.raw_response = resp
        except Exception as exc:  # pragma: no cover - passthrough for client errors
            order.status = "error"
            order.last_error = str(exc)
            raise ExecutionError(f"Failed to submit order: {exc}") from exc

        errors = resp.get("error") or []
        if errors:
            order.status = "rejected"
            order.last_error = "; ".join(errors)
            raise OrderRejectedError(order.last_error)

        if self.config.validate_only or self.config.mode != "live":
            order.status = "validated"
            return order

        txids = resp.get("txid") or []
        if txids:
            order.kraken_order_id = txids[0]
            order.status = "open"
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
