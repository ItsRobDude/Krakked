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
from kraken_bot.config_loader import RUNTIME_OVERRIDES_FILENAME
from kraken_bot.market_data.api import validate_pairs_with_client
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
            return validate_pairs_with_client(ctx.client, pairs)
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


def _prune_runtime_overrides(
    overrides_path: Path, payload_keys: set[str], preserve_keys: set[str]
) -> None:
    """
    Load runtime overrides from disk, remove keys present in payload_keys
    (except those in preserve_keys), and save back. Delete file if empty.
    """
    if not overrides_path.exists():
        return

    try:
        with open(overrides_path, "r") as f:
            overrides = yaml.safe_load(f) or {}

        original_keys = set(overrides.keys())
        keys_to_remove = (original_keys & payload_keys) - preserve_keys

        if not keys_to_remove:
            return

        for k in keys_to_remove:
            overrides.pop(k, None)

        if not overrides:
            overrides_path.unlink()
            logger.info(f"Cleared runtime overrides at {overrides_path}")
        else:
            atomic_write(overrides_path, overrides, dump_func=yaml.safe_dump)
            logger.info(f"Pruned runtime overrides at {overrides_path}: {keys_to_remove}")

    except Exception as e:
        logger.error(f"Failed to prune runtime overrides at {overrides_path}: {e}")


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

    # Strip restricted sections that should never be modified via apply
    # session: stateful, managed by system endpoints
    # profiles: registry, managed by profiles endpoints
    config_data.pop("session", None)
    config_data.pop("profiles", None)

    # 1. Security Check: Execution Mode Guards
    # Block any attempt to CHANGE restricted execution keys via generic config apply.
    # Users must use the dedicated /api/system/mode endpoint for mode changes.
    execution_payload = config_data.get("execution")
    restricted_keys = {
        "mode",
        "allow_live_trading",
        "validate_only",
        "paper_tests_completed",
    }

    if execution_payload:
        current_execution = asdict(ctx.config.execution)
        keys_to_remove = []
        for key in restricted_keys:
            if key in execution_payload:
                new_val = execution_payload[key]
                cur_val = current_execution.get(key)
                if new_val != cur_val:
                    return ApiEnvelope(
                        data=None,
                        error=f"Execution '{key}' cannot be modified via config apply. Use /api/system/mode.",
                    )
                # If same value, safe to ignore/strip
                keys_to_remove.append(key)

        for k in keys_to_remove:
            execution_payload.pop(k)

        if not execution_payload:
            config_data.pop("execution")

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
        # 3. Determine target file(s) and Load existing content for Deep Merge
        main_config_path = config_dir / "config.yaml"

        trading_sections = {
            "region",
            "universe",
            "market_data",
            "portfolio",
            "execution",
            "risk",
            "strategies",
        }

        # Keys present in the cleaned payload (used for pruning overrides)
        payload_keys = set(config_data.keys())

        # Sections always preserved in runtime overrides (never pruned by config apply)
        preserve_override_keys = {"session"}

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

            # Split payload: trading sections for profile, others for main
            profile_payload = {
                k: v for k, v in config_data.items() if k in trading_sections
            }
            main_payload = {
                k: v for k, v in config_data.items() if k not in trading_sections
            }

            # Calculate ALL keys being applied (profile OR main) to prune from profile runtime overrides
            # This ensures that if a user applies a main-config key (like 'ui') while a profile is active,
            # we still prune 'ui' from the profile's runtime override file if it exists there.
            all_applied_keys = set(config_data.keys())

            if profile_payload:
                merged_profile_config = deep_merge_dicts(
                    existing_profile_config, profile_payload
                )
                backup_file(p_path)
                atomic_write(p_path, merged_profile_config, dump_func=yaml.safe_dump)

            # Always attempt to prune profile runtime overrides if ANY key was applied
            # (even if only main payload was present)
            if profile_payload or main_payload:
                profile_overrides_path = (
                    config_dir / "profiles" / profile_name / RUNTIME_OVERRIDES_FILENAME
                )
                _prune_runtime_overrides(
                    profile_overrides_path,
                    all_applied_keys,
                    preserve_override_keys,
                )

            if main_payload:
                existing_main_config_for_split: Dict[str, Any] = {}
                if main_config_path.exists():
                    with open(main_config_path, "r") as f:
                        existing_main_config_for_split = yaml.safe_load(f) or {}

                merged_main_config = deep_merge_dicts(existing_main_config_for_split, main_payload)
                backup_file(main_config_path)
                atomic_write(main_config_path, merged_main_config, dump_func=yaml.safe_dump)

                # Prune main runtime overrides
                # Remove any keys that we just persisted to the static main config
                main_overrides_path = config_dir / RUNTIME_OVERRIDES_FILENAME
                _prune_runtime_overrides(
                    main_overrides_path,
                    set(main_payload.keys()),
                    preserve_override_keys
                )

        else:
            # No profile - everything to main config
            existing_main_config: Dict[str, Any] = {}
            if main_config_path.exists():
                with open(main_config_path, "r") as f:
                    existing_main_config = yaml.safe_load(f) or {}

            merged_main_config = deep_merge_dicts(existing_main_config, config_data)
            backup_file(main_config_path)
            atomic_write(main_config_path, merged_main_config, dump_func=yaml.safe_dump)

            # Prune main runtime overrides
            # Remove any keys that we just persisted to the static main config
            main_overrides_path = config_dir / RUNTIME_OVERRIDES_FILENAME
            _prune_runtime_overrides(
                main_overrides_path,
                payload_keys,
                preserve_override_keys
            )

        # 4. Trigger Reload
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
