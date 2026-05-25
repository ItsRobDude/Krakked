"""Comparison helpers for saved ML walk-forward report artifacts."""

from __future__ import annotations

import glob
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal, Optional

from krakked.backtest.ml_reporting import ML_WALK_FORWARD_REPORT_VERSION

SUPPORTED_ML_COMPARE_REPORT_VERSIONS = {5, ML_WALK_FORWARD_REPORT_VERSION}
CompareFormat = Literal["markdown", "tsv", "json"]
CompareSort = Literal["name", "precision-long", "p95-lift", "positive-calls"]


@dataclass
class MLReportComparisonRow:
    """Compact comparable view of one saved ML walk-forward report."""

    name: str
    path: str
    report_version: int
    timeframe: Optional[str] = None
    feature_schema: Optional[str] = None
    backend: Optional[str] = None
    framework: Optional[str] = None
    fee_bps: Optional[float] = None
    slippage_bps: Optional[float] = None
    prediction_count: int = 0
    positive_edge_count: int = 0
    base_hit_rate: Optional[float] = None
    precision_long: Optional[float] = None
    edge_accuracy: Optional[float] = None
    directional_accuracy: Optional[float] = None
    p75_lift: Optional[float] = None
    p90_lift: Optional[float] = None
    p95_lift: Optional[float] = None
    selected_avg_realized_return: Optional[float] = None
    upper_half_monotonicity: Optional[bool] = None
    diagnostic_warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MLReportComparison:
    """Rows plus non-fatal warnings produced while loading reports."""

    rows: list[MLReportComparisonRow]
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "reports": [row.to_dict() for row in self.rows],
            "warnings": list(self.warnings),
        }


def expand_ml_report_paths(
    report_paths: Iterable[str | Path], *, glob_pattern: str | None = None
) -> list[Path]:
    """Expand explicit report paths and one optional glob into stable paths."""

    paths = [Path(path) for path in report_paths]
    if glob_pattern:
        paths.extend(Path(path) for path in glob.glob(glob_pattern))
    if not paths and glob_pattern is None:
        paths.extend(Path(path) for path in glob.glob("reports/ml/*.json"))

    seen: set[Path] = set()
    resolved_paths: list[Path] = []
    for path in paths:
        resolved = path.expanduser().resolve()
        if resolved not in seen:
            seen.add(resolved)
            resolved_paths.append(resolved)
    return sorted(resolved_paths)


def compare_ml_reports(
    report_paths: Iterable[str | Path],
    *,
    glob_pattern: str | None = None,
    sort_by: CompareSort = "name",
) -> MLReportComparison:
    """Load comparable fields from v5/current ML walk-forward reports."""

    warnings: list[str] = []
    rows: list[MLReportComparisonRow] = []
    for path in expand_ml_report_paths(report_paths, glob_pattern=glob_pattern):
        try:
            row = _load_comparison_row(path)
        except ValueError as exc:
            warnings.append(str(exc))
            continue
        rows.append(row)
    return MLReportComparison(rows=_sort_rows(rows, sort_by), warnings=warnings)


def render_ml_report_comparison(
    comparison: MLReportComparison,
    *,
    output_format: CompareFormat = "markdown",
) -> str:
    """Render comparison output as markdown, TSV, or JSON."""

    if output_format == "json":
        return json.dumps(comparison.to_dict(), indent=2)

    headers = [
        "name",
        "ver",
        "tf",
        "features",
        "backend",
        "framework",
        "fee",
        "slip",
        "preds",
        "pos",
        "base_hit",
        "precision_long",
        "edge_acc",
        "dir_acc",
        "p75_lift",
        "p90_lift",
        "p95_lift",
        "selected_avg_ret",
        "upper_half",
        "warnings",
    ]
    rows = [[_format_cell(row, header) for header in headers] for row in comparison.rows]
    if output_format == "tsv":
        lines = ["\t".join(headers)]
        lines.extend("\t".join(row) for row in rows)
        return "\n".join(lines)

    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join(lines)


def _sort_rows(
    rows: list[MLReportComparisonRow], sort_by: CompareSort
) -> list[MLReportComparisonRow]:
    if sort_by == "precision-long":
        return sorted(
            rows,
            key=lambda row: _descending_optional(row.precision_long),
        )
    if sort_by == "p95-lift":
        return sorted(rows, key=lambda row: _descending_optional(row.p95_lift))
    if sort_by == "positive-calls":
        return sorted(
            rows,
            key=lambda row: (-row.positive_edge_count, row.name.lower()),
        )
    return sorted(rows, key=lambda row: row.name.lower())


def _descending_optional(value: Optional[float]) -> tuple[int, float]:
    if value is None:
        return (1, 0.0)
    return (0, -float(value))


def _load_comparison_row(path: Path) -> MLReportComparisonRow:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Skipping missing report: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Skipping non-JSON report: {path}") from exc

    if not isinstance(payload, dict):
        raise ValueError(f"Skipping non-ML report with invalid root: {path}")
    version = payload.get("report_version")
    if version not in SUPPORTED_ML_COMPARE_REPORT_VERSIONS:
        raise ValueError(
            f"Skipping unsupported ML report version {version!r}: {path}"
        )
    provenance = payload.get("provenance") or {}
    if not isinstance(provenance, dict) or provenance.get("generated_by") != (
        "krakked ml-walk-forward"
    ):
        raise ValueError(f"Skipping non-ML walk-forward report: {path}")

    summary = payload.get("summary")
    if not isinstance(summary, dict):
        raise ValueError(f"Skipping ML report without summary: {path}")
    metrics = summary.get("metrics") or {}
    if not isinstance(metrics, dict):
        metrics = {}
    calibration = summary.get("regression_calibration") or {}
    if not isinstance(calibration, dict):
        calibration = {}
    model_diagnostics = iter(_iter_model_diagnostics(summary))
    first_model = next(model_diagnostics, {})

    return MLReportComparisonRow(
        name=path.stem,
        path=str(path),
        report_version=int(version),
        timeframe=_optional_str(summary.get("timeframe")),
        feature_schema=_feature_schema(summary, first_model),
        backend=_backend(first_model),
        framework=_framework(first_model),
        fee_bps=_optional_float(summary.get("fee_bps")),
        slippage_bps=_optional_float(summary.get("slippage_bps")),
        prediction_count=int(metrics.get("prediction_count") or 0),
        positive_edge_count=int(
            metrics.get("positive_edge_prediction_count") or 0
        ),
        base_hit_rate=_base_hit_rate(calibration),
        precision_long=_optional_float(metrics.get("precision_long")),
        edge_accuracy=_optional_float(metrics.get("edge_prediction_accuracy")),
        directional_accuracy=_optional_float(metrics.get("directional_accuracy")),
        p75_lift=_sweep_value(calibration, "predicted_delta_p75", "lift_over_base_rate"),
        p90_lift=_sweep_value(calibration, "predicted_delta_p90", "lift_over_base_rate"),
        p95_lift=_sweep_value(calibration, "predicted_delta_p95", "lift_over_base_rate"),
        selected_avg_realized_return=_selected_avg_realized_return(calibration),
        upper_half_monotonicity=_upper_half_monotonicity(calibration),
        diagnostic_warnings=[
            str(warning) for warning in summary.get("diagnostic_warnings") or []
        ],
    )


def _iter_model_diagnostics(summary: dict[str, Any]) -> Iterable[dict[str, Any]]:
    for fold in summary.get("folds") or []:
        if not isinstance(fold, dict):
            continue
        diagnostics = fold.get("diagnostics") or {}
        if not isinstance(diagnostics, dict):
            continue
        for model in diagnostics.get("models") or []:
            if isinstance(model, dict):
                yield model


def _feature_schema(summary: dict[str, Any], model: dict[str, Any]) -> Optional[str]:
    for fold in summary.get("folds") or []:
        if not isinstance(fold, dict):
            continue
        diagnostics = fold.get("diagnostics") or {}
        if not isinstance(diagnostics, dict):
            continue
        features = diagnostics.get("features") or {}
        if isinstance(features, dict):
            schema = _optional_str(features.get("schema_version"))
            if schema:
                profile = _optional_str(features.get("feature_profile"))
                if profile and profile != "all":
                    return f"{schema}/{profile}"
                return schema
    schema = _optional_str(model.get("feature_schema_version"))
    if schema:
        profile = _optional_str(model.get("feature_profile"))
        if profile and profile != "all":
            return f"{schema}/{profile}"
        return schema
    return _feature_schema_from_key(_optional_str(model.get("model_key")))


def _feature_schema_from_key(model_key: Optional[str]) -> Optional[str]:
    if not model_key:
        return None
    for part in model_key.split("|"):
        if part.startswith("features_"):
            return part.removeprefix("features_")
    return None


def _backend(model: dict[str, Any]) -> Optional[str]:
    backend = _optional_str(model.get("model_backend"))
    if backend:
        return backend
    model_key = _optional_str(model.get("model_key")) or ""
    if "sgd_huber" in model_key:
        return "sgd_huber"
    if "sgd_squared_error" in model_key:
        return "sgd_squared_error"
    if "pa_reg" in model_key:
        return "pa"
    return None


def _framework(model: dict[str, Any]) -> Optional[str]:
    return _optional_str(model.get("model_framework")) or _optional_str(
        model.get("framework")
    )


def _base_hit_rate(calibration: dict[str, Any]) -> Optional[float]:
    return _sweep_value(calibration, "evaluation_hurdle", "realized_hit_rate")


def _selected_avg_realized_return(calibration: dict[str, Any]) -> Optional[float]:
    selected = _sweep_value(
        calibration,
        "predicted_delta_p95",
        "avg_realized_return_selected",
    )
    if selected is not None:
        return selected
    return _sweep_value(
        calibration,
        "predicted_delta_p90",
        "avg_realized_return_selected",
    )


def _upper_half_monotonicity(calibration: dict[str, Any]) -> Optional[bool]:
    monotonicity = calibration.get("monotonicity") or {}
    if not isinstance(monotonicity, dict):
        return None
    value = monotonicity.get("upper_half_improves")
    return value if isinstance(value, bool) else None


def _sweep_value(
    calibration: dict[str, Any], row_name: str, value_name: str
) -> Optional[float]:
    row = _sweep_row(calibration, row_name)
    if row is None:
        return None
    return _optional_float(row.get(value_name))


def _sweep_row(
    calibration: dict[str, Any], row_name: str
) -> Optional[dict[str, Any]]:
    for row in calibration.get("threshold_sweeps") or []:
        if isinstance(row, dict) and row.get("name") == row_name:
            return row
    return None


def _optional_str(value: object) -> Optional[str]:
    return value if isinstance(value, str) and value else None


def _optional_float(value: object) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _format_cell(row: MLReportComparisonRow, header: str) -> str:
    value = {
        "name": row.name,
        "ver": row.report_version,
        "tf": row.timeframe,
        "features": row.feature_schema,
        "backend": row.backend,
        "framework": row.framework,
        "fee": row.fee_bps,
        "slip": row.slippage_bps,
        "preds": row.prediction_count,
        "pos": row.positive_edge_count,
        "base_hit": row.base_hit_rate,
        "precision_long": row.precision_long,
        "edge_acc": row.edge_accuracy,
        "dir_acc": row.directional_accuracy,
        "p75_lift": row.p75_lift,
        "p90_lift": row.p90_lift,
        "p95_lift": row.p95_lift,
        "selected_avg_ret": row.selected_avg_realized_return,
        "upper_half": row.upper_half_monotonicity,
        "warnings": "; ".join(row.diagnostic_warnings),
    }[header]
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return _format_float(value)
    text = str(value)
    return text.replace("|", "\\|")


def _format_float(value: float) -> str:
    if abs(value) < 0.01:
        return f"{value:.6f}".rstrip("0").rstrip(".")
    return f"{value:.4f}".rstrip("0").rstrip(".")


__all__ = [
    "MLReportComparison",
    "MLReportComparisonRow",
    "compare_ml_reports",
    "expand_ml_report_paths",
    "render_ml_report_comparison",
]
