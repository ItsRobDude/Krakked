"""Execution-related endpoints."""

from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, Request

from kraken_bot.execution.models import ExecutionResult, LocalOrder
from kraken_bot.ui.logging import build_request_log_extra
from kraken_bot.ui.models import ApiEnvelope, ExecutionResultPayload, OpenOrderPayload

logger = logging.getLogger(__name__)

router = APIRouter()


def _context(request: Request):
    return request.app.state.context


def _serialize_order(order: LocalOrder) -> OpenOrderPayload:
    return OpenOrderPayload(
        local_id=order.local_id,
        plan_id=order.plan_id,
        strategy_id=order.strategy_id,
        pair=order.pair,
        side=order.side,
        order_type=order.order_type,
        kraken_order_id=order.kraken_order_id,
        userref=order.userref,
        requested_base_size=order.requested_base_size,
        requested_price=order.requested_price,
        status=order.status,
        created_at=order.created_at,
        updated_at=order.updated_at,
        cumulative_base_filled=order.cumulative_base_filled,
        avg_fill_price=order.avg_fill_price,
        last_error=order.last_error,
        raw_request=order.raw_request,
        raw_response=order.raw_response,
    )


def _serialize_execution_result(result: ExecutionResult) -> ExecutionResultPayload:
    return ExecutionResultPayload(
        plan_id=result.plan_id,
        started_at=result.started_at,
        completed_at=result.completed_at,
        success=result.success,
        orders=[_serialize_order(order) for order in result.orders],
        errors=result.errors,
        warnings=result.warnings,
    )


@router.get("/open_orders", response_model=ApiEnvelope[List[OpenOrderPayload]])
async def get_open_orders(request: Request) -> ApiEnvelope[List[OpenOrderPayload]]:
    ctx = _context(request)
    try:
        open_orders = [
            _serialize_order(order) for order in ctx.execution_service.get_open_orders()
        ]
        return ApiEnvelope(data=open_orders, error=None)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception(
            "Failed to fetch open orders",
            extra=build_request_log_extra(request, event="open_orders_failed"),
        )
        return ApiEnvelope(data=None, error=str(exc))


@router.get(
    "/recent_executions", response_model=ApiEnvelope[List[ExecutionResultPayload]]
)
async def get_recent_executions(
    request: Request,
) -> ApiEnvelope[List[ExecutionResultPayload]]:
    ctx = _context(request)
    try:
        executions = [
            _serialize_execution_result(result)
            for result in ctx.execution_service.get_recent_executions()
        ]
        return ApiEnvelope(data=executions, error=None)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception(
            "Failed to fetch recent executions",
            extra=build_request_log_extra(request, event="recent_executions_failed"),
        )
        return ApiEnvelope(data=None, error=str(exc))


@router.post("/cancel_all", response_model=ApiEnvelope[bool])
async def cancel_all_orders(request: Request) -> ApiEnvelope[bool]:
    ctx = _context(request)
    if ctx.config.ui.read_only:
        logger.warning(
            "Cancel all blocked: UI read-only",
            extra=build_request_log_extra(request, event="cancel_all_blocked"),
        )
        return ApiEnvelope(data=None, error="UI is in read-only mode")

    try:
        ctx.execution_service.cancel_all()
        logger.info(
            "All orders canceled via API",
            extra=build_request_log_extra(request, event="cancel_all_triggered"),
        )
        return ApiEnvelope(data=True, error=None)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception(
            "Failed to cancel all orders",
            extra=build_request_log_extra(request, event="cancel_all_failed"),
        )
        return ApiEnvelope(data=None, error=str(exc))


@router.post("/cancel/{local_id}", response_model=ApiEnvelope[bool])
async def cancel_order(local_id: str, request: Request) -> ApiEnvelope[bool]:
    ctx = _context(request)
    if ctx.config.ui.read_only:
        logger.warning(
            "Cancel order blocked: UI read-only",
            extra=build_request_log_extra(
                request, event="cancel_order_blocked", local_id=local_id
            ),
        )
        return ApiEnvelope(data=None, error="UI is in read-only mode")

    order = ctx.execution_service.open_orders.get(local_id)
    if not order:
        return ApiEnvelope(data=None, error="Order not found")

    try:
        ctx.execution_service.cancel_order(order)
        logger.info(
            "Order canceled via API",
            extra=build_request_log_extra(
                request,
                event="cancel_order_triggered",
                local_id=local_id,
                plan_id=order.plan_id,
                strategy_id=order.strategy_id,
            ),
        )
        return ApiEnvelope(data=True, error=None)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception(
            "Failed to cancel order",
            extra=build_request_log_extra(
                request,
                event="cancel_order_failed",
                local_id=local_id,
                plan_id=order.plan_id,
            ),
        )
        return ApiEnvelope(data=None, error=str(exc))


@router.post("/flatten_all", response_model=ApiEnvelope[ExecutionResultPayload])
async def flatten_all_positions(
    request: Request,
) -> ApiEnvelope[ExecutionResultPayload]:
    ctx = _context(request)
    if ctx.config.ui.read_only:
        logger.warning(
            "Flatten all blocked: UI read-only",
            extra=build_request_log_extra(request, event="flatten_all_blocked"),
        )
        return ApiEnvelope(data=None, error="UI is in read-only mode")

    plan: Optional[ExecutionPlan] = None
    try:
        actions = []
        positions = ctx.portfolio.get_positions()
        for position in positions:
            if position.base_size == 0:
                continue
            actions.append(
                {
                    "pair": position.pair,
                    "strategy_id": position.strategy_tag or "manual",
                    "target_base_size": 0.0,
                    "current_base_size": position.base_size,
                }
            )

        plan = (
            ctx.strategy_engine._build_flatten_plan(actions)
            if hasattr(ctx.strategy_engine, "_build_flatten_plan")
            else None
        )
        if plan is None:
            from datetime import datetime, timezone

            from kraken_bot.strategy.models import ExecutionPlan, RiskAdjustedAction

            now = datetime.now(timezone.utc)
            risk_actions = [
                RiskAdjustedAction(
                    pair=item["pair"],
                    strategy_id=item["strategy_id"],
                    action_type="close",
                    target_base_size=item["target_base_size"],
                    target_notional_usd=0.0,
                    current_base_size=item["current_base_size"],
                    reason="Manual flatten all",
                    blocked=False,
                    blocked_reasons=[],
                    strategy_tag=item["strategy_id"],
                    userref=None,
                    risk_limits_snapshot={},
                )
                for item in actions
            ]
            plan = ExecutionPlan(
                plan_id=f"flatten_{int(now.timestamp())}",
                generated_at=now,
                actions=risk_actions,
            )

        if plan is None:
            raise ValueError("No execution plan generated")

        result = ctx.execution_service.execute_plan(plan)
        logger.info(
            "Flatten all triggered via API",
            extra=build_request_log_extra(
                request,
                event="flatten_all_triggered",
                plan_id=plan.plan_id,
                actions=len(plan.actions),
            ),
        )
        return ApiEnvelope(data=_serialize_execution_result(result), error=None)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception(
            "Failed to flatten all positions",
            extra=build_request_log_extra(
                request,
                event="flatten_all_failed",
                plan_id=plan.plan_id if plan else None,
            ),
        )
        return ApiEnvelope(data=None, error=str(exc))
