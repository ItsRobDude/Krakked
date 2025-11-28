# src/kraken_bot/execution/adapter.py

from typing import Dict, Optional

from kraken_bot.config import ExecutionConfig
from kraken_bot.connection.rest_client import KrakenRESTClient

from .exceptions import ExecutionError
from .models import LocalOrder
from .router import build_order_payload


class KrakenExecutionAdapter:
    def __init__(self, client: KrakenRESTClient, config: Optional[ExecutionConfig] = None):
        self.client = client
        self.config = config or ExecutionConfig()

    def submit_order(self, order: LocalOrder) -> LocalOrder:
        """
        Prepare and submit an order to Kraken. The payload construction is delegated
        to routing helpers and the actual REST call is handled here.
        """
        payload: Dict[str, str] = build_order_payload(order, self.config)
        raise NotImplementedError("Order submission will be implemented in a later phase")

    def cancel_order(self, order: LocalOrder) -> None:
        """Cancel a single order identified by its Kraken order id."""
        if not order.kraken_order_id:
            raise ExecutionError("Cannot cancel order without a Kraken order id")
        raise NotImplementedError("Order cancellation will be implemented in a later phase")

    def cancel_all_orders(self) -> None:
        """Cancel all open orders for the authenticated Kraken account."""
        raise NotImplementedError("Cancel-all will be implemented in a later phase")
