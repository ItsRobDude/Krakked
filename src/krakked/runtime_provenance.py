"""Runtime build and deployment provenance helpers."""

from __future__ import annotations

import os
from typing import Optional


def _clean(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _env_value(*names: str, default: Optional[str] = "unknown") -> Optional[str]:
    for name in names:
        value = _clean(os.getenv(name))
        if value is not None:
            return value
    return default


def build_runtime_provenance(app_version: Optional[str]) -> dict[str, Optional[str]]:
    """Return operator-visible identity for the running process."""

    return {
        "app_version": _clean(app_version) or "unknown",
        "build_git_sha": _env_value("KRAKKED_BUILD_GIT_SHA"),
        "build_git_ref": _env_value("KRAKKED_BUILD_GIT_REF"),
        "image_name": _env_value("KRAKKED_RUNTIME_IMAGE", "KRAKKED_IMAGE"),
        "image_tag": _env_value("KRAKKED_RUNTIME_IMAGE_TAG", "KRAKKED_IMAGE_TAG"),
        "image_digest": _env_value(
            "KRAKKED_RUNTIME_IMAGE_DIGEST",
            "KRAKKED_IMAGE_DIGEST",
            default=None,
        ),
        "runtime_source": _env_value("KRAKKED_RUNTIME_SOURCE"),
    }
