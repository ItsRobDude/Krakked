"""Presets management endpoints."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

import yaml  # type: ignore[import-untyped]
from fastapi import APIRouter, Request
from pydantic import BaseModel

from kraken_bot.config import get_config_dir
from kraken_bot.ui.logging import build_request_log_extra
from kraken_bot.ui.models import ApiEnvelope
from kraken_bot.ui.routes.config import _apply_config_dict
from kraken_bot.utils.io import atomic_write, sanitize_filename

logger = logging.getLogger(__name__)

router = APIRouter()

ALLOWED_KINDS = {"risk", "strategies", "universe"}


class PresetPayload(BaseModel):
    name: str
    kind: str
    payload: Dict[str, Any]
    description: str = ""


class PresetApplyPayload(BaseModel):
    kind: str
    name: str
    dry_run: bool = False


class PresetSummary(BaseModel):
    name: str
    kind: str
    description: str
    updated_at: float


def _presets_dir():
    return get_config_dir() / "presets"


def _ensure_presets_dir():
    for kind in ALLOWED_KINDS:
        (_presets_dir() / kind).mkdir(parents=True, exist_ok=True)


def _context(request: Request):
    return request.app.state.context


@router.get("/", response_model=ApiEnvelope[List[PresetSummary]])
async def list_presets(
    request: Request, kind: Optional[str] = None
) -> ApiEnvelope[List[PresetSummary]]:
    """List all presets, optionally filtered by kind."""
    _ensure_presets_dir()
    summaries = []

    kinds_to_scan = [kind] if kind else ALLOWED_KINDS
    base_dir = _presets_dir()

    for k in kinds_to_scan:
        if k not in ALLOWED_KINDS:
            continue
        kind_dir = base_dir / k
        if not kind_dir.exists():
            continue

        for f in kind_dir.glob("*.yaml"):
            try:
                with open(f, "r") as fh:
                    data = yaml.safe_load(fh) or {}
                    summaries.append(
                        PresetSummary(
                            name=data.get("name", f.stem),
                            kind=k,
                            description=data.get("description", ""),
                            updated_at=f.stat().st_mtime,
                        )
                    )
            except Exception:
                logger.warning(f"Failed to parse preset {f}")

    return ApiEnvelope(data=summaries, error=None)


@router.get("/{kind}/{name}", response_model=ApiEnvelope[PresetPayload])
async def get_preset(
    kind: str, name: str, request: Request
) -> ApiEnvelope[PresetPayload]:
    """Retrieve a specific preset."""
    if kind not in ALLOWED_KINDS:
        return ApiEnvelope(data=None, error="Invalid preset kind")

    # Sanitize input
    try:
        safe_name = sanitize_filename(name)
    except ValueError as e:
        return ApiEnvelope(data=None, error=str(e))

    path = _presets_dir() / kind / f"{safe_name}.yaml"
    if not path.exists():
        return ApiEnvelope(data=None, error="Preset not found")

    try:
        with open(path, "r") as f:
            data = yaml.safe_load(f) or {}

        return ApiEnvelope(
            data=PresetPayload(
                name=data.get("name", name),
                kind=kind,
                payload=data.get("payload", {}),
                description=data.get("description", ""),
            ),
            error=None,
        )
    except Exception as exc:
        return ApiEnvelope(data=None, error=str(exc))


@router.post("/", response_model=ApiEnvelope[dict])
async def save_preset(payload: PresetPayload, request: Request) -> ApiEnvelope[dict]:
    """Save a preset to disk."""
    ctx = _context(request)
    if ctx.config.ui.read_only:
        return ApiEnvelope(data=None, error="UI is in read-only mode")

    if ctx.session.active:
        return ApiEnvelope(
            data=None, error="Cannot save preset while session is active"
        )

    if payload.kind not in ALLOWED_KINDS:
        return ApiEnvelope(data=None, error=f"Invalid kind. Allowed: {ALLOWED_KINDS}")

    _ensure_presets_dir()

    # Sanitize name
    try:
        safe_name = sanitize_filename(payload.name)
    except ValueError as e:
        return ApiEnvelope(data=None, error=str(e))

    path = _presets_dir() / payload.kind / f"{safe_name}.yaml"

    data = {
        "name": payload.name,
        "kind": payload.kind,
        "version": 1,
        "description": payload.description,
        "payload": payload.payload,
        "updated_at": time.time(),
    }

    try:
        atomic_write(path, data, dump_func=yaml.safe_dump)

        logger.info(
            "Preset saved",
            extra=build_request_log_extra(
                request, event="preset_saved", kind=payload.kind, name=payload.name
            ),
        )
        return ApiEnvelope(data={"success": True, "path": str(path)}, error=None)
    except Exception as exc:
        logger.exception("Failed to save preset")
        return ApiEnvelope(data=None, error=str(exc))


@router.post("/apply", response_model=ApiEnvelope[dict])
async def apply_preset(
    payload: PresetApplyPayload, request: Request
) -> ApiEnvelope[dict]:
    """Apply a saved preset to the active profile configuration."""
    ctx = _context(request)

    # Note: We must duplicate some basic checks here before validation to ensure
    # we return consistent ApiEnvelope 200 OK errors (helper does this too but we
    # need fail-fast logic for file loading which happens before helper call).

    if ctx.config.ui.read_only:
        return ApiEnvelope(data=None, error="UI is in read-only mode")

    if ctx.session.active:
        return ApiEnvelope(
            data=None, error="Cannot apply preset while session is active"
        )

    if not ctx.session.profile_name:
        return ApiEnvelope(data=None, error="No active profile selected")

    if payload.kind not in ALLOWED_KINDS:
        return ApiEnvelope(data=None, error=f"Invalid kind. Allowed: {ALLOWED_KINDS}")

    # Sanitize name
    try:
        safe_name = sanitize_filename(payload.name)
    except ValueError as e:
        return ApiEnvelope(data=None, error=str(e))

    path = _presets_dir() / payload.kind / f"{safe_name}.yaml"
    if not path.exists():
        # Consistent with get/delete
        return ApiEnvelope(data=None, error="Preset not found")

    # Load & Validate Structure
    try:
        with open(path, "r") as f:
            data = yaml.safe_load(f)
    except Exception as e:
        # YAML syntax error or permission error
        logger.error(f"Failed to load preset {path}: {e}")
        return ApiEnvelope(data=None, error=f"Failed to load preset: {str(e)}")

    if not isinstance(data, dict):
        return ApiEnvelope(data=None, error="Preset file is not a mapping")

    if data.get("kind") != payload.kind:
        return ApiEnvelope(data=None, error="Preset kind mismatch")

    preset_payload = data.get("payload")
    if not isinstance(preset_payload, dict):
        return ApiEnvelope(data=None, error="Preset payload must be a mapping")

    # Construct Patch
    patch = {payload.kind: preset_payload}

    # Apply via strict pipeline
    # log_events=False because we handle logging here to avoid duplicates
    try:
        result = _apply_config_dict(
            ctx=ctx,
            request=request,
            config_data=patch,
            dry_run=payload.dry_run,
            log_events=False,
        )
    except Exception as e:
        # Fallback if helper raises unexpected exception (defensive)
        logger.exception(
            "Preset apply failed unexpectedly",
            extra=build_request_log_extra(
                request,
                event="preset_apply_failed",
                preset_kind=payload.kind,
                preset_name=payload.name,
                preset_safe_name=safe_name,
                profile_name=ctx.session.profile_name,
                dry_run=payload.dry_run,
            ),
        )
        return ApiEnvelope(data=None, error=str(e))

    # Handle Result Logging
    if result.error:
        logger.error(
            "Preset apply failed",
            extra=build_request_log_extra(
                request,
                event="preset_apply_failed",
                preset_kind=payload.kind,
                preset_name=payload.name,
                preset_safe_name=safe_name,
                profile_name=ctx.session.profile_name,
                dry_run=payload.dry_run,
                error=result.error,
            ),
        )
    else:
        logger.info(
            "Preset applied",
            extra=build_request_log_extra(
                request,
                event="preset_applied",
                preset_kind=payload.kind,
                preset_name=payload.name,
                preset_safe_name=safe_name,
                profile_name=ctx.session.profile_name,
                dry_run=payload.dry_run,
            ),
        )
        # Enrich response data
        if result.data is None:
            result.data = {}
        result.data["preset"] = {"kind": payload.kind, "name": payload.name}

    return result


@router.delete("/{kind}/{name}", response_model=ApiEnvelope[dict])
async def delete_preset(kind: str, name: str, request: Request) -> ApiEnvelope[dict]:
    """Delete a preset."""
    ctx = _context(request)
    if ctx.config.ui.read_only:
        return ApiEnvelope(data=None, error="UI is in read-only mode")

    if ctx.session.active:
        return ApiEnvelope(
            data=None, error="Cannot delete preset while session is active"
        )

    if kind not in ALLOWED_KINDS:
        return ApiEnvelope(data=None, error="Invalid kind")

    # Sanitize inputs
    try:
        safe_name = sanitize_filename(name)
    except ValueError as e:
        return ApiEnvelope(data=None, error=str(e))

    path = _presets_dir() / kind / f"{safe_name}.yaml"
    if not path.exists():
        return ApiEnvelope(data=None, error="Preset not found")

    try:
        path.unlink()
        logger.info(
            "Preset deleted",
            extra=build_request_log_extra(
                request, event="preset_deleted", kind=kind, name=name
            ),
        )
        return ApiEnvelope(data={"success": True}, error=None)
    except Exception as exc:
        return ApiEnvelope(data=None, error=str(exc))
