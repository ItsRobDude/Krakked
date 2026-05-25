"""Feature-level ablation summaries for ML walk-forward reports."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal, Optional

from krakked.backtest.ml_report_compare import expand_ml_report_paths
from krakked.backtest.ml_reporting import ML_WALK_FORWARD_REPORT_VERSION

SUPPORTED_ML_ABLATION_REPORT_VERSIONS = {6, ML_WALK_FORWARD_REPORT_VERSION}
AblationFormat = Literal["markdown", "tsv", "json"]
AblationSort = Literal["drop-score", "contribution", "rank", "health", "name"]

CONTRIBUTION_SHARE_DROP_THRESHOLD = 0.05
CONTRIBUTION_SHARE_KEEP_THRESHOLD = 0.08
MEAN_RANK_PERCENTILE_DROP_THRESHOLD = 0.65
CLIPPED_RATE_HEALTH_THRESHOLD = 0.05
COEFFICIENT_ZERO_THRESHOLD = 1e-12


@dataclass
class MLFeatureAblationRow:
    """Feature-level summary from one saved ML walk-forward report."""

    report_name: str
    path: str
    timeframe: Optional[str]
    feature_schema: Optional[str]
    backend: Optional[str]
    feature: str
    fold_count: int
    best_rank: int
    mean_rank: float
    worst_rank: int
    mean_avg_abs_contribution: float
    mean_p95_abs_contribution: float
    contribution_share: float
    health_warning_count: int
    max_clipped_rate: float
    clipped_rate_gate_failed: bool
    coefficient_positive_count: int
    coefficient_negative_count: int
    coefficient_zero_count: int
    sign_stable: bool
    drop_score: float
    recommendation: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MLFeatureAblationSummary:
    """Feature rows plus non-fatal warnings produced while loading reports."""

    rows: list[MLFeatureAblationRow]
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "features": [row.to_dict() for row in self.rows],
            "warnings": list(self.warnings),
        }


def summarize_ml_feature_ablation(
    report_paths: Iterable[str | Path],
    *,
    glob_pattern: str | None = None,
    sort_by: AblationSort = "drop-score",
) -> MLFeatureAblationSummary:
    """Load feature-level ablation candidates from v6 ML walk-forward reports."""

    warnings: list[str] = []
    rows: list[MLFeatureAblationRow] = []
    for path in expand_ml_report_paths(report_paths, glob_pattern=glob_pattern):
        try:
            rows.extend(_load_ablation_rows(path))
        except ValueError as exc:
            warnings.append(str(exc))
            continue
    return MLFeatureAblationSummary(
        rows=_sort_rows(rows, sort_by),
        warnings=warnings,
    )


def render_ml_feature_ablation_summary(
    summary: MLFeatureAblationSummary,
    *,
    output_format: AblationFormat = "markdown",
) -> str:
    """Render feature ablation output as markdown, TSV, or JSON."""

    if output_format == "json":
        return json.dumps(summary.to_dict(), indent=2)

    headers = [
        "report",
        "tf",
        "features",
        "backend",
        "feature",
        "folds",
        "best_rank",
        "mean_rank",
        "worst_rank",
        "avg_abs",
        "p95_abs",
        "share",
        "health",
        "max_clip",
        "clip_gate",
        "coef_signs",
        "sign_stable",
        "drop_score",
        "recommendation",
    ]
    rows = [[_format_cell(row, header) for header in headers] for row in summary.rows]
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


def _load_ablation_rows(path: Path) -> list[MLFeatureAblationRow]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Skipping missing report: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Skipping non-JSON report: {path}") from exc

    if not isinstance(payload, dict):
        raise ValueError(f"Skipping non-ML report with invalid root: {path}")
    version = payload.get("report_version")
    if version not in SUPPORTED_ML_ABLATION_REPORT_VERSIONS:
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
    feature_rows = _collect_report_feature_rows(path, summary)
    if not feature_rows:
        raise ValueError(f"Skipping ML report without feature contributions: {path}")
    return feature_rows


def _collect_report_feature_rows(
    path: Path,
    summary: dict[str, Any],
) -> list[MLFeatureAblationRow]:
    report_name = path.stem
    feature_schema = _feature_schema(summary)
    backend = _backend(summary)
    timeframe = _optional_str(summary.get("timeframe"))

    per_feature: dict[str, dict[str, Any]] = {}
    for fold in summary.get("folds") or []:
        if not isinstance(fold, dict):
            continue
        diagnostics = fold.get("diagnostics") or {}
        if not isinstance(diagnostics, dict):
            continue
        features = diagnostics.get("features") or {}
        if not isinstance(features, dict):
            continue
        contributions = [
            row
            for row in features.get("linear_contributions") or []
            if isinstance(row, dict) and isinstance(row.get("feature"), str)
        ]
        feature_count = len(contributions)
        if feature_count == 0:
            continue
        health_warnings = [str(w) for w in features.get("health_warnings") or []]
        clipping_features = (features.get("clipping") or {}).get("features") or {}
        if not isinstance(clipping_features, dict):
            clipping_features = {}
        for rank, contribution in enumerate(contributions, start=1):
            name = str(contribution["feature"])
            bucket = per_feature.setdefault(
                name,
                {
                    "feature_counts": [],
                    "ranks": [],
                    "avg_abs": [],
                    "p95_abs": [],
                    "coefficients": [],
                    "health_warning_count": 0,
                    "clipped_rates": [],
                },
            )
            bucket["feature_counts"].append(feature_count)
            bucket["ranks"].append(rank)
            bucket["avg_abs"].append(
                _optional_float(contribution.get("avg_abs_row_contribution")) or 0.0
            )
            bucket["p95_abs"].append(
                _optional_float(contribution.get("p95_abs_row_contribution")) or 0.0
            )
            coefficient = _optional_float(contribution.get("coefficient"))
            if coefficient is not None:
                bucket["coefficients"].append(coefficient)
            bucket["health_warning_count"] += sum(
                1 for warning in health_warnings if name in warning
            )
            clipping = clipping_features.get(name)
            if isinstance(clipping, dict):
                bucket["clipped_rates"].append(
                    _optional_float(clipping.get("clipped_rate")) or 0.0
                )

    mean_contributions = {
        name: _mean(values["avg_abs"])
        for name, values in per_feature.items()
        if values["avg_abs"]
    }
    total_mean_contribution = sum(mean_contributions.values())
    rows: list[MLFeatureAblationRow] = []
    for name, values in per_feature.items():
        ranks = values["ranks"]
        feature_count = max(values["feature_counts"])
        mean_rank = _mean(ranks)
        mean_avg_abs = mean_contributions.get(name, 0.0)
        contribution_share = (
            mean_avg_abs / total_mean_contribution
            if total_mean_contribution > 0
            else 0.0
        )
        positive_count, negative_count, zero_count = _coefficient_sign_counts(
            values["coefficients"]
        )
        sign_stable = positive_count == 0 or negative_count == 0
        max_clipped_rate = max(values["clipped_rates"] or [0.0])
        health_warning_count = int(values["health_warning_count"])
        clipped_gate_failed = max_clipped_rate > CLIPPED_RATE_HEALTH_THRESHOLD
        drop_score = _drop_score(
            contribution_share=contribution_share,
            mean_rank=mean_rank,
            feature_count=feature_count,
            health_warning_count=health_warning_count,
            max_clipped_rate=max_clipped_rate,
            sign_stable=sign_stable,
        )
        rows.append(
            MLFeatureAblationRow(
                report_name=report_name,
                path=str(path),
                timeframe=timeframe,
                feature_schema=feature_schema,
                backend=backend,
                feature=name,
                fold_count=len(ranks),
                best_rank=min(ranks),
                mean_rank=mean_rank,
                worst_rank=max(ranks),
                mean_avg_abs_contribution=mean_avg_abs,
                mean_p95_abs_contribution=_mean(values["p95_abs"]),
                contribution_share=contribution_share,
                health_warning_count=health_warning_count,
                max_clipped_rate=max_clipped_rate,
                clipped_rate_gate_failed=clipped_gate_failed,
                coefficient_positive_count=positive_count,
                coefficient_negative_count=negative_count,
                coefficient_zero_count=zero_count,
                sign_stable=sign_stable,
                drop_score=drop_score,
                recommendation=_recommendation(
                    contribution_share=contribution_share,
                    mean_rank=mean_rank,
                    best_rank=min(ranks),
                    feature_count=feature_count,
                    health_warning_count=health_warning_count,
                    fold_count=len(ranks),
                    max_clipped_rate=max_clipped_rate,
                    sign_stable=sign_stable,
                ),
            )
        )
    return rows


def _recommendation(
    *,
    contribution_share: float,
    mean_rank: float,
    best_rank: int,
    feature_count: int,
    health_warning_count: int,
    fold_count: int,
    max_clipped_rate: float,
    sign_stable: bool,
) -> str:
    mean_rank_percentile = _rank_percentile(mean_rank, feature_count)
    ever_top_half = best_rank <= feature_count / 2.0
    keep_worthy = (
        contribution_share >= CONTRIBUTION_SHARE_KEEP_THRESHOLD
        or best_rank <= math.ceil(feature_count / 3.0)
    )
    health_risk = (
        max_clipped_rate > CLIPPED_RATE_HEALTH_THRESHOLD
        or health_warning_count >= math.ceil(fold_count / 2.0)
    )
    if (
        contribution_share <= CONTRIBUTION_SHARE_DROP_THRESHOLD
        and mean_rank_percentile >= MEAN_RANK_PERCENTILE_DROP_THRESHOLD
        and not ever_top_half
    ):
        return "drop_candidate"
    if keep_worthy and health_risk:
        return "keep_but_health_risk"
    if keep_worthy and sign_stable:
        return "keep_candidate"
    return "review_candidate"


def _drop_score(
    *,
    contribution_share: float,
    mean_rank: float,
    feature_count: int,
    health_warning_count: int,
    max_clipped_rate: float,
    sign_stable: bool,
) -> float:
    rank_score = _rank_percentile(mean_rank, feature_count)
    low_contribution_score = max(0.0, 1.0 - contribution_share / 0.10)
    health_score = min(1.0, health_warning_count / max(feature_count, 1))
    clip_score = min(1.0, max_clipped_rate / max(CLIPPED_RATE_HEALTH_THRESHOLD, 1e-9))
    sign_score = 0.0 if sign_stable else 0.15
    return round(
        0.55 * rank_score
        + 0.30 * low_contribution_score
        + 0.10 * health_score
        + 0.05 * clip_score
        + sign_score,
        6,
    )


def _rank_percentile(mean_rank: float, feature_count: int) -> float:
    if feature_count <= 1:
        return 0.0
    return max(0.0, min(1.0, (mean_rank - 1.0) / (feature_count - 1.0)))


def _coefficient_sign_counts(values: list[float]) -> tuple[int, int, int]:
    positive_count = 0
    negative_count = 0
    zero_count = 0
    for value in values:
        if value > COEFFICIENT_ZERO_THRESHOLD:
            positive_count += 1
        elif value < -COEFFICIENT_ZERO_THRESHOLD:
            negative_count += 1
        else:
            zero_count += 1
    return positive_count, negative_count, zero_count


def _feature_schema(summary: dict[str, Any]) -> Optional[str]:
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
    return None


def _backend(summary: dict[str, Any]) -> Optional[str]:
    for fold in summary.get("folds") or []:
        if not isinstance(fold, dict):
            continue
        diagnostics = fold.get("diagnostics") or {}
        if not isinstance(diagnostics, dict):
            continue
        for model in diagnostics.get("models") or []:
            if isinstance(model, dict):
                backend = _optional_str(model.get("model_backend"))
                if backend:
                    return backend
    return None


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _optional_str(value: object) -> Optional[str]:
    return value if isinstance(value, str) and value else None


def _optional_float(value: object) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _sort_rows(
    rows: list[MLFeatureAblationRow], sort_by: AblationSort
) -> list[MLFeatureAblationRow]:
    if sort_by == "contribution":
        return sorted(
            rows,
            key=lambda row: (
                -row.mean_avg_abs_contribution,
                row.report_name.lower(),
                row.feature.lower(),
            ),
        )
    if sort_by == "rank":
        return sorted(
            rows,
            key=lambda row: (row.mean_rank, row.report_name.lower(), row.feature.lower()),
        )
    if sort_by == "health":
        return sorted(
            rows,
            key=lambda row: (
                -row.health_warning_count,
                -row.max_clipped_rate,
                row.report_name.lower(),
                row.feature.lower(),
            ),
        )
    if sort_by == "name":
        return sorted(rows, key=lambda row: (row.report_name.lower(), row.feature.lower()))
    return sorted(
        rows,
        key=lambda row: (-row.drop_score, row.report_name.lower(), row.feature.lower()),
    )


def _format_cell(row: MLFeatureAblationRow, header: str) -> str:
    value: object = {
        "report": row.report_name,
        "tf": row.timeframe,
        "features": row.feature_schema,
        "backend": row.backend,
        "feature": row.feature,
        "folds": row.fold_count,
        "best_rank": row.best_rank,
        "mean_rank": row.mean_rank,
        "worst_rank": row.worst_rank,
        "avg_abs": row.mean_avg_abs_contribution,
        "p95_abs": row.mean_p95_abs_contribution,
        "share": row.contribution_share,
        "health": row.health_warning_count,
        "max_clip": row.max_clipped_rate,
        "clip_gate": row.clipped_rate_gate_failed,
        "coef_signs": (
            f"+{row.coefficient_positive_count}/"
            f"-{row.coefficient_negative_count}/"
            f"0{row.coefficient_zero_count}"
        ),
        "sign_stable": row.sign_stable,
        "drop_score": row.drop_score,
        "recommendation": row.recommendation,
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
    "MLFeatureAblationRow",
    "MLFeatureAblationSummary",
    "render_ml_feature_ablation_summary",
    "summarize_ml_feature_ablation",
]
