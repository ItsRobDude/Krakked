# src/krakked/execution/oms.py

import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional
from uuid import uuid4

from krakked.config import ExecutionConfig
from krakked.connection.rate_limiter import RateLimiter
from krakked.connection.rest_client import KrakenRESTClient
from krakked.logging_config import structured_log_extra
from krakked.market_data.api import MarketDataAPI
from krakked.strategy.models import ExecutionPlan

from .adapter import ExecutionAdapter, get_execution_adapter
from .exceptions import ExecutionError
from .models import ExecutionResult, LocalOrder
from .router import build_order_from_plan_action
from .userref import resolve_userref

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from krakked.portfolio.store import PortfolioStore
    from krakked.strategy.models import RiskAdjustedAction, RiskStatus


class ExecutionService:
    """Coordinate execution routing, persistence, and reconciliation for a plan.

    The service builds :class:`LocalOrder` objects from risk-adjusted actions,
    applies lightweight notional guardrails, and delegates submission to the
    configured :class:`ExecutionAdapter`. Orders are tracked in-memory, mapped to
    Kraken IDs when available, persisted via the optional :class:`PortfolioStore`,
    and reconciled against Kraken open/closed order feeds. When running in live
    mode, a readiness checklist is emitted to the logs before any submission.
    """

    def __init__(
        self,
        adapter: Optional[ExecutionAdapter] = None,
        store: Optional["PortfolioStore"] = None,
        client: Optional[KrakenRESTClient] = None,
        config: Optional[ExecutionConfig] = None,
        market_data: MarketDataAPI | None = None,
        rate_limiter: Optional[RateLimiter] = None,
        risk_status_provider: Optional[Callable[[], "RiskStatus"]] = None,
    ):
        self.adapter = adapter or get_execution_adapter(
            client=client, config=config or ExecutionConfig(), rate_limiter=rate_limiter
        )
        self.store = store
        if market_data is None:
            raise ValueError("market_data is required for ExecutionService")
        self.market_data = market_data
        self.open_orders: Dict[str, LocalOrder] = {}
        self.recent_executions: List[ExecutionResult] = []
        self.kraken_to_local: Dict[str, str] = {}
        self._risk_status_provider = risk_status_provider
        self._last_dead_man_refresh_at: Optional[datetime] = None

        adapter_config = getattr(self.adapter, "config", None)
        self._execution_config = adapter_config or config or ExecutionConfig()
        mode = getattr(self._execution_config, "mode", None)

        if mode == "live" and self._risk_status_provider is None:
            logger.error(
                "ExecutionService initialized in live mode without risk_status_provider; refusing to start.",
                extra=structured_log_extra(event="risk_status_missing_live"),
            )
            raise ValueError(
                "risk_status_provider is required when execution.mode='live'"
            )

        if mode == "live":
            self._emit_live_readiness_checklist()

    def recommended_dead_man_refresh_interval_seconds(self) -> Optional[float]:
        """Return the recommended refresh cadence for exchange dead-man heartbeats."""

        timeout_seconds = getattr(self._execution_config, "dead_man_switch_seconds", 0)
        if not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
            return None

        if self._execution_config.mode != "live" or self._execution_config.validate_only:
            return None

        if not getattr(self._execution_config, "allow_live_trading", False):
            return None

        return max(1.0, float(timeout_seconds) / 2.0)

    def refresh_dead_man_switch(
        self, *, force: bool = False, now: Optional[datetime] = None
    ) -> bool:
        """Refresh Kraken's dead-man switch heartbeat when live trading is active."""

        interval_seconds = self.recommended_dead_man_refresh_interval_seconds()
        if interval_seconds is None:
            return False

        if not getattr(self._execution_config, "paper_tests_completed", False):
            return False

        client = getattr(self.adapter, "client", None)
        if client is None or not hasattr(client, "cancel_all_orders_after"):
            logger.warning(
                "Dead man switch configured but execution client does not support heartbeat refresh",
                extra=structured_log_extra(
                    event="dead_man_switch_unavailable",
                    timeout_seconds=self._execution_config.dead_man_switch_seconds,
                ),
            )
            return False

        current_time = now or datetime.now(UTC)
        if (
            not force
            and self._last_dead_man_refresh_at is not None
            and (current_time - self._last_dead_man_refresh_at).total_seconds()
            < interval_seconds
        ):
            return False

        try:
            client.cancel_all_orders_after(self._execution_config.dead_man_switch_seconds)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to refresh dead man switch heartbeat",
                extra=structured_log_extra(
                    event="dead_man_switch_error",
                    timeout_seconds=self._execution_config.dead_man_switch_seconds,
                    error=str(exc),
                ),
            )
            return False

        self._last_dead_man_refresh_at = current_time
        logger.info(
            "Dead man switch heartbeat refreshed",
            extra=structured_log_extra(
                event="dead_man_switch_refreshed",
                timeout_seconds=self._execution_config.dead_man_switch_seconds,
                forced=force,
            ),
        )
        return True

    def _kill_switch_active(self, plan: Optional[ExecutionPlan] = None) -> bool:
        """Return True when execution should be blocked by the kill switch."""

        plan_id = plan.plan_id if plan is not None else None

        if plan and plan.emergency_reduce_only:
            if all(action.action_type in {"reduce", "close"} for action in plan.actions):
                return False
            logger.error(
                "Emergency reduce-only plan contained non-reducing actions; refusing kill-switch bypass",
                extra=structured_log_extra(
                    event="invalid_emergency_reduce_only_plan",
                    plan_id=plan.plan_id,
                ),
            )

        # Missing provider is always a hard block — tests rely on this and it's
        # safer than blindly executing with an unknown risk state.
        if not self._risk_status_provider:
            mode = getattr(self._execution_config, "mode", None)
            logger.error(
                "Risk status provider missing; forcing kill switch",
                extra=structured_log_extra(
                    event="risk_missing",
                    execution_mode=mode,
                    plan_id=plan_id,
                ),
            )
            return True

        mode = getattr(self._execution_config, "mode", None)

        try:
            status = self._risk_status_provider()
        except Exception:  # noqa: BLE001
            logger.exception(
                "Risk status provider failed",
                extra=structured_log_extra(
                    event="risk_provider_error",
                    execution_mode=mode,
                    plan_id=plan_id,
                ),
            )

            if mode == "live":
                logger.error(
                    "Kill switch forced due to risk provider error in live mode",
                    extra=structured_log_extra(
                        event="risk_provider_error_kill_switch",
                        execution_mode=mode,
                        plan_id=plan_id,
                    ),
                )
                return True

            return False

        kill_switch_active = bool(getattr(status, "kill_switch_active", False))

        if kill_switch_active:
            logger.warning(
                "Kill switch active; blocking plan execution",
                extra=structured_log_extra(
                    event="kill_switch_active",
                    execution_mode=mode,
                    plan_id=plan_id,
                ),
            )

        return kill_switch_active

    def _create_rejected_order(
        self,
        plan: ExecutionPlan,
        action: "RiskAdjustedAction",
        reason: str,
        error_suffix: Optional[str] = None,
    ) -> LocalOrder:
        """Helper to create, persist, and return a rejected LocalOrder."""
        delta = action.target_base_size - action.current_base_size
        side = "buy" if delta > 0 else "sell"
        volume = abs(delta)

        last_error = f"{reason} {error_suffix}" if error_suffix else reason

        order = LocalOrder(
            local_id=str(uuid4()),
            plan_id=plan.plan_id,
            strategy_id=action.strategy_id,
            pair=action.pair,
            side=side,
            order_type=plan.metadata.get("order_type", ""),
            userref=resolve_userref(action.userref),
            requested_base_size=volume,
            requested_price=plan.metadata.get("requested_price"),
            status="rejected",
            last_error=last_error,
        )
        order.updated_at = datetime.now(UTC)

        if self.store:
            self.store.save_order(order)

        return order

    def execute_plan(self, plan: ExecutionPlan) -> ExecutionResult:
        """
            Execute a plan by building orders, enforcing guardrails, and routing submissions.

            * Skips blocked/"none" actions and no-op deltas.
            * Enforces max_concurrent_orders, marking extra actions as rejected.
            * Applies notional guardrails before any submission attempt.
            * Submits eligible orders through the adapter; any :class:`ExecutionError`
              is captured and persisted on the associated :class:`LocalOrder`.
        * Persists orders and the aggregate :class:`ExecutionResult` when a store
          is configured.
        * Records an in-memory execution result and registers each order for
          later reconciliation via :meth:`refresh_open_orders` or
          :meth:`reconcile_orders`.
        """
        started_at = datetime.now(UTC)
        result = ExecutionResult(plan_id=plan.plan_id, started_at=started_at)

        adapter_config = self._execution_config

        max_age = getattr(adapter_config, "max_plan_age_seconds", None)
        if isinstance(max_age, int) and max_age > 0:
            generated_at = getattr(plan, "generated_at", None)

            if isinstance(generated_at, datetime):
                if generated_at.tzinfo is None:
                    generated_at = generated_at.replace(tzinfo=UTC)
                plan_age_seconds = (started_at - generated_at).total_seconds()
            else:
                # Missing or invalid generated_at is treated as infinitely stale.
                plan_age_seconds = float("inf")

            if plan_age_seconds > max_age:
                reason = (
                    "Execution plan age "
                    f"{plan_age_seconds:.1f}s exceeds max_plan_age_seconds={max_age}s; "
                    "rejecting without order submission"
                )
                logger.warning(
                    "Execution plan stale; blocking execution",
                    extra=structured_log_extra(
                        event="plan_stale",
                        plan_id=plan.plan_id,
                        plan_age_seconds=plan_age_seconds,
                        max_plan_age_seconds=max_age,
                    ),
                )
                result.errors.append(reason)
                result.completed_at = datetime.now(UTC)
                result.success = False
                self.record_execution_result(result)
                if self.store:
                    self.store.save_execution_result(result)
                return result

        eligible_actions = []
        for action in plan.actions:
            if action.blocked or action.action_type == "none":
                continue

            # Calculate delta using Decimal to avoid 0.1 + 0.2 != 0.3 issues.
            # We treat differences smaller than a tiny epsilon as zero (noise).
            try:
                tgt = Decimal(str(action.target_base_size))
                cur = Decimal(str(action.current_base_size))
                delta_dec = tgt - cur

                # If the difference is extremely small (e.g. < 1 satoshi for BTC), ignore it.
                # Kraken's smallest divisible unit is usually 1e-8.
                # We use 1e-9 as a safe "zero" threshold.
                if abs(delta_dec) < Decimal("1e-9"):
                    continue

                # Convert back to float for the rest of the system
                delta = float(delta_dec)
            except Exception:
                # Fallback to standard float math if something bizarre happens
                delta = action.target_base_size - action.current_base_size
                if delta == 0:
                    continue

            eligible_actions.append(action)

        if self._kill_switch_active(plan=plan):
            blocked_reason = "Execution blocked by kill switch"
            logger.warning(
                blocked_reason,
                extra=structured_log_extra(
                    event="kill_switch_block",
                    plan_id=plan.plan_id,
                    eligible_actions=len(eligible_actions),
                ),
            )
            result.errors.append(blocked_reason)

            for action in eligible_actions:
                rejected_order = self._create_rejected_order(
                    plan,
                    action,
                    blocked_reason,
                    error_suffix="(kill_switch_active)",
                )
                result.orders.append(rejected_order)

            result.completed_at = datetime.now(UTC)
            result.success = False
            self.record_execution_result(result)
            if self.store:
                self.store.save_execution_result(result)
            return result

        max_concurrent = getattr(adapter_config, "max_concurrent_orders", None)
        actions_to_process = eligible_actions
        truncated_actions: List["RiskAdjustedAction"] = []
        if (
            isinstance(max_concurrent, int)
            and max_concurrent > 0
            and len(eligible_actions) > max_concurrent
        ):
            actions_to_process = eligible_actions[:max_concurrent]
            truncated_actions = eligible_actions[max_concurrent:]

            logger.warning(
                "Execution concurrency limit reached; truncating actions",
                extra=structured_log_extra(
                    event="execution_concurrency_truncated",
                    plan_id=plan.plan_id,
                    max_concurrent_orders=max_concurrent,
                    eligible_actions=len(eligible_actions),
                    skipped_actions=len(truncated_actions),
                ),
            )

        pair_target_notional: Dict[str, float] = {}
        for action in actions_to_process:
            target_notional = max(action.target_notional_usd, 0.0)
            current_target = pair_target_notional.get(action.pair, 0.0)
            pair_target_notional[action.pair] = max(current_target, target_notional)

        total_target_notional = sum(pair_target_notional.values())

        projected_total_exposure = self._calculate_projected_exposure(
            plan=plan,
            actions_to_process=actions_to_process,
            total_target_notional=total_target_notional,
        )

        for action in actions_to_process:
            try:
                pair_metadata = self.market_data.get_pair_metadata_or_raise(action.pair)
            except ValueError as exc:
                logger.error(
                    "Execution aborted: missing metadata for pair",
                    extra=structured_log_extra(
                        event="execution_missing_metadata",
                        plan_id=plan.plan_id,
                        pair=action.pair,
                    ),
                )
                result.errors.append(str(exc))
                result.completed_at = datetime.now(UTC)
                result.success = False
                self.record_execution_result(result)
                if self.store:
                    self.store.save_execution_result(result)
                return result

            try:
                order, routing_warning = build_order_from_plan_action(
                    action=action,
                    plan=plan,
                    pair_metadata=pair_metadata,
                    config=adapter_config,
                    market_data=self.market_data,
                )
            except ValueError as exc:
                result.errors.append(str(exc))
                logger.warning(
                    "Order build rejected due to invalid size",
                    extra=structured_log_extra(
                        event="order_build_invalid_size",
                        plan_id=plan.plan_id,
                        strategy_id=action.strategy_id,
                        pair=action.pair,
                        error=str(exc),
                    ),
                )
                continue

            if routing_warning:
                result.warnings.append(routing_warning)
                result.errors.append(routing_warning)
                logger.warning(
                    "Order routing failed",
                    extra=structured_log_extra(
                        event="order_routing_failed",
                        plan_id=plan.plan_id,
                        strategy_id=action.strategy_id,
                        pair=action.pair,
                        reason=routing_warning,
                    ),
                )
                continue

            if order is None:
                missing_order_reason = f"Failed to build order for {action.pair}"
                result.errors.append(missing_order_reason)
                logger.warning(
                    "Order build returned no order",
                    extra=structured_log_extra(
                        event="order_build_missing",
                        plan_id=plan.plan_id,
                        strategy_id=action.strategy_id,
                        pair=action.pair,
                    ),
                )
                continue

            guardrail_reason = self._evaluate_guardrails(
                action=action,
                order_notional=max(action.target_notional_usd, 0.0),
                pair_target_notional=pair_target_notional,
                projected_total_exposure=projected_total_exposure,
                metadata=plan.metadata,
            )

            if guardrail_reason:
                order.status = "rejected"
                order.last_error = guardrail_reason
                order.updated_at = datetime.now(UTC)
                result.errors.append(guardrail_reason)
                if self.store:
                    self.store.save_order(order)
                logger.warning(
                    "Order blocked by guardrail",
                    extra=structured_log_extra(
                        event="order_guardrail_reject",
                        plan_id=plan.plan_id,
                        strategy_id=action.strategy_id,
                        pair=action.pair,
                        reason=guardrail_reason,
                    ),
                )
                result.orders.append(order)
                continue

            latest_price: Optional[float] = None
            min_notional = getattr(adapter_config, "min_order_notional_usd", 0)

            # Only require latest_price when min_notional > 0 AND side=='buy' AND not risk_reducing.
            # If risk reducing, we let it through without a price (adapter will skip the notional check).
            if (
                order.requested_price is None
                and min_notional > 0
                and order.side == "buy"
                and not order.risk_reducing
            ):
                try:
                    latest_price = self.market_data.get_latest_price(order.pair)
                except Exception as exc:  # pragma: no cover
                    latest_price = None  # Ensure explicit None on failure
                    # If live, we must fail closed.
                    if adapter_config.mode == "live":
                        reason = f"Latest price unavailable in live mode: {exc}"
                        logger.error(
                            "Order rejected: latest price error (live)",
                            extra=structured_log_extra(
                                event="order_rejected_price_error",
                                plan_id=plan.plan_id,
                                strategy_id=action.strategy_id,
                                pair=action.pair,
                                error=str(exc),
                            ),
                        )
                        order.status = "rejected"
                        order.last_error = reason
                        result.errors.append(reason)
                        if self.store:
                            self.store.save_order(order)
                        result.orders.append(order)
                        continue

                    # In paper/sim, attempt fallback or proceed
                    logger.warning(
                        "Latest price missing in non-live mode; attempting fallback",
                        extra=structured_log_extra(
                            event="price_fallback_attempt",
                            pair=order.pair,
                            error=str(exc),
                        ),
                    )
                    fallback_price: Optional[float] = None
                    try:
                        ticker = self.market_data.get_best_bid_ask(order.pair)
                        if ticker and ticker.get("bid") and ticker.get("ask"):
                            fallback_price = (ticker["bid"] + ticker["ask"]) / 2.0
                    except Exception:
                        pass

                    if fallback_price is not None:
                        latest_price = fallback_price
                    else:
                        logger.warning(
                            "Proceeding with missing price (paper/sim)",
                            extra=structured_log_extra(
                                event="price_missing_allowed", pair=order.pair
                            ),
                        )

            try:
                logger.info(
                    "Submitting order",
                    extra=structured_log_extra(
                        event="order_routed",
                        plan_id=plan.plan_id,
                        strategy_id=action.strategy_id,
                        pair=action.pair,
                        side=order.side,
                        volume=order.requested_base_size,
                        local_order_id=order.local_id,
                    ),
                )
                order = self.adapter.submit_order(
                    order, pair_metadata, latest_price=latest_price
                )

                # Only track non-validated orders in memory
                if order.status != "validated":
                    self.register_order(order)

                if self.store:
                    self.store.save_order(order)

                logger.info(
                    "Order submission result",
                    extra=structured_log_extra(
                        event="order_status",
                        plan_id=plan.plan_id,
                        strategy_id=action.strategy_id,
                        pair=action.pair,
                        local_order_id=order.local_id,
                        kraken_order_id=order.kraken_order_id,
                        status=order.status,
                    ),
                )
            except ExecutionError as exc:
                message = str(exc)
                order.last_error = message
                order.status = "error"
                order.updated_at = datetime.now(UTC)
                result.errors.append(message)

                if self.store:
                    self.store.save_order(order)

                logger.error(
                    "Order submission failed",
                    extra=structured_log_extra(
                        event="execution_error",
                        plan_id=plan.plan_id,
                        strategy_id=action.strategy_id,
                        pair=action.pair,
                        local_order_id=order.local_id,
                        error=message,
                    ),
                )

            result.orders.append(order)

        for action in truncated_actions:
            reason = (
                f"Execution concurrency limit {max_concurrent} reached; "
                f"skipping additional action for {action.pair}"
            )
            order = self._create_rejected_order(plan, action, reason)
            result.errors.append(
                order.last_error or "execution concurrency limit reached"
            )
            result.orders.append(order)

        result.completed_at = datetime.now(UTC)
        result.success = not result.errors
        self.record_execution_result(result)
        if self.store:
            self.store.save_execution_result(result)
        return result

    def register_order(self, order: LocalOrder) -> None:
        """Track an order locally and index it by Kraken order id when available."""
        # Track only actively working orders.
        if order.status not in {"pending", "open", "partially_filled"}:
            return

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
            extra=structured_log_extra(
                event="open_orders_loaded",
                plan_id=plan_id,
                strategy_id=strategy_id,
                count=len(orders),
            ),
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

    def record_execution_result(
        self, result: ExecutionResult, max_records: int = 100
    ) -> None:
        """Append a completed execution result to the in-memory buffer."""
        self.recent_executions.append(result)
        if len(self.recent_executions) > max_records:
            self.recent_executions = self.recent_executions[-max_records:]

    def update_order_status(
        self, local_id: str, status: str, kraken_order_id: Optional[str] = None
    ) -> None:
        """Update the status of a tracked order and mirror the Kraken mapping."""
        order = self.open_orders.get(local_id)
        if not order:
            return

        order.status = status
        order.updated_at = datetime.now(UTC)

        if kraken_order_id:
            order.kraken_order_id = kraken_order_id
            self.kraken_to_local[kraken_order_id] = local_id

        if status in {"filled", "canceled", "rejected", "error", "validated"}:
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
        if self.adapter.client is None:
            return

        try:
            # Kraken's OpenOrders userref filter accepts a single integer. Fetch all
            # open orders and reconcile locally so multi-userref sessions work.
            remote = self.adapter.client.get_open_orders()
        except Exception:
            return

        for kraken_id, payload in (remote.get("open") or {}).items():
            self._sync_remote_order(kraken_id, payload, is_closed=False)

    def reconcile_orders(self) -> None:
        """Pull closed orders from Kraken and mark any matching local orders finalized."""
        if self.adapter.client is None:
            return

        try:
            remote = self.adapter.client.get_closed_orders()
        except Exception:
            return

        for kraken_id, payload in (remote.get("closed") or {}).items():
            self._sync_remote_order(kraken_id, payload, is_closed=True)

    def _sync_remote_order(
        self, kraken_id: str, payload: dict, is_closed: bool
    ) -> None:
        """Update a local order based on Kraken order payload."""
        userref_raw = payload.get("userref")
        userref = None
        if userref_raw is not None:
            try:
                userref = int(userref_raw)
            except (TypeError, ValueError):
                userref = None

        order = self._resolve_local_order(kraken_id, userref)
        if not order:
            return

        self.register_order(order)
        order.kraken_order_id = kraken_id
        if userref is not None:
            order.userref = userref
        order.status = payload.get("status") or ("closed" if is_closed else "open")
        order.updated_at = datetime.now(UTC)
        order.raw_response = payload

        vol_exec = payload.get("vol_exec")
        try:
            order.cumulative_base_filled = (
                float(vol_exec)
                if vol_exec is not None
                else order.cumulative_base_filled
            )
        except (TypeError, ValueError):
            pass

        price = payload.get("price") or payload.get("price_avg")
        try:
            order.avg_fill_price = (
                float(price) if price is not None else order.avg_fill_price
            )
        except (TypeError, ValueError):
            pass

        if is_closed or order.status in {
            "canceled",
            "closed",
            "expired",
            "rejected",
            "filled",
            "validated",
        }:
            self.open_orders.pop(order.local_id, None)

        logger.info(
            "Reconciled order state",
            extra=structured_log_extra(
                event="order_reconciled",
                plan_id=order.plan_id,
                strategy_id=order.strategy_id,
                pair=order.pair,
                kraken_order_id=kraken_id,
                local_order_id=order.local_id,
                status=order.status,
                is_closed_feed=is_closed,
            ),
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

    def _resolve_local_order(
        self, kraken_id: str, userref: Optional[int]
    ) -> Optional[LocalOrder]:
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
            stored_order = self.store.get_order_by_reference(
                kraken_order_id=kraken_id, userref=userref
            )
            if stored_order:
                return stored_order

        return None

    def cancel_order(self, order: LocalOrder) -> None:
        """Cancel a single order and persist the status update."""

        self.adapter.cancel_order(order)
        order.status = "canceled"
        order.updated_at = datetime.now(UTC)
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
            extra=structured_log_extra(
                event="order_canceled",
                plan_id=order.plan_id,
                strategy_id=order.strategy_id,
                pair=order.pair,
                local_order_id=order.local_id,
                kraken_order_id=order.kraken_order_id,
            ),
        )

    def cancel_all(self) -> None:
        """Cancel all open orders via the adapter and mark them locally."""

        logger.warning(
            "Canceling all open orders",
            extra=structured_log_extra(event="cancel_all_orders"),
        )

        self.adapter.cancel_all_orders()

        open_before_refresh = len(self.open_orders)

        # Refresh local state before marking remaining open orders as canceled.
        self.refresh_open_orders()
        open_after_refresh = len(self.open_orders)
        self.reconcile_orders()

        logger.info(
            "Reconciled orders after cancel_all",
            extra=structured_log_extra(
                event="cancel_all_reconcile",
                open_orders_before_refresh=open_before_refresh,
                open_orders_after_refresh=open_after_refresh,
                open_orders_remaining=len(self.open_orders),
            ),
        )

        for order in list(self.open_orders.values()):
            order.status = "canceled"
            order.updated_at = datetime.now(UTC)

            if self.store:
                self.store.update_order_status(
                    local_id=order.local_id,
                    status=order.status,
                    kraken_order_id=order.kraken_order_id,
                    event_message="Canceled via cancel_all",
                )

            logger.info(
                "Canceled order via cancel_all",
                extra=structured_log_extra(
                    event="order_canceled",
                    plan_id=order.plan_id,
                    strategy_id=order.strategy_id,
                    pair=order.pair,
                    local_order_id=order.local_id,
                    kraken_order_id=order.kraken_order_id,
                ),
            )

            self.open_orders.pop(order.local_id, None)

    def cancel_orders(self, orders: List[LocalOrder]) -> None:
        for order in orders:
            try:
                self.cancel_order(order)
            except ExecutionError as exc:
                logger.error(
                    "Failed to cancel order",
                    extra=structured_log_extra(
                        event="order_cancel_error",
                        plan_id=order.plan_id,
                        strategy_id=order.strategy_id,
                        pair=order.pair,
                        local_order_id=order.local_id,
                        kraken_order_id=order.kraken_order_id,
                        error=str(exc),
                    ),
                )

    def _emit_live_readiness_checklist(self) -> None:
        """Surface a readiness checklist before enabling live trading."""

        config = self.adapter.config
        config_sane = bool(
            config.min_order_notional_usd and config.min_order_notional_usd > 0
        )
        reconciliation_available = self.store is not None

        logger.warning(
            "Live readiness checklist",
            extra=structured_log_extra(
                event="live_readiness",
                mode=config.mode,
                validate_only=config.validate_only,
                allow_live_trading=getattr(config, "allow_live_trading", False),
                config_sane=config_sane,
                paper_tests_completed=getattr(config, "paper_tests_completed", False),
                reconciliation_available=reconciliation_available,
            ),
        )

    def _calculate_projected_exposure(
        self,
        plan: ExecutionPlan,
        actions_to_process: List["RiskAdjustedAction"],
        total_target_notional: float,
    ) -> float:
        """Calculate total portfolio exposure accounting for passive assets.

        Logic (Drift-Proof):
        Instead of subtracting live active value from snapshot total (which causes artifacts
        if prices drift), explicitly sum the snapshot value of assets *not* in the plan.
        """
        # Default to just the plan's notional if store is missing or calc fails
        projected_total_exposure = total_target_notional

        if not self.store:
            return projected_total_exposure

        try:
            snapshots = self.store.get_snapshots(limit=5)
            if not snapshots:
                return projected_total_exposure

            latest = snapshots[0]

            # Align the snapshot we use with the plan generation time when possible.
            generated_at = getattr(plan, "generated_at", None)
            if isinstance(generated_at, datetime):
                if generated_at.tzinfo is None:
                    generated_at = generated_at.replace(tzinfo=UTC)
                plan_ts = int(generated_at.timestamp())
                latest = min(snapshots, key=lambda s: abs(s.timestamp - plan_ts))

            # 1. Identify active pairs directly from plan actions
            # We use source_pair matching which is faster and aligns with snapshot creation.
            active_pairs = {a.pair for a in actions_to_process}

            # 2. Sum value of assets NOT in the plan (Passive)
            # We use the snapshot's valuation to ensure consistency and avoid
            # phantom exposure from price drift between snapshot time and now.
            passive_exposure = 0.0
            if latest.asset_valuations:
                for av in latest.asset_valuations:
                    # If the asset came from a pair we are currently trading, exclude it (it's active).
                    if av.source_pair and av.source_pair in active_pairs:
                        continue

                    # Otherwise, it is a passive holding.
                    passive_exposure += av.value_base
            else:
                # Fallback for legacy snapshots without granular valuations:
                # Use Total Risk (Equity - Cash) is safest but strict.
                # Let's try the approximation but with 0 active deduction (Conservative).
                passive_exposure = max(0.0, latest.equity_base - latest.cash_base)

            return passive_exposure + total_target_notional

        except Exception as e:
            logger.warning(
                "Failed to calculate portfolio-aware exposure; falling back to plan notional",
                extra=structured_log_extra(
                    event="guardrail_exposure_calc_failed", error=str(e)
                ),
            )
            return projected_total_exposure

    def _evaluate_guardrails(
        self,
        action: "RiskAdjustedAction",
        order_notional: float,
        pair_target_notional: Dict[str, float],
        projected_total_exposure: float,
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
        if total_limit is not None and projected_total_exposure > total_limit:
            risk_status = (
                metadata.get("risk_status") if isinstance(metadata, dict) else None
            )
            total_pct = None
            if isinstance(risk_status, dict):
                total_pct = risk_status.get("total_exposure_pct")
            pct_context = (
                f" (current total {total_pct:.2f}% of equity)"
                if total_pct is not None
                else ""
            )
            return (
                f"Projected total portfolio exposure ${projected_total_exposure:,.2f} exceeds "
                f"max_total_notional_usd ${total_limit:,.2f}{pct_context}"
            )

        return None
