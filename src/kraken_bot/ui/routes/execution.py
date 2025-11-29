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
