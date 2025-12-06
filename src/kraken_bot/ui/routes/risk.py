"""Risk monitoring endpoints."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, Request

from kraken_bot.config import dump_runtime_overrides
from kraken_bot.ui.logging import build_request_log_extra
from kraken_bot.ui.models import (
    ApiEnvelope,
    KillSwitchPayload,
    RiskConfigPayload,
    RiskDecisionPayload,
    RiskStatusPayload,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# These presets are applied by /risk/preset/{name} and surfaced in the UI.
# Adjust values to suit deployment risk tolerances.
PRESET_PROFILES: Dict[str, Dict[str, Any]] = {
    "conservative": {
        "risk": {
            "max_risk_per_trade_pct": 0.5,
            "max_portfolio_risk_pct": 5.0,
            "max_daily_drawdown_pct": 5.0,
        },
        "per_strategy": {
            "default": {"risk_profile": "conservative"},
            "trend_core": {"risk_profile": "conservative", "cap_pct": 25.0},
            "dca_overlay": {"risk_profile": "conservative", "cap_pct": 10.0},
            "vol_breakout": {"risk_profile": "conservative", "cap_pct": 5.0},
            "majors_mean_rev": {"risk_profile": "conservative", "cap_pct": 5.0},
            "rs_rotation": {"risk_profile": "conservative", "cap_pct": 10.0},
            "ai_predictor": {"risk_profile": "conservative", "cap_pct": 5.0},
            "ai_predictor_alt": {"risk_profile": "conservative", "cap_pct": 2.5},
            "ai_regression": {"risk_profile": "conservative", "cap_pct": 2.5},
        },
    },
    "balanced": {
        "risk": {
            "max_risk_per_trade_pct": 1.0,
            "max_portfolio_risk_pct": 10.0,
            "max_daily_drawdown_pct": 10.0,
        },
        "per_strategy": {
            "default": {"risk_profile": "balanced"},
            "trend_core": {"risk_profile": "balanced", "cap_pct": 40.0},
            "dca_overlay": {"risk_profile": "balanced", "cap_pct": 20.0},
            "vol_breakout": {"risk_profile": "balanced", "cap_pct": 10.0},
            "majors_mean_rev": {"risk_profile": "balanced", "cap_pct": 10.0},
            "rs_rotation": {"risk_profile": "balanced", "cap_pct": 20.0},
            "ai_predictor": {"risk_profile": "balanced", "cap_pct": 10.0},
            "ai_predictor_alt": {"risk_profile": "balanced", "cap_pct": 5.0},
            "ai_regression": {"risk_profile": "balanced", "cap_pct": 5.0},
        },
    },
    "aggressive": {
        "risk": {
            "max_risk_per_trade_pct": 1.5,
            "max_portfolio_risk_pct": 15.0,
            "max_daily_drawdown_pct": 15.0,
        },
        "per_strategy": {
            "default": {"risk_profile": "aggressive"},
            "trend_core": {"risk_profile": "aggressive", "cap_pct": 50.0},
            "dca_overlay": {"risk_profile": "aggressive", "cap_pct": 25.0},
            "vol_breakout": {"risk_profile": "aggressive", "cap_pct": 15.0},
            "majors_mean_rev": {"risk_profile": "aggressive", "cap_pct": 15.0},
            "rs_rotation": {"risk_profile": "aggressive", "cap_pct": 25.0},
            "ai_predictor": {"risk_profile": "aggressive", "cap_pct": 15.0},
            "ai_predictor_alt": {"risk_profile": "aggressive", "cap_pct": 10.0},
            "ai_regression": {"risk_profile": "aggressive", "cap_pct": 10.0},
        },
    },
    "degen": {
        "risk": {
            "max_risk_per_trade_pct": 2.0,
            "max_portfolio_risk_pct": 25.0,
            "max_daily_drawdown_pct": 25.0,
        },
        "per_strategy": {
            # degen still uses the "aggressive" risk_profile definition under the hood,
            # it just gives the spicy stuff more headroom.
            "default": {"risk_profile": "aggressive"},
            "trend_core": {"risk_profile": "aggressive", "cap_pct": 50.0},
            "dca_overlay": {"risk_profile": "aggressive", "cap_pct": 25.0},
            "vol_breakout": {"risk_profile": "aggressive", "cap_pct": 20.0},
            "majors_mean_rev": {"risk_profile": "aggressive", "cap_pct": 15.0},
            "rs_rotation": {"risk_profile": "aggressive", "cap_pct": 25.0},
            "ai_predictor": {"risk_profile": "aggressive", "cap_pct": 20.0},
            "ai_predictor_alt": {"risk_profile": "aggressive", "cap_pct": 15.0},
            "ai_regression": {"risk_profile": "aggressive", "cap_pct": 15.0},
        },
    },
}


def _context(request: Request):
    return request.app.state.context


def _serialize_decision(record) -> RiskDecisionPayload:
    try:
        raw_data = json.loads(record.raw_json) if record.raw_json else {}
    except Exception:
        raw_data = {}

    block_reasons: List[str] = []
    if raw_data.get("blocked_reasons"):
        block_reasons = [str(reason) for reason in raw_data["blocked_reasons"]]
    elif record.block_reason:
        block_reasons = [r for r in record.block_reason.split(";") if r]

    decided_at = datetime.fromtimestamp(record.time, tz=timezone.utc)

    return RiskDecisionPayload(
        decided_at=decided_at,
        plan_id=record.plan_id,
        strategy_id=record.strategy_name,
        pair=record.pair,
        action_type=record.action_type,
        blocked=record.blocked,
        block_reasons=block_reasons,
        kill_switch_active=record.kill_switch_active,
    )


@router.get("/status", response_model=ApiEnvelope[RiskStatusPayload])
async def get_risk_status(request: Request) -> ApiEnvelope[RiskStatusPayload]:
    ctx = _context(request)
    try:
        status = ctx.strategy_engine.get_risk_status()
        data = RiskStatusPayload(**status.__dict__)
        return ApiEnvelope(data=data, error=None)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception(
            "Failed to fetch risk status",
            extra=build_request_log_extra(request, event="risk_status_failed"),
        )
        return ApiEnvelope(data=None, error=str(exc))


@router.get("/decisions", response_model=ApiEnvelope[List[RiskDecisionPayload]])
async def get_risk_decisions(
    request: Request, limit: int = 50
) -> ApiEnvelope[List[RiskDecisionPayload]]:
    ctx = _context(request)
    try:
        decisions = ctx.portfolio.get_decisions(limit=limit)
        payload = [_serialize_decision(record) for record in decisions]
        return ApiEnvelope(data=payload, error=None)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception(
            "Failed to fetch risk decisions",
            extra=build_request_log_extra(request, event="risk_decisions_failed"),
        )
        return ApiEnvelope(data=None, error=str(exc))


@router.get("/config", response_model=ApiEnvelope[RiskConfigPayload])
async def get_risk_config(request: Request) -> ApiEnvelope[RiskConfigPayload]:
    ctx = _context(request)
    try:
        data = RiskConfigPayload(**ctx.config.risk.__dict__)
        return ApiEnvelope(data=data, error=None)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception(
            "Failed to fetch risk config",
            extra=build_request_log_extra(request, event="risk_config_fetch_failed"),
        )
        return ApiEnvelope(data=None, error=str(exc))


@router.patch("/config", response_model=ApiEnvelope[RiskConfigPayload])
async def update_risk_config(request: Request) -> ApiEnvelope[RiskConfigPayload]:
    ctx = _context(request)
    if ctx.config.ui.read_only:
        logger.warning(
            "Risk config update blocked: UI read-only",
            extra=build_request_log_extra(request, event="risk_config_blocked"),
        )
        return ApiEnvelope(data=None, error="UI is in read-only mode")

    try:
        payload = await request.json()
    except Exception:  # pragma: no cover - malformed body
        return ApiEnvelope(data=None, error="Invalid JSON payload")

    updated_fields = {}
    try:
        for field, value in payload.items():
            if hasattr(ctx.config.risk, field):
                setattr(ctx.config.risk, field, value)
                setattr(ctx.strategy_engine.risk_engine.config, field, value)
                updated_fields[field] = value

        if updated_fields:
            dump_runtime_overrides(ctx.config)
            logger.info(
                "Updated risk config",
                extra=build_request_log_extra(
                    request, event="risk_config_updated", fields=updated_fields
                ),
            )

        return ApiEnvelope(
            data=RiskConfigPayload(**ctx.config.risk.__dict__), error=None
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception(
            "Failed to update risk config",
            extra=build_request_log_extra(request, event="risk_config_update_failed"),
        )
        return ApiEnvelope(data=None, error=str(exc))


@router.post("/preset/{name}", response_model=ApiEnvelope[RiskConfigPayload])
async def apply_risk_preset(
    name: str, request: Request
) -> ApiEnvelope[RiskConfigPayload]:
    ctx = _context(request)
    if ctx.config.ui.read_only:
        logger.warning(
            "Risk preset application blocked: UI read-only",
            extra=build_request_log_extra(
                request, event="risk_preset_blocked", name=name
            ),
        )
        return ApiEnvelope(data=None, error="UI is in read-only mode")

    if name not in PRESET_PROFILES:
        return ApiEnvelope(data=None, error="Unknown preset")

    profile = PRESET_PROFILES[name]

    try:
        risk_updates = profile.get("risk", {})
        per_strategy_settings = profile.get("per_strategy", {})
        default_strategy_settings = per_strategy_settings.get("default", {})

        updated_fields = {}

        for field, value in risk_updates.items():
            if hasattr(ctx.config.risk, field):
                setattr(ctx.config.risk, field, value)
                setattr(ctx.strategy_engine.risk_engine.config, field, value)
                updated_fields[field] = value

        for strategy_id, strat_cfg in ctx.config.strategies.configs.items():
            settings = per_strategy_settings.get(strategy_id, default_strategy_settings)
            if not settings:
                continue

            if strat_cfg.params is None:
                strat_cfg.params = {}

            risk_profile = settings.get("risk_profile")
            if risk_profile:
                strat_cfg.params["risk_profile"] = risk_profile
                if strategy_id in ctx.strategy_engine.strategy_states:
                    ctx.strategy_engine.strategy_states[strategy_id].params[
                        "risk_profile"
                    ] = risk_profile

            cap_pct = settings.get("cap_pct")
            if cap_pct is not None:
                ctx.config.risk.max_per_strategy_pct[strategy_id] = cap_pct
                ctx.strategy_engine.risk_engine.config.max_per_strategy_pct[
                    strategy_id
                ] = cap_pct

        dump_runtime_overrides(ctx.config)
        logger.info(
            "Applied risk preset",
            extra=build_request_log_extra(
                request, event="risk_preset_applied", preset=name, fields=updated_fields
            ),
        )
        return ApiEnvelope(
            data=RiskConfigPayload(**ctx.config.risk.__dict__), error=None
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception(
            "Failed to apply risk preset",
            extra=build_request_log_extra(
                request, event="risk_preset_failed", preset=name, error=str(exc)
            ),
        )
        return ApiEnvelope(data=None, error=str(exc))


@router.post("/kill_switch", response_model=ApiEnvelope[RiskStatusPayload])
async def set_kill_switch(request: Request) -> ApiEnvelope[RiskStatusPayload]:
    ctx = _context(request)
    if ctx.config.ui.read_only:
        logger.warning(
            "Kill switch update blocked: UI read-only",
            extra=build_request_log_extra(request, event="kill_switch_blocked"),
        )
        return ApiEnvelope(data=None, error="UI is in read-only mode")

    try:
        payload = KillSwitchPayload(**await request.json())
    except Exception:  # pragma: no cover - malformed body
        return ApiEnvelope(data=None, error="Invalid JSON payload")

    try:
        ctx.strategy_engine.set_manual_kill_switch(payload.active)
        status = ctx.strategy_engine.get_risk_status()
        logger.info(
            "Updated manual kill switch",
            extra=build_request_log_extra(
                request, event="kill_switch_updated", active=payload.active
            ),
        )
        return ApiEnvelope(data=RiskStatusPayload(**status.__dict__), error=None)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception(
            "Failed to update kill switch",
            extra=build_request_log_extra(request, event="kill_switch_update_failed"),
        )
        return ApiEnvelope(data=None, error=str(exc))
