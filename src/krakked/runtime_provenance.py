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


def _deployment_drift_reason(
    *,
    image_name: Optional[str],
    image_tag: Optional[str],
    build_git_sha: Optional[str],
    runtime_source: Optional[str],
    expected_image_name: Optional[str],
    expected_image_tag: Optional[str],
    expected_build_git_sha: Optional[str],
    expected_runtime_source: Optional[str],
) -> Optional[str]:
    mismatches: list[str] = []
    comparisons = (
        ("image_name", image_name, expected_image_name),
        ("image_tag", image_tag, expected_image_tag),
        ("build_git_sha", build_git_sha, expected_build_git_sha),
        ("runtime_source", runtime_source, expected_runtime_source),
    )
    for label, actual, expected in comparisons:
        if expected is not None and actual != expected:
            mismatches.append(f"{label} expected {expected}, got {actual or 'unknown'}")
    return "; ".join(mismatches) if mismatches else None


def build_runtime_provenance(
    app_version: Optional[str],
) -> dict[str, Optional[str] | bool]:
    """Return operator-visible identity for the running process."""

    app_version_value = _clean(app_version) or "unknown"
    build_git_sha = _env_value("KRAKKED_BUILD_GIT_SHA")
    build_git_ref = _env_value("KRAKKED_BUILD_GIT_REF")
    image_name = _env_value("KRAKKED_RUNTIME_IMAGE", "KRAKKED_IMAGE")
    image_tag = _env_value("KRAKKED_RUNTIME_IMAGE_TAG", "KRAKKED_IMAGE_TAG")
    image_digest = _env_value(
        "KRAKKED_RUNTIME_IMAGE_DIGEST",
        "KRAKKED_IMAGE_DIGEST",
        default=None,
    )
    runtime_source = _env_value("KRAKKED_RUNTIME_SOURCE")
    expected_image_name = _env_value("KRAKKED_EXPECTED_IMAGE", default=image_name)
    expected_image_tag = _env_value("KRAKKED_EXPECTED_IMAGE_TAG", default=image_tag)
    expected_build_git_sha = _env_value(
        "KRAKKED_EXPECTED_BUILD_GIT_SHA",
        default=build_git_sha,
    )
    expected_runtime_source = _env_value(
        "KRAKKED_EXPECTED_RUNTIME_SOURCE",
        default=runtime_source,
    )
    deployment_drift_reason = _deployment_drift_reason(
        image_name=image_name,
        image_tag=image_tag,
        build_git_sha=build_git_sha,
        runtime_source=runtime_source,
        expected_image_name=expected_image_name,
        expected_image_tag=expected_image_tag,
        expected_build_git_sha=expected_build_git_sha,
        expected_runtime_source=expected_runtime_source,
    )

    return {
        "app_version": app_version_value,
        "build_git_sha": build_git_sha,
        "build_git_ref": build_git_ref,
        "image_name": image_name,
        "image_tag": image_tag,
        "image_digest": image_digest,
        "runtime_source": runtime_source,
        "expected_image_name": expected_image_name,
        "expected_image_tag": expected_image_tag,
        "expected_build_git_sha": expected_build_git_sha,
        "expected_runtime_source": expected_runtime_source,
        "deployment_drift_detected": deployment_drift_reason is not None,
        "deployment_drift_reason": deployment_drift_reason,
    }
