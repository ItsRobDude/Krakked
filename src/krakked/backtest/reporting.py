"""Shared helpers for durable backtest report artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

LATEST_BACKTEST_REPORT_RELATIVE_PATH = Path("reports") / "backtests" / "latest.json"


def get_latest_backtest_report_path(config_dir: Path) -> Path:
    """Return the canonical published replay-report path under ``config_dir``."""

    return Path(config_dir).expanduser().resolve() / LATEST_BACKTEST_REPORT_RELATIVE_PATH


def write_backtest_report(payload: dict[str, Any], report_path: str | Path) -> Path:
    """Write a JSON backtest report artifact to ``report_path``."""

    resolved = Path(report_path).expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return resolved


def publish_latest_backtest_report(
    payload: dict[str, Any], *, config_dir: Path
) -> Path:
    """Write ``payload`` to the canonical latest replay-report location."""

    resolved = get_latest_backtest_report_path(config_dir)
    return write_backtest_report(payload, resolved)


def validate_backtest_report_payload(
    payload: dict[str, Any], *, resolved_path: Path
) -> dict[str, Any]:
    """Validate the minimal report contract used by CLI and UI readers."""

    if payload.get("report_version") != 1:
        raise ValueError(
            f"Unsupported report version in {resolved_path}: {payload.get('report_version')}"
        )

    summary = payload.get("summary")
    if not isinstance(summary, dict):
        raise ValueError(f"Report is missing a summary payload: {resolved_path}")

    required_fields = {
        "ending_equity_usd",
        "return_pct",
        "max_drawdown_pct",
        "filled_orders",
        "blocked_actions",
        "execution_errors",
        "per_strategy",
        "replay_inputs",
    }
    missing_fields = sorted(field for field in required_fields if field not in summary)
    if missing_fields:
        raise ValueError(
            f"Report summary is missing required fields in {resolved_path}: {', '.join(missing_fields)}"
        )

    if not isinstance(summary.get("per_strategy"), dict):
        raise ValueError(f"Report per_strategy is invalid in {resolved_path}")
    if not isinstance(summary.get("replay_inputs"), dict):
        raise ValueError(f"Report replay_inputs is invalid in {resolved_path}")

    preflight = payload.get("preflight")
    if preflight is not None and not isinstance(preflight, dict):
        raise ValueError(f"Report preflight payload is invalid in {resolved_path}")
    provenance = payload.get("provenance")
    if provenance is not None and not isinstance(provenance, dict):
        raise ValueError(f"Report provenance payload is invalid in {resolved_path}")

    return payload


def load_backtest_report(report_path: str | Path) -> dict[str, Any]:
    """Load and validate a saved backtest report."""

    resolved = Path(report_path).expanduser().resolve()
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Report not found: {resolved}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Report is not valid JSON: {resolved}") from exc

    if not isinstance(payload, dict):
        raise ValueError(f"Report root payload is invalid: {resolved}")
    return validate_backtest_report_payload(payload, resolved_path=resolved)


def summarize_latest_backtest_report(
    payload: dict[str, Any], *, resolved_path: Path
) -> dict[str, Any]:
    """Extract the compact replay summary shown in the operator UI."""

    summary = payload.get("summary") or {}
    preflight = payload.get("preflight") or {}
    replay_inputs = summary.get("replay_inputs") or {}
    blocked_reason_counts = summary.get("blocked_reason_counts") or {}
    missing_series = list(preflight.get("missing_series") or summary.get("missing_series") or [])
    partial_series = list(preflight.get("partial_series") or summary.get("partial_series") or [])
    usable_series_count = int(
        preflight.get(
            "usable_series_count",
            summary.get("usable_series_count", 0),
        )
        or 0
    )
    coverage_status = preflight.get("status")
    if not isinstance(coverage_status, str) or not coverage_status:
        coverage_status = "limited" if (missing_series or partial_series) else "ready"

    return {
        "available": True,
        "generated_at": payload.get("generated_at"),
        "trust_level": summary.get("trust_level"),
        "trust_note": summary.get("trust_note"),
        "notable_warnings": list(summary.get("notable_warnings") or []),
        "end_equity_usd": summary.get("ending_equity_usd"),
        "pnl_usd": summary.get("absolute_pnl_usd"),
        "return_pct": summary.get("return_pct"),
        "fills": summary.get("filled_orders"),
        "blocked_actions": summary.get("blocked_actions"),
        "execution_errors": summary.get("execution_errors"),
        "coverage_status": coverage_status,
        "usable_series_count": usable_series_count,
        "missing_series": missing_series,
        "partial_series": partial_series,
        "blocked_reason_counts": blocked_reason_counts,
        "cost_model": summary.get("cost_model"),
        "replay_inputs": replay_inputs,
        "report_path": str(resolved_path),
    }
