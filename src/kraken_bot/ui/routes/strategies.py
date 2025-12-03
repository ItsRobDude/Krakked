"""Strategy state endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from kraken_bot.config import dump_runtime_overrides
from kraken_bot.ui.logging import build_request_log_extra
from kraken_bot.ui.models import (
    ApiEnvelope,
    StrategyPerformancePayload,
    StrategyStatePayload,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _context(request: Request):
    return request.app.state.context


@router.get("/", response_model=ApiEnvelope[list[StrategyStatePayload]])
async def get_strategies(request: Request) -> ApiEnvelope[list[StrategyStatePayload]]:
    ctx = _context(request)
    try:
        strategies = [
            StrategyStatePayload(**state.__dict__)
            for state in ctx.strategy_engine.get_strategy_state()
        ]
        return ApiEnvelope(data=strategies, error=None)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception(
            "Failed to fetch strategies",
            extra=build_request_log_extra(request, event="strategies_fetch_failed"),
        )
        return ApiEnvelope(data=None, error=str(exc))


@router.get(
    "/performance", response_model=ApiEnvelope[list[StrategyPerformancePayload]]
)
async def get_strategy_performance(
    request: Request,
) -> ApiEnvelope[list[StrategyPerformancePayload]]:
    ctx = _context(request)
    try:
        perf = ctx.portfolio.get_strategy_performance()
        payload = [
            StrategyPerformancePayload(**record.__dict__)
            for record in perf.values()
        ]
        return ApiEnvelope(data=payload, error=None)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception(
            "Failed to fetch strategy performance",
            extra=build_request_log_extra(
                request, event="strategy_performance_fetch_failed"
            ),
        )
        return ApiEnvelope(data=None, error=str(exc))


@router.patch("/{strategy_id}/enabled", response_model=ApiEnvelope[dict])
async def set_strategy_enabled(strategy_id: str, request: Request) -> ApiEnvelope[dict]:
    ctx = _context(request)
    if ctx.config.ui.read_only:
        logger.warning(
            "Strategy enable toggle blocked: UI read-only",
            extra=build_request_log_extra(
                request, event="strategy_toggle_blocked", strategy_id=strategy_id
            ),
        )
        return ApiEnvelope(data=None, error="UI is in read-only mode")

    try:
        payload = await request.json()
    except Exception:  # pragma: no cover - malformed body
        return ApiEnvelope(data=None, error="Invalid JSON payload")

    enabled = payload.get("enabled")
    if enabled is None:
        return ApiEnvelope(data=None, error="'enabled' field is required")

    try:
        if strategy_id in ctx.strategy_engine.strategy_states:
            ctx.strategy_engine.strategy_states[strategy_id].enabled = bool(enabled)
        if strategy_id in ctx.config.strategies.enabled and not enabled:
            ctx.config.strategies.enabled.remove(strategy_id)
        elif enabled and strategy_id not in ctx.config.strategies.enabled:
            ctx.config.strategies.enabled.append(strategy_id)

        dump_runtime_overrides(ctx.config)
        logger.info(
            "Strategy enable state updated",
            extra=build_request_log_extra(
                request,
                event="strategy_enabled_updated",
                strategy_id=strategy_id,
                enabled=bool(enabled),
            ),
        )
        return ApiEnvelope(
            data={"strategy_id": strategy_id, "enabled": bool(enabled)}, error=None
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception(
            "Failed to update strategy enabled state",
            extra=build_request_log_extra(
                request, event="strategy_toggle_failed", strategy_id=strategy_id
            ),
        )
        return ApiEnvelope(data=None, error=str(exc))


@router.patch("/{strategy_id}/config", response_model=ApiEnvelope[dict])
async def update_strategy_config(
    strategy_id: str, request: Request
) -> ApiEnvelope[dict]:
    ctx = _context(request)
    if ctx.config.ui.read_only:
        logger.warning(
            "Strategy config update blocked: UI read-only",
            extra=build_request_log_extra(
                request, event="strategy_config_blocked", strategy_id=strategy_id
            ),
        )
        return ApiEnvelope(data=None, error="UI is in read-only mode")

    try:
        payload = await request.json()
    except Exception:  # pragma: no cover - malformed body
        return ApiEnvelope(data=None, error="Invalid JSON payload")

    try:
        strat_cfg = ctx.config.strategies.configs.get(strategy_id)
        if not strat_cfg:
            return ApiEnvelope(data=None, error="Strategy not found")

        updated_fields = {}
        for field, value in payload.items():
            if field == "params" and isinstance(value, dict):
                strat_cfg.params.update(value)
                updated_fields[field] = value
                if strategy_id in ctx.strategy_engine.strategy_states:
                    ctx.strategy_engine.strategy_states[strategy_id].params.update(
                        value
                    )
            elif hasattr(strat_cfg, field) and field not in {"name", "type"}:
                setattr(strat_cfg, field, value)
                updated_fields[field] = value

        dump_runtime_overrides(ctx.config)
        logger.info(
            "Strategy config updated",
            extra=build_request_log_extra(
                request,
                event="strategy_config_updated",
                strategy_id=strategy_id,
                fields=updated_fields,
            ),
        )

        return ApiEnvelope(data=strat_cfg.__dict__, error=None)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception(
            "Failed to update strategy config",
            extra=build_request_log_extra(
                request, event="strategy_config_update_failed", strategy_id=strategy_id
            ),
        )
        return ApiEnvelope(data=None, error=str(exc))
