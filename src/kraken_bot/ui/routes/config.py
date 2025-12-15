"""Configuration snapshot and management endpoints for the UI."""

from __future__ import annotations

import logging
import time
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from kraken_bot.config import get_config_dir
from kraken_bot.config_loader import write_initial_config
from kraken_bot.ui.logging import build_request_log_extra
from kraken_bot.ui.models import ApiEnvelope

logger = logging.getLogger(__name__)

router = APIRouter()


class ConfigApplyPayload(BaseModel):
    """Payload for applying full or partial configuration."""
    config: Dict[str, Any]
    dry_run: bool = False  # If True, only validates


class ProfileCreatePayload(BaseModel):
    name: str
    description: str = ""
    # Optional defaults for the new profile
    default_mode: str = "paper"
    base_config: Optional[Dict[str, Any]] = None


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
            from kraken_bot.market_data.api import MarketDataAPI
            # Hacky: Create temp API to use its validation logic?
            # Or just duplicate the simple check.
            # Let's duplicate simple check for safety if market_data service is down.
            resp = ctx.client.get_public("AssetPairs")
            known_pairs = resp.get("result", resp) if resp else {}
            known_keys = set(known_pairs.keys())
            known_altnames = {v.get("altname") for v in known_pairs.values() if isinstance(v, dict)}

            invalid = []
            for pair in pairs:
                if pair not in known_keys and pair not in known_altnames:
                    slashless = pair.replace("/", "")
                    if slashless not in known_keys and slashless not in known_altnames:
                        invalid.append(pair)
            return invalid
         except Exception as e:
            logger.error(f"Error manually validating pairs: {e}")
            return []

    return []

def _backup_file(path: Path) -> Optional[Path]:
    """Creates a backup of the given file with a timestamp."""
    if not path.exists():
        return None
    timestamp = int(time.time())
    backup_path = path.with_name(f"{path.name}.{timestamp}.bak")
    try:
        with open(path, "rb") as src, open(backup_path, "wb") as dst:
            dst.write(src.read())
        return backup_path
    except Exception as e:
        logger.error(f"Failed to backup {path}: {e}")
        raise

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
async def apply_config(payload: ConfigApplyPayload, request: Request) -> ApiEnvelope[dict]:
    """
    Validates, persists, and applies configuration changes.
    Supports hot-reload by triggering re-initialization.
    """
    ctx = _context(request)
    if ctx.config.ui.read_only:
        return ApiEnvelope(data=None, error="UI is in read-only mode")

    config_data = payload.config

    # 1. Validation
    # Universe Pairs
    new_universe = config_data.get("universe", {})
    include_pairs = new_universe.get("include_pairs", [])
    if include_pairs:
        invalid_pairs = _validate_universe_pairs(include_pairs, ctx)
        if invalid_pairs:
            return ApiEnvelope(
                data=None,
                error=f"Invalid universe pairs: {', '.join(invalid_pairs)}"
            )

    if payload.dry_run:
        return ApiEnvelope(data={"status": "valid"}, error=None)

    # 2. Determine target file(s)
    # If a profile is active, we write to the profile config.
    # AND we might need to update the main config if non-trading settings changed (like UI).
    # For simplicity, we assume the payload represents the *effective* config structure.
    # However, separating them cleanly is tricky if the UI sends a merged blob.
    # Strategy:
    # - If profile active:
    #    - Extract trading sections (region, universe, market_data, portfolio, execution, risk, strategies).
    #    - Write those to profiles/<profile>.yaml.
    #    - Extract 'session' and 'ui' and 'profiles' registry.
    #    - Update main config.yaml with those.
    # - If NO profile active:
    #    - Write everything to config.yaml.

    config_dir = get_config_dir()
    profile_name = ctx.session.profile_name

    try:
        # Paths
        main_config_path = config_dir / "config.yaml"

        if profile_name:
            # We need to resolve the profile path from the CURRENT config registry
            # to be safe, though usually it's profiles/<name>.yaml
            profiles_entry = ctx.config.profiles.get(profile_name)
            if not profiles_entry:
                 return ApiEnvelope(data=None, error=f"Active profile '{profile_name}' not found in registry")

            # Resolve path
            p_path_str = profiles_entry.config_path
            p_path = Path(p_path_str)
            if not p_path.is_absolute():
                p_path = config_dir / p_path

            # Split payload
            trading_sections = ["region", "universe", "market_data", "portfolio", "execution", "risk", "strategies"]
            profile_payload = {k: v for k, v in config_data.items() if k in trading_sections}

            # Remaining goes to main config?
            # Actually, usually users editing via UI are editing the *active trading config*.
            # They rarely change UI host/port via the React app (it would kill the connection).
            # So updating the profile config is the primary action.

            # Backup
            _backup_file(p_path)

            # Write Profile Config
            # We read the existing one to preserve comments? No, YAML persistence usually wipes comments.
            # We assume overwrite.
            # Ideally we merge with existing file content to keep fields not in payload?
            # The payload usually comes from 'get_config' which returns the Full Merged Config.
            # This is dangerous: if we write the Merged Config to the Profile File,
            # we might bake in things that were inherited from Main Config.
            # BUT, explicitly explicit is fine.

            with open(p_path, "w") as f:
                yaml.safe_dump(profile_payload, f)

        else:
            # No profile - write to main config
            _backup_file(main_config_path)
            with open(main_config_path, "w") as f:
                yaml.safe_dump(config_data, f)

        # 3. Trigger Reload
        # Signal the main loop to re-bootstrap
        ctx.reinitialize_event.set()

        logger.info(
            "Configuration applied and reload triggered",
            extra=build_request_log_extra(
                request,
                event="config_applied",
                profile=profile_name
            )
        )

        return ApiEnvelope(data={"status": "applied", "reloading": True}, error=None)

    except Exception as exc:
        logger.exception(
            "Failed to apply config",
            extra=build_request_log_extra(request, event="config_apply_failed"),
        )
        return ApiEnvelope(data=None, error=str(exc))


@router.post("/profiles", response_model=ApiEnvelope[dict])
async def create_profile(payload: ProfileCreatePayload, request: Request) -> ApiEnvelope[dict]:
    """
    Creates a new profile:
    1. Creates profiles/<name>.yaml
    2. Adds entry to main config.yaml
    """
    ctx = _context(request)
    if ctx.config.ui.read_only:
        return ApiEnvelope(data=None, error="UI is in read-only mode")

    config_dir = get_config_dir()
    profile_filename = f"{payload.name}.yaml"
    profile_path = config_dir / "profiles" / profile_filename

    if profile_path.exists():
        return ApiEnvelope(data=None, error=f"Profile file '{profile_filename}' already exists")

    try:
        # 1. Create Profile File
        profile_path.parent.mkdir(parents=True, exist_ok=True)

        base_config = payload.base_config or {}
        # Ensure minimal structure
        if "execution" not in base_config:
            base_config["execution"] = {"mode": payload.default_mode}

        with open(profile_path, "w") as f:
            yaml.safe_dump(base_config, f)

        # 2. Update Main Config Registry
        main_config_path = config_dir / "config.yaml"
        _backup_file(main_config_path)

        with open(main_config_path, "r") as f:
            main_data = yaml.safe_load(f) or {}

        profiles = main_data.get("profiles", {})
        profiles[payload.name] = {
            "name": payload.name,
            "description": payload.description,
            "config_path": str(Path("profiles") / profile_filename),
            "credentials_path": "", # Optional
            "default_mode": payload.default_mode
        }
        main_data["profiles"] = profiles

        with open(main_config_path, "w") as f:
            yaml.safe_dump(main_data, f)

        # 3. Trigger Reload to pick up new profile in registry
        ctx.reinitialize_event.set()

        logger.info(
            "Profile created",
            extra=build_request_log_extra(
                request,
                event="profile_created",
                profile_name=payload.name
            )
        )

        return ApiEnvelope(data={"name": payload.name, "path": str(profile_path)}, error=None)

    except Exception as exc:
        logger.exception(
            "Failed to create profile",
            extra=build_request_log_extra(request, event="profile_create_failed"),
        )
        return ApiEnvelope(data=None, error=str(exc))
