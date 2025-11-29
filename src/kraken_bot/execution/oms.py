# src/kraken_bot/execution/oms.py

import logging
from typing import Any, Dict, List, Optional, TYPE_CHECKING
from datetime import datetime
from uuid import uuid4

from kraken_bot.strategy.models import ExecutionPlan

from kraken_bot.config import ExecutionConfig
from kraken_bot.connection.rest_client import KrakenRESTClient
from .adapter import ExecutionAdapter, KrakenExecutionAdapter, get_execution_adapter
from .exceptions import ExecutionError
from .models import ExecutionResult, LocalOrder

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from kraken_bot.portfolio.store import PortfolioStore
    from kraken_bot.strategy.models import RiskAdjustedAction


class ExecutionService:
    """Lightweight OMS façade for coordinating plan execution and order tracking."""

    def __init__(
        self,
        adapter: Optional[ExecutionAdapter] = None,
        store: Optional["PortfolioStore"] = None,
        client: Optional[KrakenRESTClient] = None,
        config: Optional[ExecutionConfig] = None,
    ):
        self.adapter = adapter or get_execution_adapter(client=client, config=config or ExecutionConfig())
        self.store = store
        self.open_orders: Dict[str, LocalOrder] = {}
        self.recent_executions: List[ExecutionResult] = []
        self.kraken_to_local: Dict[str, str] = {}

        if self.adapter.config.mode == "live":
            self._emit_live_readiness_checklist()

    def execute_plan(self, plan: ExecutionPlan) -> ExecutionResult:
        """
        Execute all risk-adjusted actions in the provided plan and return a summary result.
        Actual routing, submission, and reconciliation will be implemented in later phases.
        """
        result = ExecutionResult(plan_id=plan.plan_id, started_at=datetime.utcnow())

        eligible_actions = []
        for action in plan.actions:
            if action.blocked or action.action_type == "none":
                continue

            delta = action.target_base_size - action.current_base_size
            if delta == 0:
                continue

            eligible_actions.append(action)

        max_concurrent = getattr(self.adapter, "config", None)
        max_concurrent = getattr(max_concurrent, "max_concurrent_orders", None)
        actions_to_process = eligible_actions
        truncated_actions: List["RiskAdjustedAction"] = []
        if max_concurrent and max_concurrent > 0 and len(eligible_actions) > max_concurrent:
            actions_to_process = eligible_actions[:max_concurrent]
            truncated_actions = eligible_actions[max_concurrent:]

            logger.warning(
                "Execution concurrency limit reached; truncating actions",
                extra={
                    "event": "execution_concurrency_truncated",
                    "plan_id": plan.plan_id,
                    "max_concurrent_orders": max_concurrent,
                    "eligible_actions": len(eligible_actions),
                    "skipped_actions": len(truncated_actions),
                },
            )

        pair_target_notional: Dict[str, float] = {}
        for action in actions_to_process:
            target_notional = max(action.target_notional_usd, 0.0)
            current_target = pair_target_notional.get(action.pair, 0.0)
            pair_target_notional[action.pair] = max(current_target, target_notional)

        total_target_notional = sum(pair_target_notional.values())

        for action in actions_to_process:
            delta = action.target_base_size - action.current_base_size

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

            guardrail_reason = self._evaluate_guardrails(
                action=action,
                order_notional=max(action.target_notional_usd, 0.0),
                pair_target_notional=pair_target_notional,
                total_target_notional=total_target_notional,
                metadata=plan.metadata,
            )

            if guardrail_reason:
                order.status = "rejected"
                order.last_error = guardrail_reason
                order.updated_at = datetime.utcnow()
                result.errors.append(guardrail_reason)
                if self.store:
                    self.store.save_order(order)
                logger.warning(
                    "Order blocked by guardrail",
                    extra={
                        "event": "order_guardrail_reject",
                        "plan_id": plan.plan_id,
                        "strategy_id": action.strategy_id,
                        "pair": action.pair,
                        "reason": guardrail_reason,
                    },
                )
                result.orders.append(order)
                continue

            try:
                logger.info(
                    "Submitting order",
                    extra={
                        "event": "order_submit",
                        "plan_id": plan.plan_id,
                        "strategy_id": action.strategy_id,
                        "pair": action.pair,
                        "side": side,
                        "volume": volume,
                    },
                )
                order = self.adapter.submit_order(order)
                self.register_order(order)
                if self.store:
                    self.store.save_order(order)
                logger.info(
                    "Order submission result",
                    extra={
                        "event": "order_status",
                        "plan_id": plan.plan_id,
                        "local_id": order.local_id,
                        "kraken_order_id": order.kraken_order_id,
                        "status": order.status,
                    },
                )
            except ExecutionError as exc:
                message = str(exc)
                order.last_error = message
                order.status = "error"
                order.updated_at = datetime.utcnow()
                result.errors.append(message)

                if self.store:
                    self.store.save_order(order)

                logger.error(
                    "Order submission failed",
                    extra={
                        "event": "order_error",
                        "plan_id": plan.plan_id,
                        "local_id": order.local_id,
                        "error": message,
                    },
                )

            result.orders.append(order)

        for action in truncated_actions:
            delta = action.target_base_size - action.current_base_size
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
                status="rejected",
                last_error=(
                    f"Execution concurrency limit {max_concurrent} reached; "
                    f"skipping additional action for {action.pair}"
                ),
            )

            if self.store:
                self.store.save_order(order)

            result.errors.append(order.last_error)
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

    def load_open_orders_from_store(
        self, plan_id: Optional[str] = None, strategy_id: Optional[str] = None
    ) -> List[LocalOrder]:
        """Seed in-memory tracking with persisted open orders."""

        if not self.store or not hasattr(self.store, "get_open_orders"):
            return []

        orders = self.store.get_open_orders(plan_id=plan_id, strategy_id=strategy_id)
        for order in orders:
            self.register_order(order)

        logger.info(
            "Loaded persisted open orders",
            extra={
                "event": "open_orders_loaded",
                "count": len(orders),
                "plan_id": plan_id,
                "strategy_id": strategy_id,
            },
        )
        return orders

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

        logger.info(
            "Reconciled order state",
            extra={
                "event": "order_reconciled",
                "kraken_order_id": kraken_id,
                "local_id": order.local_id,
                "status": order.status,
                "is_closed_feed": is_closed,
            },
        )

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

    def cancel_order(self, order: LocalOrder) -> None:
        """Cancel a single order and persist the status update."""

        self.adapter.cancel_order(order)
        order.status = "canceled"
        order.updated_at = datetime.utcnow()
        self.open_orders.pop(order.local_id, None)

        if self.store:
            self.store.update_order_status(
                local_id=order.local_id,
                status=order.status,
                kraken_order_id=order.kraken_order_id,
                event_message="Canceled via OMS",
            )

        logger.info(
            "Canceled order",
            extra={
                "event": "order_canceled",
                "local_id": order.local_id,
                "kraken_order_id": order.kraken_order_id,
            },
        )

    def cancel_all(self) -> None:
        """Cancel all open orders via the adapter and mark them locally."""

        logger.warning("Canceling all open orders", extra={"event": "cancel_all_orders"})

        self.adapter.cancel_all_orders()

        # Refresh local state before marking remaining open orders as canceled.
        self.refresh_open_orders()
        self.reconcile_orders()

        for order in list(self.open_orders.values()):
            order.status = "canceled"
            order.updated_at = datetime.utcnow()

            if self.store:
                self.store.update_order_status(
                    local_id=order.local_id,
                    status=order.status,
                    kraken_order_id=order.kraken_order_id,
                    event_message="Canceled via cancel_all",
                )

            logger.info(
                "Canceled order via cancel_all",
                extra={
                    "event": "order_canceled",
                    "local_id": order.local_id,
                    "kraken_order_id": order.kraken_order_id,
                },
            )

            self.open_orders.pop(order.local_id, None)

    def cancel_orders(self, orders: List[LocalOrder]) -> None:
        for order in orders:
            try:
                self.cancel_order(order)
            except ExecutionError as exc:
                logger.error(
                    "Failed to cancel order",
                    extra={
                        "event": "order_cancel_error",
                        "local_id": order.local_id,
                        "kraken_order_id": order.kraken_order_id,
                        "error": str(exc),
                    },
                )

    def _emit_live_readiness_checklist(self) -> None:
        """Surface a readiness checklist before enabling live trading."""

        config = self.adapter.config
        config_sane = bool(config.min_order_notional_usd and config.min_order_notional_usd > 0)
        reconciliation_available = self.store is not None

        logger.warning(
            "Live readiness checklist",
            extra={
                "event": "live_readiness",
                "mode": config.mode,
                "validate_only": config.validate_only,
                "allow_live_trading": getattr(config, "allow_live_trading", False),
                "config_sane": config_sane,
                "paper_tests_completed": getattr(config, "paper_tests_completed", False),
                "reconciliation_available": reconciliation_available,
            },
        )

    def _evaluate_guardrails(
        self,
        action: "RiskAdjustedAction",
        order_notional: float,
        pair_target_notional: Dict[str, float],
        total_target_notional: float,
        metadata: Dict[str, Any],
    ) -> Optional[str]:
        """Apply lightweight notional guardrails before attempting submission."""

        config = getattr(self.adapter, "config", None)
        if not config:
            return None

        pair_limit = getattr(config, "max_pair_notional_usd", None)
        if pair_limit is not None:
            projected_pair = pair_target_notional.get(action.pair, order_notional)
            if projected_pair > pair_limit:
                return (
                    f"Projected notional ${projected_pair:,.2f} for {action.pair} "
                    f"exceeds max_pair_notional_usd ${pair_limit:,.2f}"
                )

        total_limit = getattr(config, "max_total_notional_usd", None)
        if total_limit is not None and total_target_notional > total_limit:
            risk_status = metadata.get("risk_status") if isinstance(metadata, dict) else None
            total_pct = None
            if isinstance(risk_status, dict):
                total_pct = risk_status.get("total_exposure_pct")
            pct_context = f" (current total {total_pct:.2f}% of equity)" if total_pct is not None else ""
            return (
                f"Projected aggregate notional ${total_target_notional:,.2f} exceeds "
                f"max_total_notional_usd ${total_limit:,.2f}{pct_context}"
            )

        return None
