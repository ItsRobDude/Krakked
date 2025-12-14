"""Strategy state endpoints."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request

from kraken_bot.config import dump_runtime_overrides
from kraken_bot.strategy.risk_profiles import profile_to_definition
from kraken_bot.ui.logging import build_request_log_extra
from kraken_bot.ui.models import (
    ApiEnvelope,
    StrategyPerformancePayload,
    StrategyStatePayload,
)

logger = logging.getLogger(__name__)


def _coerce_bool(value: Any, *, field: str = "value") -> bool:
    """Coerce common JSON-ish representations into a real bool."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "t", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "f", "no", "n", "off"}:
            return False
    raise ValueError(f"{field} must be a boolean")


def _set_strategy_enabled(ctx: AppContext, strategy_id: str, enabled: bool) -> None:
    state = ctx.strategy_engine.strategy_states.get(strategy_id)
    if state is None:
        raise KeyError(strategy_id)
    state.enabled = enabled

    strat_cfg = ctx.config.strategies.configs.get(strategy_id)
    if strat_cfg is not None:
        strat_cfg.enabled = enabled

    enabled_list = ctx.config.strategies.enabled
    if enabled:
        if strategy_id not in enabled_list:
            enabled_list.append(strategy_id)
    else:
        if strategy_id in enabled_list:
            enabled_list.remove(strategy_id)

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

    enabled_raw = payload.get("enabled")
    if enabled_raw is None:
        return ApiEnvelope(data=None, error="'enabled' field is required")

    try:
        enabled = _coerce_bool(enabled_raw)
    except ValueError:
        return ApiEnvelope(data=None, error="'enabled' must be a boolean")

    try:
        _set_strategy_enabled(ctx, strategy_id, enabled)
        dump_runtime_overrides(ctx.config)
        logger.info(
            "Strategy enabled state updated",
            extra=build_request_log_extra(
                request,
                event="strategy_enabled_updated",
                strategy_id=strategy_id,
                enabled=enabled,
            ),
        )
        return ApiEnvelope(data={"strategy_id": strategy_id, "enabled": enabled}, error=None)
    except KeyError:
        return ApiEnvelope(data=None, error="Strategy not found")
    except Exception as exc:
        logger.exception(
            "Failed to update strategy enabled state",
            extra=build_request_log_extra(
                request, event="strategy_enabled_update_failed", strategy_id=strategy_id
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

        updated_fields: dict[str, Any] = {}
        for field, value in payload.items():
            if field == "params" and isinstance(value, dict):
                strat_cfg.params.update(value)
                updated_fields[field] = value
                if strategy_id in ctx.strategy_engine.strategy_states:
                    ctx.strategy_engine.strategy_states[strategy_id].params.update(
                        value
                    )
            elif field == "enabled":
                try:
                    enabled = _coerce_bool(value)
                except ValueError:
                    return ApiEnvelope(data=None, error="'enabled' must be a boolean")

                _set_strategy_enabled(ctx, strategy_id, enabled)
                updated_fields[field] = enabled
            elif hasattr(strat_cfg, field) and field not in {"name", "type"}:
                setattr(strat_cfg, field, value)
                updated_fields[field] = value

        params = payload.get("params") or {}
        profile = params.get("risk_profile")
        if profile:
            rp = profile_to_definition(profile)
            ctx.config.risk.max_per_strategy_pct[strategy_id] = rp.max_per_strategy_pct
            ctx.strategy_engine.risk_engine.config.max_per_strategy_pct = dict(
                ctx.config.risk.max_per_strategy_pct
            )

            updated_fields["risk_profile"] = profile
            updated_fields["max_per_strategy_pct"] = rp.max_per_strategy_pct

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
