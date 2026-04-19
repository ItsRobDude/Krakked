"""Strategy state endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from pydantic import ValidationError

from krakked.config import dump_runtime_overrides
from krakked.strategy.catalog import CANONICAL_STRATEGIES
from krakked.strategy.risk_profiles import profile_to_definition
from krakked.ui.logging import build_request_log_extra
from krakked.ui.models import (
    ApiEnvelope,
    StrategyConfigPatchPayload,
    StrategyPerformancePayload,
    StrategyStatePayload,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _context(request: Request):
    return request.app.state.context


def _strategy_label(ctx, strategy_id: str) -> str:
    canonical = CANONICAL_STRATEGIES.get(strategy_id)
    if canonical:
        return canonical.label

    strat_cfg = ctx.config.strategies.configs.get(strategy_id)
    if strat_cfg and strat_cfg.name and strat_cfg.name != strategy_id:
        return strat_cfg.name

    return strategy_id.replace("_", " ").replace("-", " ").title()


@router.get("/", response_model=ApiEnvelope[list[StrategyStatePayload]])
async def get_strategies(request: Request) -> ApiEnvelope[list[StrategyStatePayload]]:
    ctx = _context(request)
    try:
        strategies = [
            StrategyStatePayload(label=_strategy_label(ctx, state.strategy_id), **state.__dict__)
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
            StrategyPerformancePayload(**record.__dict__) for record in perf.values()
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

    if not isinstance(enabled, bool):
        return ApiEnvelope(data=None, error="'enabled' must be a boolean")

    try:
        ctx.strategy_engine.set_strategy_enabled(strategy_id, enabled)

        dump_runtime_overrides(ctx.config)
        logger.info(
            "Strategy enable state updated",
            extra=build_request_log_extra(
                request,
                event="strategy_enabled_updated",
                strategy_id=strategy_id,
                enabled=enabled,
            ),
        )
        return ApiEnvelope(
            data={"strategy_id": strategy_id, "enabled": enabled}, error=None
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
        raw_payload = await request.json()
        if not isinstance(raw_payload, dict):
            raise ValueError("Strategy config payload must be a JSON object")
        payload = StrategyConfigPatchPayload.model_validate(raw_payload)
    except ValidationError as exc:
        messages = "; ".join(error["msg"] for error in exc.errors())
        return ApiEnvelope(data=None, error=messages)
    except Exception:  # pragma: no cover - malformed body
        return ApiEnvelope(data=None, error="Invalid JSON payload")

    try:
        strat_cfg = ctx.config.strategies.configs.get(strategy_id)
        if not strat_cfg:
            return ApiEnvelope(data=None, error="Strategy not found")

        if not payload.model_fields_set:
            return ApiEnvelope(data=None, error="No strategy config fields provided")

        updated_fields: dict[str, object] = {}

        if "strategy_weight" in payload.model_fields_set:
            if payload.strategy_weight is None:
                return ApiEnvelope(data=None, error="'strategy_weight' cannot be null")
            strat_cfg.strategy_weight = payload.strategy_weight
            updated_fields["strategy_weight"] = payload.strategy_weight
            if strategy_id in ctx.strategy_engine.strategy_states:
                ctx.strategy_engine.strategy_states[
                    strategy_id
                ].configured_weight = payload.strategy_weight

        if "params" in payload.model_fields_set and payload.params is None:
            return ApiEnvelope(data=None, error="'params' cannot be null")

        profile = payload.params.risk_profile if payload.params else None
        if profile:
            strat_cfg.params["risk_profile"] = profile
            updated_fields["params"] = {"risk_profile": profile}
            if strategy_id in ctx.strategy_engine.strategy_states:
                ctx.strategy_engine.strategy_states[strategy_id].params[
                    "risk_profile"
                ] = profile
            rp = profile_to_definition(profile)
            ctx.config.risk.max_per_strategy_pct[strategy_id] = rp.max_per_strategy_pct
            ctx.strategy_engine.risk_engine.config.max_per_strategy_pct = dict(
                ctx.config.risk.max_per_strategy_pct
            )

            updated_fields["risk_profile"] = profile
            updated_fields["max_per_strategy_pct"] = rp.max_per_strategy_pct

        if payload.params and payload.params.continuous_learning is not None:
            strat_cfg.params["continuous_learning"] = payload.params.continuous_learning
            updated_fields.setdefault("params", {})
            if isinstance(updated_fields["params"], dict):
                updated_fields["params"]["continuous_learning"] = (
                    payload.params.continuous_learning
                )
            if strategy_id in ctx.strategy_engine.strategy_states:
                ctx.strategy_engine.strategy_states[strategy_id].params[
                    "continuous_learning"
                ] = payload.params.continuous_learning

        ctx.strategy_engine.refresh_strategy_weight_state()
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
