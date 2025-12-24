"""Configuration snapshot and management endpoints for the UI."""

from __future__ import annotations

import logging
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml  # type: ignore[import-untyped]
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from kraken_bot.config import get_config_dir
from kraken_bot.config_loader import (
    RUNTIME_OVERRIDES_FILENAME,
    _load_yaml_mapping,
    _resolve_effective_env,
    parse_app_config,
)
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


def _split_ui_payload(ui_payload: Any) -> Tuple[Dict, Dict]:
    """
    Splits a UI configuration payload into profile-bound and main-bound parts.
    - Profile: 'refresh_intervals'
    - Main: everything else (host, port, auth, etc.)
    """
    if not isinstance(ui_payload, dict):
        return {}, {}

    profile_ui = {}
    main_ui = {}

    if "refresh_intervals" in ui_payload:
        profile_ui["refresh_intervals"] = ui_payload["refresh_intervals"]

    for k, v in ui_payload.items():
        if k != "refresh_intervals":
            main_ui[k] = v

    return profile_ui, main_ui


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
        overrides = _load_yaml_mapping(overrides_path)

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
            logger.info(
                f"Pruned runtime overrides at {overrides_path}: {keys_to_remove}"
            )

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
    config_data.pop("session", None)
    config_data.pop("profiles", None)

    # 1. Security Check: Execution Mode Guards
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

    config_dir = get_config_dir()
    profile_name = ctx.session.profile_name
    main_config_path = config_dir / "config.yaml"

    try:
        # --- Strict Full Validation (Matches Load Behavior) ---
        # 1. Load Existing Base
        try:
            existing_main = _load_yaml_mapping(main_config_path)
        except Exception as e:
            return ApiEnvelope(
                data=None, error=f"Main config corrupted, cannot validate apply: {e}"
            )

        # 2. Resolve Environment
        effective_env = _resolve_effective_env(None, str(main_config_path))
        env_config_path = main_config_path.parent / f"config.{effective_env}.yaml"
        try:
            env_config = _load_yaml_mapping(env_config_path)
        except Exception as e:
            return ApiEnvelope(
                data=None, error=f"Environment config corrupted, cannot validate: {e}"
            )

        # 3. Handle Profiles & Split Payloads
        existing_profile: Dict[str, Any] = {}
        profile_config_path: Path | None = None
        profiles_entry = None

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
            profile_config_path = p_path
            try:
                existing_profile = _load_yaml_mapping(profile_config_path)
            except Exception as e:
                return ApiEnvelope(
                    data=None,
                    error=f"Profile config corrupted, cannot validate apply: {e}",
                )

        # Split Config Data into Profile vs Main payloads
        trading_sections = {
            "region",
            "universe",
            "market_data",
            "portfolio",
            "execution",
            "risk",
            "strategies",
            # UI handled specially below
        }

        profile_payload = {}
        main_payload = {}

        # Handle UI splitting
        ui_payload = config_data.get("ui")
        profile_ui, main_ui = _split_ui_payload(ui_payload)

        # BLOCKER FIX: Enforce profile requirement for profile-bound UI settings
        if profile_ui and not profile_name:
            return ApiEnvelope(
                data=None,
                error="ui.refresh_intervals requires an active profile",
            )

        # Assign other sections
        for k, v in config_data.items():
            if k == "ui":
                continue
            if profile_name and k in trading_sections:
                profile_payload[k] = v
            else:
                main_payload[k] = v

        # Inject separated UI
        if profile_ui and profile_name:
            profile_payload["ui"] = profile_ui
        if main_ui:
            if "ui" not in main_payload:
                main_payload["ui"] = {}
            main_payload["ui"].update(main_ui)

        # Calculate applied keys based on payload content
        main_applied = set(main_payload.keys())
        profile_applied = set(profile_payload.keys())

        # 4. Determine Relevant Overrides File
        # STRICT RULE: Load exactly the same file load_config uses.
        if profile_name:
            overrides_path = (
                config_dir / "profiles" / profile_name / RUNTIME_OVERRIDES_FILENAME
            )
        else:
            overrides_path = config_dir / RUNTIME_OVERRIDES_FILENAME

        try:
            relevant_overrides = _load_yaml_mapping(overrides_path)
        except Exception as e:
            return ApiEnvelope(
                data=None,
                error=f"Runtime overrides corrupted, cannot validate apply: {e}",
            )

        # 5. Build Candidates & Merge (In-Memory Validation)
        # Main Candidate
        main_candidate = deep_merge_dicts(existing_main, main_payload)

        # Start Merge Chain
        merged_candidate = main_candidate

        # Env Overlay
        if env_config:
            merged_candidate = deep_merge_dicts(merged_candidate, env_config)

        # Profile Overlay
        if profile_name:
            profile_candidate = deep_merge_dicts(existing_profile, profile_payload)
            merged_candidate = deep_merge_dicts(merged_candidate, profile_candidate)

        # Prune Overrides (In-Memory Simulation)
        preserve_override_keys = {"session"}

        if profile_name:
            applied_for_pruning = main_applied | profile_applied
        else:
            applied_for_pruning = main_applied

        keys_to_prune = (set(relevant_overrides.keys()) & applied_for_pruning) - preserve_override_keys
        pruned_overrides = {
            k: v for k, v in relevant_overrides.items() if k not in keys_to_prune
        }

        # Merge Pruned Overrides
        if pruned_overrides:
            merged_candidate = deep_merge_dicts(merged_candidate, pruned_overrides)

        # 6. Parse & Verify
        try:
            parse_app_config(
                merged_candidate,
                config_path=main_config_path,
                effective_env=effective_env,
            )
        except Exception as e:
            # This catches invariant violations (like max_per_strategy_pct missing)
            return ApiEnvelope(data=None, error=f"Validation failed: {str(e)}")

        # Validation Passed!
        if payload.dry_run:
            return ApiEnvelope(data={"status": "valid"}, error=None)

        # --- Persistence Phase (Writes) ---

        # Write Profile Config
        if profile_name and profile_config_path:
            if profile_payload:
                final_profile_write = deep_merge_dicts(
                    existing_profile, profile_payload
                )
                backup_file(profile_config_path)
                atomic_write(
                    profile_config_path, final_profile_write, dump_func=yaml.safe_dump
                )

        # Write Main Config
        if main_payload:
            final_main_write = deep_merge_dicts(existing_main, main_payload)
            backup_file(main_config_path)
            atomic_write(
                main_config_path, final_main_write, dump_func=yaml.safe_dump
            )

        # Prune Overrides on Disk
        if profile_name:
            # Prune Profile Overrides (Union)
            profile_overrides_path = (
                config_dir / "profiles" / profile_name / RUNTIME_OVERRIDES_FILENAME
            )
            # Re-calculate union here just to be explicit
            applied_union = main_applied | profile_applied
            _prune_runtime_overrides(
                profile_overrides_path,
                applied_union,
                preserve_override_keys,
            )

            # Prune Main Overrides (Main Payload Only)
            main_overrides_path = config_dir / RUNTIME_OVERRIDES_FILENAME
            _prune_runtime_overrides(
                main_overrides_path,
                main_applied,
                preserve_override_keys,
            )
        else:
            # Prune Main Overrides (Main Payload)
            main_overrides_path = config_dir / RUNTIME_OVERRIDES_FILENAME
            _prune_runtime_overrides(
                main_overrides_path,
                main_applied,
                preserve_override_keys,
            )

        # Trigger Reload
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
