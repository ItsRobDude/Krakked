"""Execution-related endpoints."""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any, Dict

from fastapi import APIRouter, Request

from kraken_bot.execution.models import ExecutionResult, LocalOrder

logger = logging.getLogger(__name__)

router = APIRouter()


def _context(request: Request):
    return request.app.state.context


def _serialize_order(order: LocalOrder) -> Dict[str, Any]:
    data = asdict(order)
    data["created_at"] = order.created_at.isoformat()
    data["updated_at"] = order.updated_at.isoformat()
    return data


def _serialize_execution_result(result: ExecutionResult) -> Dict[str, Any]:
    return {
        "plan_id": result.plan_id,
        "started_at": result.started_at.isoformat(),
        "completed_at": result.completed_at.isoformat() if result.completed_at else None,
        "success": result.success,
        "orders": [_serialize_order(order) for order in result.orders],
        "errors": result.errors,
        "warnings": result.warnings,
    }


@router.get("/open_orders")
async def get_open_orders(request: Request):
    ctx = _context(request)
    try:
        open_orders = [asdict(order) for order in ctx.execution_service.get_open_orders()]
        return {"data": open_orders, "error": None}
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Failed to fetch open orders")
        return {"data": None, "error": str(exc)}


@router.get("/recent_executions")
async def get_recent_executions(request: Request):
    ctx = _context(request)
    try:
        executions = [
            _serialize_execution_result(result)
            for result in ctx.execution_service.get_recent_executions()
        ]
        return {"data": executions, "error": None}
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Failed to fetch recent executions")
        return {"data": None, "error": str(exc)}


@router.post("/cancel_all")
async def cancel_all_orders(request: Request):
    ctx = _context(request)
    if ctx.config.ui.read_only:
        logger.warning("Cancel all blocked: UI read-only", extra={"event": "cancel_all_blocked"})
        return {"data": None, "error": "UI is in read-only mode"}

    try:
        ctx.execution_service.cancel_all()
        logger.info("All orders canceled via API", extra={"event": "cancel_all_triggered"})
        return {"data": True, "error": None}
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Failed to cancel all orders")
        return {"data": None, "error": str(exc)}


@router.post("/cancel/{local_id}")
async def cancel_order(local_id: str, request: Request):
    ctx = _context(request)
    if ctx.config.ui.read_only:
        logger.warning(
            "Cancel order blocked: UI read-only",
            extra={"event": "cancel_order_blocked", "local_id": local_id},
        )
        return {"data": None, "error": "UI is in read-only mode"}

    order = ctx.execution_service.open_orders.get(local_id)
    if not order:
        return {"data": None, "error": "Order not found"}

    try:
        ctx.execution_service.cancel_order(order)
        logger.info(
            "Order canceled via API",
            extra={"event": "cancel_order_triggered", "local_id": local_id},
        )
        return {"data": True, "error": None}
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Failed to cancel order", extra={"local_id": local_id})
        return {"data": None, "error": str(exc)}


@router.post("/flatten_all")
async def flatten_all_positions(request: Request):
    ctx = _context(request)
    if ctx.config.ui.read_only:
        logger.warning("Flatten all blocked: UI read-only", extra={"event": "flatten_all_blocked"})
        return {"data": None, "error": "UI is in read-only mode"}

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

        plan = ctx.strategy_engine._build_flatten_plan(actions) if hasattr(ctx.strategy_engine, "_build_flatten_plan") else None
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
            plan = ExecutionPlan(plan_id=f"flatten_{int(now.timestamp())}", generated_at=now, actions=risk_actions)

        result = ctx.execution_service.execute_plan(plan)
        logger.info(
            "Flatten all triggered via API",
            extra={"event": "flatten_all_triggered", "actions": len(plan.actions)},
        )
        return {"data": _serialize_execution_result(result), "error": None}
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Failed to flatten all positions")
        return {"data": None, "error": str(exc)}
