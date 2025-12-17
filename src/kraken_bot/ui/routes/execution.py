"""Execution-related endpoints."""

from __future__ import annotations

import logging
from typing import List

from fastapi import APIRouter, Request

from kraken_bot.config_loader import dump_runtime_overrides
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

    try:
        pre_warnings: List[str] = []
        try:
            ctx.execution_service.cancel_all()
        except Exception as cancel_exc:
            msg = f"Flatten-all preflight cancel_all failed: {cancel_exc}"
            pre_warnings.append(msg)

        # Try to sync portfolio immediately so emergency flatten uses the latest on-exchange state
        try:
            ctx.portfolio.sync()
            if not getattr(ctx.portfolio, "last_sync_ok", True):
                pre_warnings.append(
                    "Portfolio sync failed; flattening may use stale position data."
                )
        except Exception as sync_exc:
            pre_warnings.append(f"Portfolio sync before flatten failed: {sync_exc}")

        positions = ctx.portfolio.get_positions()
        plan = ctx.strategy_engine.build_emergency_flatten_plan(positions)

        # Set and persist emergency flag so the main loop picks it up and retries if we crash/restart
        ctx.session.emergency_flatten = True
        if hasattr(ctx.config, "session"):
            ctx.config.session.emergency_flatten = True
        dump_runtime_overrides(ctx.config, session=ctx.session, sections={"session"})

        result = ctx.execution_service.execute_plan(plan)
        if pre_warnings:
            # Preserve any warnings generated by execute_plan and surface preflight issues to the UI
            result.warnings = pre_warnings + list(result.warnings)
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
                plan_id=plan.plan_id if "plan" in locals() and plan else None,
            ),
        )
        return ApiEnvelope(data=None, error=str(exc))


# Note: /mode/live endpoint removed; logic consolidated into system.py POST /mode
