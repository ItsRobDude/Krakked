# src/kraken_bot/execution/oms.py

from typing import Dict, List, Optional, TYPE_CHECKING
from datetime import datetime
from uuid import uuid4

from kraken_bot.strategy.models import ExecutionPlan

from .adapter import KrakenExecutionAdapter
from .exceptions import ExecutionError
from .models import ExecutionResult, LocalOrder

if TYPE_CHECKING:
    from kraken_bot.portfolio.store import PortfolioStore


class ExecutionService:
    """Lightweight OMS façade for coordinating plan execution and order tracking."""

    def __init__(self, adapter: KrakenExecutionAdapter, store: Optional["PortfolioStore"] = None):
        self.adapter = adapter
        self.store = store
        self.open_orders: Dict[str, LocalOrder] = {}
        self.recent_executions: List[ExecutionResult] = []
        self.kraken_to_local: Dict[str, str] = {}

    def execute_plan(self, plan: ExecutionPlan) -> ExecutionResult:
        """
        Execute all risk-adjusted actions in the provided plan and return a summary result.
        Actual routing, submission, and reconciliation will be implemented in later phases.
        """
        result = ExecutionResult(plan_id=plan.plan_id, started_at=datetime.utcnow())

        for action in plan.actions:
            if action.blocked or action.action_type == "none":
                continue

            delta = action.target_base_size - action.current_base_size
            if delta == 0:
                continue

            side = "buy" if delta > 0 else "sell"
            volume = abs(delta)

            order = LocalOrder(
                local_id=str(uuid4()),
                plan_id=plan.plan_id,
                strategy_id=action.strategy_id,
                pair=action.pair,
                side=side,
                order_type=plan.metadata.get("order_type", ""),
                userref=action.userref,
                requested_base_size=volume,
                requested_price=plan.metadata.get("requested_price"),
            )

            try:
                order = self.adapter.submit_order(order)
                self.register_order(order)
                if self.store:
                    self.store.save_order(order)
            except ExecutionError as exc:
                message = str(exc)
                order.last_error = message
                order.status = "error"
                order.updated_at = datetime.utcnow()
                result.errors.append(message)

                if self.store:
                    self.store.save_order(order)

            result.orders.append(order)

        result.completed_at = datetime.utcnow()
        result.success = not result.errors
        self.record_execution_result(result)
        if self.store:
            self.store.save_execution_result(result)
        return result

    def register_order(self, order: LocalOrder) -> None:
        """Track an order locally and index it by Kraken order id when available."""
        self.open_orders[order.local_id] = order
        if order.kraken_order_id:
            self.kraken_to_local[order.kraken_order_id] = order.local_id

    def get_open_orders(self) -> List[LocalOrder]:
        """Return a snapshot list of currently open or pending local orders."""
        return list(self.open_orders.values())

    def get_recent_executions(self, limit: int = 10) -> List[ExecutionResult]:
        """Return the most recent execution results, limited to the provided window."""
        if limit <= 0:
            return []
        return self.recent_executions[-limit:]

    def record_execution_result(self, result: ExecutionResult, max_records: int = 100) -> None:
        """Append a completed execution result to the in-memory buffer."""
        self.recent_executions.append(result)
        if len(self.recent_executions) > max_records:
            self.recent_executions = self.recent_executions[-max_records:]

    def update_order_status(self, local_id: str, status: str, kraken_order_id: Optional[str] = None) -> None:
        """Update the status of a tracked order and mirror the Kraken mapping."""
        order = self.open_orders.get(local_id)
        if not order:
            return

        order.status = status
        order.updated_at = datetime.utcnow()

        if kraken_order_id:
            order.kraken_order_id = kraken_order_id
            self.kraken_to_local[kraken_order_id] = local_id

        if status in {"filled", "canceled", "rejected", "error"}:
            self.open_orders.pop(local_id, None)

        if self.store:
            self.store.update_order_status(
                local_id=local_id,
                status=status,
                kraken_order_id=kraken_order_id,
                cumulative_base_filled=order.cumulative_base_filled,
                avg_fill_price=order.avg_fill_price,
                last_error=order.last_error,
                raw_response=order.raw_response,
            )

    def refresh_open_orders(self) -> None:
        """Pull open orders from Kraken and reconcile with local state."""
        userrefs = {o.userref for o in self.open_orders.values() if o.userref is not None}
        params = {"userref": ",".join(str(u) for u in userrefs)} if userrefs else None

        try:
            remote = self.adapter.client.get_open_orders(params=params)
        except Exception:
            return

        for kraken_id, payload in (remote.get("open") or {}).items():
            self._sync_remote_order(kraken_id, payload, is_closed=False)

    def reconcile_orders(self) -> None:
        """Pull closed orders from Kraken and mark any matching local orders finalized."""
        try:
            remote = self.adapter.client.get_closed_orders()
        except Exception:
            return

        for kraken_id, payload in (remote.get("closed") or {}).items():
            self._sync_remote_order(kraken_id, payload, is_closed=True)

    def _sync_remote_order(self, kraken_id: str, payload: dict, is_closed: bool) -> None:
        """Update a local order based on Kraken order payload."""
        userref = payload.get("userref")
        order = self._resolve_local_order(kraken_id, userref)
        if not order:
            return

        self.register_order(order)
        order.kraken_order_id = kraken_id
        order.status = payload.get("status") or ("closed" if is_closed else "open")
        order.updated_at = datetime.utcnow()
        order.raw_response = payload

        vol_exec = payload.get("vol_exec")
        try:
            order.cumulative_base_filled = float(vol_exec) if vol_exec is not None else order.cumulative_base_filled
        except (TypeError, ValueError):
            pass

        price = payload.get("price") or payload.get("price_avg")
        try:
            order.avg_fill_price = float(price) if price is not None else order.avg_fill_price
        except (TypeError, ValueError):
            pass

        if is_closed or order.status in {"canceled", "closed", "expired", "rejected", "filled"}:
            self.open_orders.pop(order.local_id, None)

        if self.store:
            self.store.update_order_status(
                local_id=order.local_id,
                status=order.status,
                kraken_order_id=kraken_id,
                cumulative_base_filled=order.cumulative_base_filled,
                avg_fill_price=order.avg_fill_price,
                last_error=order.last_error,
                raw_response=order.raw_response,
            )

    def _resolve_local_order(self, kraken_id: str, userref: Optional[int]) -> Optional[LocalOrder]:
        """Find or reload a LocalOrder using known references."""
        local_id = self.kraken_to_local.get(kraken_id)
        if local_id and local_id in self.open_orders:
            return self.open_orders[local_id]

        if userref is not None:
            for order in self.open_orders.values():
                if order.userref == userref:
                    self.kraken_to_local[kraken_id] = order.local_id
                    return order

        if self.store and hasattr(self.store, "get_order_by_reference"):
            order = self.store.get_order_by_reference(kraken_order_id=kraken_id, userref=userref)
            if order:
                return order

        return None
