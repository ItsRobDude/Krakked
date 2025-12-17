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
from kraken_bot.utils.io import atomic_write, sanitize_filename

logger = logging.getLogger(__name__)

router = APIRouter()

PRESETS_DIR = get_config_dir() / "presets"
ALLOWED_KINDS = {"risk", "strategies", "universe"}


class PresetPayload(BaseModel):
    name: str
    kind: str
    payload: Dict[str, Any]
    description: str = ""


class PresetSummary(BaseModel):
    name: str
    kind: str
    description: str
    updated_at: float


def _ensure_presets_dir():
    for kind in ALLOWED_KINDS:
        (PRESETS_DIR / kind).mkdir(parents=True, exist_ok=True)


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

    for k in kinds_to_scan:
        if k not in ALLOWED_KINDS:
            continue
        kind_dir = PRESETS_DIR / k
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

    path = PRESETS_DIR / kind / f"{safe_name}.yaml"
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

    path = PRESETS_DIR / payload.kind / f"{safe_name}.yaml"

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

    path = PRESETS_DIR / kind / f"{safe_name}.yaml"
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
