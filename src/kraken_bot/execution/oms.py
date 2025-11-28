# src/kraken_bot/execution/oms.py

from typing import Dict, List, Optional
from datetime import datetime

from kraken_bot.strategy.models import ExecutionPlan

from .adapter import KrakenExecutionAdapter
from .models import ExecutionResult, LocalOrder


class ExecutionService:
    """Lightweight OMS façade for coordinating plan execution and order tracking."""

    def __init__(self, adapter: KrakenExecutionAdapter):
        self.adapter = adapter
        self.open_orders: Dict[str, LocalOrder] = {}
        self.recent_executions: List[ExecutionResult] = []
        self.kraken_to_local: Dict[str, str] = {}

    def execute_plan(self, plan: ExecutionPlan) -> ExecutionResult:
        """
        Execute all risk-adjusted actions in the provided plan and return a summary result.
        Actual routing, submission, and reconciliation will be implemented in later phases.
        """
        result = ExecutionResult(plan_id=plan.plan_id, started_at=datetime.utcnow())
        raise NotImplementedError("Plan execution logic will be implemented in a later phase")

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
