"""Configuration snapshot and management endpoints for the UI."""

from __future__ import annotations

import logging
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List

import yaml  # type: ignore[import-untyped]
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from kraken_bot.config import get_config_dir
from kraken_bot.ui.logging import build_request_log_extra
from kraken_bot.ui.models import ApiEnvelope
from kraken_bot.utils.io import atomic_write, backup_file, deep_merge_dicts

logger = logging.getLogger(__name__)

router = APIRouter()


class ConfigApplyPayload(BaseModel):
    """Payload for applying full or partial configuration."""

    config: Dict[str, Any]
    dry_run: bool = False  # If True, only validates


def _context(request: Request):
    return request.app.state.context


def _redact_auth_token(config_dict: dict) -> dict:
    redacted = deepcopy(config_dict)
    ui_cfg = redacted.get("ui") or {}
    auth_cfg = ui_cfg.get("auth") or {}
    if "token" in auth_cfg:
        auth_cfg["token"] = "***"
    return redacted


def _validate_universe_pairs(pairs: List[str], ctx) -> List[str]:
    """
    Validates a list of pair names against Kraken's asset pairs.
    Returns a list of invalid pairs.
    """
    if ctx.market_data:
        return ctx.market_data.validate_pairs(pairs)

    # Fallback to manual check if market_data not ready but client is
    if ctx.client:
        try:
            # Duplicate the simple check for safety if market_data service is down.
            resp = ctx.client.get_public("AssetPairs")
            known_pairs = resp.get("result", resp) if resp else {}
            known_keys = set(known_pairs.keys())
            known_altnames = {
                v.get("altname") for v in known_pairs.values() if isinstance(v, dict)
            }

            invalid = []
            for pair in pairs:
                if pair not in known_keys and pair not in known_altnames:
                    slashless = pair.replace("/", "")
                    if slashless not in known_keys and slashless not in known_altnames:
                        invalid.append(pair)
            return invalid
        except Exception as e:
            logger.error(f"Error manually validating pairs: {e}")
            raise HTTPException(
                status_code=503,
                detail=f"Universe validation unavailable (cannot reach Kraken): {str(e)}",
            )

    # If no client and no market data, validation impossible. Fail closed.
    raise HTTPException(
        status_code=503, detail="Universe validation unavailable (no connection)"
    )


@router.get("/runtime")
async def get_runtime_config(request: Request) -> JSONResponse:
    """Return the current runtime AppConfig as a JSON attachment."""
    ctx = _context(request)
    try:
        config_dict = _redact_auth_token(asdict(ctx.config))

        return JSONResponse(
            content={"data": config_dict, "error": None},
            headers={
                "Content-Disposition": 'attachment; filename="krakked-config-runtime.json"'
            },
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception(
            "Failed to dump runtime config",
            extra=build_request_log_extra(request, event="config_runtime_failed"),
        )
        return JSONResponse(
            content={"data": None, "error": str(exc)},
            status_code=500,
        )


@router.post("/apply", response_model=ApiEnvelope[dict])
async def apply_config(
    payload: ConfigApplyPayload, request: Request
) -> ApiEnvelope[dict]:
    """
    Validates, persists, and applies configuration changes.
    Supports hot-reload by triggering re-initialization.
    """
    ctx = _context(request)
    if ctx.config.ui.read_only:
        return ApiEnvelope(data=None, error="UI is in read-only mode")

    if ctx.session.active:
        return ApiEnvelope(
            data=None, error="Cannot apply configuration while session is active"
        )

    config_data = payload.config

    # 1. Security Check: Execution Mode Guards
    # Block any attempt to set restricted execution keys via generic config apply.
    # Users must use the dedicated /api/system/mode endpoint for mode changes.
    execution_payload = config_data.get("execution", {})
    restricted_keys = {
        "mode",
        "allow_live_trading",
        "validate_only",
        "paper_tests_completed",
    }

    # Check if any restricted key is present in the payload (even if value is same)
    # Ideally we only block CHANGES, but blocking presence enforces the "use system endpoint" rule strictly.
    # However, a "save all" UI might send the whole config back.
    # If the UI sends back existing values, we might allow it?
    # BUT, to be safe and force usage of the guard, we should block attempts to change them TO dangerous values
    # OR block them entirely if they differ from current config.
    # The safest "fail closed" approach is to forbid setting them here at all.
    # The UI should strip these keys or use the mode endpoint.

    # Let's inspect what is being set.
    for key in restricted_keys:
        if key in execution_payload:
            return ApiEnvelope(
                data=None,
                error=f"Execution '{key}' cannot be modified via config apply. Use /api/system/mode.",
            )

    # 2. Validation
    new_universe = config_data.get("universe", {})
    include_pairs = new_universe.get("include_pairs", [])
    if include_pairs:
        try:
            # Must catch any exception from validation (ServiceUnavailable etc)
            # and translate to API error.
            invalid_pairs = _validate_universe_pairs(include_pairs, ctx)
            if invalid_pairs:
                return ApiEnvelope(
                    data=None,
                    error=f"Invalid universe pairs: {', '.join(invalid_pairs)}",
                )
        except HTTPException as e:
            return ApiEnvelope(data=None, error=e.detail)
        except Exception as e:
            logger.error(f"Universe validation failed: {e}")
            return ApiEnvelope(
                data=None, error=f"Universe validation unavailable: {str(e)}"
            )

    if payload.dry_run:
        return ApiEnvelope(data={"status": "valid"}, error=None)

    config_dir = get_config_dir()
    profile_name = ctx.session.profile_name

    try:
        # 2. Determine target file(s) and Load existing content for Deep Merge
        main_config_path = config_dir / "config.yaml"

        trading_sections = [
            "region",
            "universe",
            "market_data",
            "portfolio",
            "execution",
            "risk",
            "strategies",
        ]

        # If profile active: merge trading sections into profile config, rest into main config (if applicable)
        # But wait, applying config usually sends the WHOLE merged view or a partial view.
        # Deep merge handles partial updates.

        if profile_name:
            profiles_entry = ctx.config.profiles.get(profile_name)
            if not profiles_entry:
                return ApiEnvelope(
                    data=None,
                    error=f"Active profile '{profile_name}' not found in registry",
                )

            p_path_str = profiles_entry.config_path
            p_path = Path(p_path_str)
            if not p_path.is_absolute():
                p_path = config_dir / p_path

            # Read existing profile config
            existing_profile_config: Dict[str, Any] = {}
            if p_path.exists():
                with open(p_path, "r") as f:
                    existing_profile_config = yaml.safe_load(f) or {}

            # Split payload: trading sections for profile
            profile_payload = {
                k: v for k, v in config_data.items() if k in trading_sections
            }

            if profile_payload:
                merged_profile_config = deep_merge_dicts(
                    existing_profile_config, profile_payload
                )
                backup_file(p_path)
                atomic_write(p_path, merged_profile_config, dump_func=yaml.safe_dump)

            # Fix Split-Brain: If we just wrote to the profile config, we must ensure
            # stale runtime overrides don't clobber it on next load.
            # Best way is to delete the runtime override file for this profile.
            # The next 'dump_runtime_overrides' (if any) will recreate it with fresh state.
            from kraken_bot.config_loader import RUNTIME_OVERRIDES_FILENAME

            runtime_overrides_path = (
                config_dir / "profiles" / profile_name / RUNTIME_OVERRIDES_FILENAME
            )
            if runtime_overrides_path.exists():
                try:
                    runtime_overrides_path.unlink()
                    logger.info(
                        f"Cleared stale runtime overrides for profile {profile_name}"
                    )
                except Exception as e:
                    logger.warning(f"Failed to clear runtime overrides: {e}")

        else:
            # No profile - everything to main config
            existing_main_config: Dict[str, Any] = {}
            if main_config_path.exists():
                with open(main_config_path, "r") as f:
                    existing_main_config = yaml.safe_load(f) or {}

            merged_main_config = deep_merge_dicts(existing_main_config, config_data)
            backup_file(main_config_path)
            atomic_write(main_config_path, merged_main_config, dump_func=yaml.safe_dump)

        # 3. Trigger Reload
        ctx.reinitialize_event.set()

        logger.info(
            "Configuration applied and reload triggered",
            extra=build_request_log_extra(
                request, event="config_applied", profile=profile_name
            ),
        )

        return ApiEnvelope(data={"status": "applied", "reloading": True}, error=None)

    except Exception as exc:
        logger.exception(
            "Failed to apply config",
            extra=build_request_log_extra(request, event="config_apply_failed"),
        )
        return ApiEnvelope(data=None, error=str(exc))
