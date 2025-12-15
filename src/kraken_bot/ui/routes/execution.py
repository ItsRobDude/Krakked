"""Execution-related endpoints."""

from __future__ import annotations

import logging
from typing import List

import yaml
from fastapi import APIRouter, Request
from pydantic import BaseModel

from kraken_bot.config import dump_runtime_overrides, get_config_dir
from kraken_bot.execution.models import ExecutionResult, LocalOrder
from kraken_bot.secrets import unlock_secrets
from kraken_bot.ui.logging import build_request_log_extra
from kraken_bot.ui.models import ApiEnvelope, ExecutionResultPayload, OpenOrderPayload

logger = logging.getLogger(__name__)

router = APIRouter()


class LiveModePayload(BaseModel):
    password: str
    confirmation: str


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


def _backup_file(path):
    import time
    if not path.exists():
        return
    timestamp = int(time.time())
    backup_path = path.with_name(f"{path.name}.{timestamp}.bak")
    try:
        with open(path, "rb") as src, open(backup_path, "wb") as dst:
            dst.write(src.read())
    except Exception as e:
        logger.error(f"Failed to backup {path}: {e}")

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
        positions = ctx.portfolio.get_positions()
        plan = ctx.strategy_engine.build_emergency_flatten_plan(positions)

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
                plan_id=plan.plan_id if "plan" in locals() and plan else None,
            ),
        )
        return ApiEnvelope(data=None, error=str(exc))


@router.post("/mode/live", response_model=ApiEnvelope[dict])
async def enable_live_trading(
    payload: LiveModePayload, request: Request
) -> ApiEnvelope[dict]:
    """
    Guarded endpoint to enable live trading.
    Requires master password and explicit confirmation string.
    """
    ctx = _context(request)
    if ctx.config.ui.read_only:
        return ApiEnvelope(data=None, error="UI is in read-only mode")

    if payload.confirmation != "ENABLE LIVE TRADING":
        return ApiEnvelope(data=None, error="Invalid confirmation phrase")

    if ctx.session.active:
        return ApiEnvelope(data=None, error="Session must be stopped before enabling live trading")

    # 1. Verify Password
    try:
        unlock_secrets(payload.password)
    except Exception:
        logger.warning(
            "Live trading enable failed: invalid password",
            extra=build_request_log_extra(request, event="live_enable_auth_failed")
        )
        return ApiEnvelope(data=None, error="Invalid password")

    # 2. Update Configuration
    config_dir = get_config_dir()
    profile_name = ctx.session.profile_name

    # We update the active profile (or main config) to set execution mode
    target_path = None

    if profile_name:
         profiles_entry = ctx.config.profiles.get(profile_name)
         if profiles_entry:
             # Resolve path
             from pathlib import Path
             p_path_str = profiles_entry.config_path
             p_path = Path(p_path_str)
             if not p_path.is_absolute():
                 p_path = config_dir / p_path
             if p_path.exists():
                 target_path = p_path

    if not target_path:
        target_path = config_dir / "config.yaml"

    try:
        _backup_file(target_path)

        with open(target_path, "r") as f:
            data = yaml.safe_load(f) or {}

        execution = data.get("execution", {})
        execution["mode"] = "live"
        execution["validate_only"] = False
        execution["allow_live_trading"] = True
        execution["paper_tests_completed"] = True
        data["execution"] = execution

        with open(target_path, "w") as f:
            yaml.safe_dump(data, f)

        # 3. Trigger Reload
        ctx.reinitialize_event.set()

        logger.info(
            "Live trading ENABLED via guarded UI flow",
            extra=build_request_log_extra(request, event="live_trading_enabled", profile=profile_name)
        )

        return ApiEnvelope(data={"status": "live_enabled", "reloading": True}, error=None)

    except Exception as exc:
        logger.exception("Failed to enable live trading")
        return ApiEnvelope(data=None, error=str(exc))
