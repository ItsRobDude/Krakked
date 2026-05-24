"""Shared helpers for durable ML walk-forward report artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ML_WALK_FORWARD_REPORT_VERSION = 2
LATEST_ML_WALK_FORWARD_REPORT_RELATIVE_PATH = Path("reports") / "ml" / "latest.json"


def get_latest_ml_walk_forward_report_path(config_dir: Path) -> Path:
    """Return the canonical latest ML walk-forward report path."""

    return (
        Path(config_dir).expanduser().resolve()
        / LATEST_ML_WALK_FORWARD_REPORT_RELATIVE_PATH
    )


def validate_ml_walk_forward_report_payload(
    payload: dict[str, Any], *, resolved_path: Path
) -> dict[str, Any]:
    """Validate the minimal ML walk-forward report contract."""

    if payload.get("report_version") != ML_WALK_FORWARD_REPORT_VERSION:
        raise ValueError(
            f"Unsupported ML report version in {resolved_path}: "
            f"{payload.get('report_version')}"
        )

    summary = payload.get("summary")
    if not isinstance(summary, dict):
        raise ValueError(f"ML report is missing a summary payload: {resolved_path}")

    required_fields = {
        "start",
        "end",
        "strategy_id",
        "timeframe",
        "train_bars",
        "test_bars",
        "evaluation_mode",
        "model_state_reused_across_folds",
        "fold_count",
        "pairs",
        "fee_bps",
        "slippage_bps",
        "round_trip_cost_bps",
        "coverage_status",
        "warnings",
        "metrics",
        "confidence_buckets",
        "promotable",
        "promotable_reasons",
        "folds",
    }
    missing_fields = sorted(field for field in required_fields if field not in summary)
    if missing_fields:
        raise ValueError(
            f"ML report summary is missing required fields in {resolved_path}: "
            f"{', '.join(missing_fields)}"
        )

    if not isinstance(summary.get("metrics"), dict):
        raise ValueError(f"ML report metrics are invalid in {resolved_path}")
    if not isinstance(summary.get("folds"), list):
        raise ValueError(f"ML report folds are invalid in {resolved_path}")
    if not isinstance(summary.get("confidence_buckets"), list):
        raise ValueError(f"ML report confidence buckets are invalid in {resolved_path}")

    provenance = payload.get("provenance")
    if not isinstance(provenance, dict):
        raise ValueError(f"ML report provenance payload is invalid in {resolved_path}")
    if provenance.get("generated_by") != "krakked ml-walk-forward":
        raise ValueError(f"ML report provenance is invalid in {resolved_path}")

    return payload


def write_ml_walk_forward_report(
    payload: dict[str, Any], report_path: str | Path
) -> Path:
    """Validate and write a JSON ML walk-forward report artifact."""

    resolved = Path(report_path).expanduser().resolve()
    validate_ml_walk_forward_report_payload(payload, resolved_path=resolved)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return resolved


def publish_latest_ml_walk_forward_report(
    payload: dict[str, Any], *, config_dir: Path
) -> Path:
    """Write ``payload`` to the canonical latest ML walk-forward report path."""

    resolved = get_latest_ml_walk_forward_report_path(config_dir)
    return write_ml_walk_forward_report(payload, resolved)


def load_ml_walk_forward_report(report_path: str | Path) -> dict[str, Any]:
    """Load and validate a saved ML walk-forward report."""

    resolved = Path(report_path).expanduser().resolve()
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"ML report not found: {resolved}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"ML report is not valid JSON: {resolved}") from exc

    if not isinstance(payload, dict):
        raise ValueError(f"ML report root payload is invalid: {resolved}")
    return validate_ml_walk_forward_report_payload(payload, resolved_path=resolved)


def summarize_latest_ml_walk_forward_report(
    payload: dict[str, Any], *, resolved_path: Path
) -> dict[str, Any]:
    """Extract a compact ML report summary for future operator surfaces."""

    summary = payload.get("summary") or {}
    metrics = summary.get("metrics") or {}
    return {
        "report_path": str(Path(resolved_path).expanduser().resolve()),
        "generated_at": payload.get("generated_at"),
        "strategy_id": summary.get("strategy_id"),
        "timeframe": summary.get("timeframe"),
        "evaluation_mode": summary.get("evaluation_mode"),
        "model_state_reused_across_folds": summary.get(
            "model_state_reused_across_folds"
        ),
        "fold_count": summary.get("fold_count"),
        "prediction_count": metrics.get("prediction_count"),
        "positive_edge_prediction_count": metrics.get("positive_edge_prediction_count"),
        "edge_prediction_accuracy": metrics.get("edge_prediction_accuracy"),
        "directional_accuracy": metrics.get("directional_accuracy"),
        "precision_long": metrics.get("precision_long"),
        "promotable": summary.get("promotable"),
        "promotable_reasons": list(summary.get("promotable_reasons") or []),
        "coverage_status": summary.get("coverage_status"),
        "warnings": list(summary.get("warnings") or []),
        "confidence_buckets": list(summary.get("confidence_buckets") or []),
    }


__all__ = [
    "LATEST_ML_WALK_FORWARD_REPORT_RELATIVE_PATH",
    "ML_WALK_FORWARD_REPORT_VERSION",
    "get_latest_ml_walk_forward_report_path",
    "load_ml_walk_forward_report",
    "publish_latest_ml_walk_forward_report",
    "summarize_latest_ml_walk_forward_report",
    "validate_ml_walk_forward_report_payload",
    "write_ml_walk_forward_report",
]
