"""Walk-forward evaluation for ML strategies on cached OHLC data."""

from __future__ import annotations

import copy
import json
import logging
import math
import pickle
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Iterable, Optional

from krakked import APP_VERSION
from krakked.backtest.ml_reporting import ML_WALK_FORWARD_REPORT_VERSION
from krakked.backtest.runner import (
    BacktestMarketData,
    BacktestPortfolioService,
    _configured_backtest_pairs,
    _timeframe_seconds,
)
from krakked.config import AppConfig
from krakked.strategy.engine import StrategyEngine
from krakked.strategy.ml_labels import (
    FEE_ADJUSTED_EDGE_PREDICTION_TARGET,
    NO_POSITIVE_EDGE_PREDICTION,
    POSITIVE_EDGE_PREDICTION,
)
from krakked.strategy.features import ML_FEATURE_CLIP_RANGES, ML_FEATURE_NAMES
from krakked.strategy.models import StrategyIntent

logger = logging.getLogger(__name__)

ML_STRATEGY_TYPES = {
    "machine_learning",
    "machine_learning_alt",
    "machine_learning_regression",
}
EVALUATION_MODE = "rolling_window_isolated"
EDGE_SCORING_MODE = "intent_hurdle_aligned"
DIAGNOSTIC_RETURN_THRESHOLDS = (0.003, 0.005, 0.01, 0.015)
PREDICTED_DELTA_QUANTILE_THRESHOLDS = (0.75, 0.90, 0.95)
NEAR_ZERO_THRESHOLD = 1e-12
RARE_POSITIVE_LABEL_RATE = 0.01
MONOTONICITY_INSUFFICIENT_DATA = "insufficient_data"
MIN_TOTAL_ROWS_FOR_MONOTONICITY = 60
MIN_ROWS_PER_HALF_FOR_MONOTONICITY = 30
PROMOTION_TIER_BLOCKED = "blocked"
PROMOTION_TIER_RESEARCH = "research_promising"
PROMOTION_TIER_RISK_OVERLAY = "risk_overlay_candidate"
PROMOTION_TIER_SELF_STANDING = "self_standing"
PROMOTION_TIERS_ORDERED: tuple[str, ...] = (
    PROMOTION_TIER_RESEARCH,
    PROMOTION_TIER_RISK_OVERLAY,
    PROMOTION_TIER_SELF_STANDING,
)
PROMOTION_TIERS_OPERATIONAL: frozenset[str] = frozenset(
    {PROMOTION_TIER_RISK_OVERLAY, PROMOTION_TIER_SELF_STANDING}
)
RISK_OVERLAY_MIN_PRECISION_LIFT = 1.20
RISK_OVERLAY_MIN_P95_LIFT = 1.30
SELF_STANDING_MIN_PRECISION_LONG = 0.50
SELF_STANDING_SELECTED_RETURN_COST_MULTIPLE = 2.0
SCALED_FEATURE_MEDIAN_ABS_WARNING_THRESHOLD = 0.75
SCALED_FEATURE_STD_MIN_WARNING_THRESHOLD = 0.5
SCALED_FEATURE_STD_MAX_WARNING_THRESHOLD = 2.0
SCALED_FEATURE_TAIL_ABS_WARNING_THRESHOLD = 3.0
FEATURE_CLIPPED_RATE_WARNING_THRESHOLD = 0.02
FEATURE_CLIPPED_RATE_RESEARCH_GATE_THRESHOLD = 0.05
HIGH_RISK_SCALED_FEATURES = frozenset(
    {
        "volume_change",
        "volume_log_ratio",
        "range_atr",
        "upper_wick_atr",
    }
)


@dataclass
class MLWalkForwardPrediction:
    fold_index: int
    generated_at: datetime
    strategy_id: str
    pair: str
    timeframe: str
    side: str
    intent_type: str
    confidence: float
    prediction_target: str
    predicted_positive_edge: bool
    predicted_direction: Optional[str]
    current_close: float
    future_close: float
    realized_return: float
    round_trip_cost_pct: float
    evaluation_hurdle_pct: float
    evaluation_hurdle_source: str
    directional_correct: Optional[bool]
    evaluation_hurdle_correct: bool
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def fee_adjusted_correct(self) -> bool:
        return self.evaluation_hurdle_correct

    def to_dict(self) -> dict[str, Any]:
        return {
            "fold_index": self.fold_index,
            "generated_at": self.generated_at.astimezone(UTC).isoformat(),
            "strategy_id": self.strategy_id,
            "pair": self.pair,
            "timeframe": self.timeframe,
            "side": self.side,
            "intent_type": self.intent_type,
            "confidence": self.confidence,
            "prediction_target": self.prediction_target,
            "predicted_positive_edge": self.predicted_positive_edge,
            "predicted_direction": self.predicted_direction,
            "current_close": self.current_close,
            "future_close": self.future_close,
            "realized_return": self.realized_return,
            "round_trip_cost_pct": self.round_trip_cost_pct,
            "evaluation_hurdle_pct": self.evaluation_hurdle_pct,
            "evaluation_hurdle_source": self.evaluation_hurdle_source,
            "directional_correct": self.directional_correct,
            "evaluation_hurdle_correct": self.evaluation_hurdle_correct,
            "fee_adjusted_correct": self.fee_adjusted_correct,
            "metadata": copy.deepcopy(self.metadata),
        }


@dataclass
class MLWalkForwardFold:
    fold_index: int
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime
    train_cycles: int
    test_cycles: int
    predictions: list[MLWalkForwardPrediction] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fold_index": self.fold_index,
            "train_start": self.train_start.astimezone(UTC).isoformat(),
            "train_end": self.train_end.astimezone(UTC).isoformat(),
            "test_start": self.test_start.astimezone(UTC).isoformat(),
            "test_end": self.test_end.astimezone(UTC).isoformat(),
            "train_cycles": self.train_cycles,
            "test_cycles": self.test_cycles,
            "prediction_count": len(self.predictions),
            "metrics": _build_prediction_metrics(self.predictions),
            "confidence_buckets": _build_confidence_buckets(self.predictions),
            "regression_calibration": _build_regression_calibration(
                self.predictions
            ),
            "diagnostics": copy.deepcopy(self.diagnostics),
        }


@dataclass
class MLWalkForwardSummary:
    start: datetime
    end: datetime
    strategy_id: str
    timeframe: str
    train_bars: int
    test_bars: int
    folds: list[MLWalkForwardFold]
    fee_bps: float
    slippage_bps: float
    pairs: list[str]
    coverage_status: str
    warnings: list[str]
    evaluation_mode: str = EVALUATION_MODE
    edge_scoring_mode: str = EDGE_SCORING_MODE
    model_state_reused_across_folds: bool = False

    def to_dict(self) -> dict[str, Any]:
        predictions = [
            prediction for fold in self.folds for prediction in fold.predictions
        ]
        metrics = _build_prediction_metrics(predictions)
        fold_dicts = [fold.to_dict() for fold in self.folds]
        regression_calibration = _build_regression_calibration(predictions)
        diagnostic_warnings = _build_diagnostic_warnings(fold_dicts)
        if (
            regression_calibration.get("monotonicity", {}).get("upper_half_improves")
            is False
        ):
            diagnostic_warnings.append(
                "Higher predicted-delta buckets did not improve realized returns overall."
            )
        round_trip_cost_pct = _round_trip_cost_pct(
            fee_bps=self.fee_bps, slippage_bps=self.slippage_bps
        )
        assessment = _assess_promotability(
            metrics=metrics,
            regression_calibration=regression_calibration,
            fold_dicts=fold_dicts,
            round_trip_cost_pct=round_trip_cost_pct,
        )
        promotable = assessment.is_operational
        # promotable_reasons mirrors `promotable`: pass messages when operational,
        # the failure reasons of the lowest unmet tier when blocked. Next-tier
        # blockers for operational runs live in promotion_tiers so operator
        # tooling can render them with a clear "next tier" label.
        if assessment.tier == PROMOTION_TIER_SELF_STANDING:
            promotable_reasons = [_tier_pass_message(PROMOTION_TIER_SELF_STANDING)]
        elif assessment.tier == PROMOTION_TIER_RISK_OVERLAY:
            promotable_reasons = [_tier_pass_message(PROMOTION_TIER_RISK_OVERLAY)]
        elif assessment.tier == PROMOTION_TIER_RESEARCH:
            promotable_reasons = assessment.reasons_for_tier(
                PROMOTION_TIER_RISK_OVERLAY
            )
        else:
            promotable_reasons = assessment.reasons_for_tier(PROMOTION_TIER_RESEARCH)
        if not promotable:
            for warning in diagnostic_warnings:
                reason = f"Diagnostic warning: {warning}"
                if reason not in promotable_reasons:
                    promotable_reasons.append(reason)
        return {
            "start": self.start.astimezone(UTC).isoformat(),
            "end": self.end.astimezone(UTC).isoformat(),
            "strategy_id": self.strategy_id,
            "timeframe": self.timeframe,
            "train_bars": self.train_bars,
            "test_bars": self.test_bars,
            "evaluation_mode": self.evaluation_mode,
            "edge_scoring_mode": self.edge_scoring_mode,
            "model_state_reused_across_folds": self.model_state_reused_across_folds,
            "fold_count": len(self.folds),
            "pairs": list(self.pairs),
            "fee_bps": self.fee_bps,
            "slippage_bps": self.slippage_bps,
            "round_trip_cost_bps": _round_trip_cost_bps(
                fee_bps=self.fee_bps, slippage_bps=self.slippage_bps
            ),
            "coverage_status": self.coverage_status,
            "warnings": list(self.warnings),
            "metrics": metrics,
            "confidence_buckets": _build_confidence_buckets(predictions),
            "regression_calibration": regression_calibration,
            "diagnostic_warnings": diagnostic_warnings,
            "promotion_tier": assessment.tier,
            "promotion_tiers": assessment.to_dict()["tiers"],
            "promotable": promotable,
            "promotable_reasons": promotable_reasons,
            "folds": fold_dicts,
        }


@dataclass
class MLWalkForwardResult:
    summary: MLWalkForwardSummary

    def to_report_dict(self) -> dict[str, Any]:
        return {
            "report_version": ML_WALK_FORWARD_REPORT_VERSION,
            "generated_at": datetime.now(UTC).isoformat(),
            "summary": self.summary.to_dict(),
            "provenance": {
                "app_version": APP_VERSION,
                "generated_by": "krakked ml-walk-forward",
            },
        }


def _round_trip_cost_bps(*, fee_bps: float, slippage_bps: float) -> float:
    return 2.0 * (max(float(fee_bps), 0.0) + max(float(slippage_bps), 0.0))


def _round_trip_cost_pct(*, fee_bps: float, slippage_bps: float) -> float:
    return _round_trip_cost_bps(fee_bps=fee_bps, slippage_bps=slippage_bps) / 10_000.0


def _build_walk_forward_folds(
    timestamps: list[int], *, train_bars: int, test_bars: int
) -> list[tuple[list[int], list[int]]]:
    if train_bars <= 0:
        raise ValueError("train_bars must be greater than 0")
    if test_bars <= 0:
        raise ValueError("test_bars must be greater than 0")

    folds: list[tuple[list[int], list[int]]] = []
    start_index = 0
    while start_index + train_bars + test_bars <= len(timestamps):
        train = timestamps[start_index : start_index + train_bars]
        test = timestamps[
            start_index + train_bars : start_index + train_bars + test_bars
        ]
        folds.append((train, test))
        start_index += test_bars
    return folds


def _set_strategy_learning(config: AppConfig, strategy_id: str, enabled: bool) -> None:
    strat_cfg = config.strategies.configs[strategy_id]
    params = dict(strat_cfg.params or {})
    params["continuous_learning"] = bool(enabled)
    strat_cfg.params = params


def _prepare_ml_config(
    config: AppConfig,
    *,
    strategy_id: str,
    timeframe: str,
    fee_bps: float,
    slippage_bps: Optional[float] = None,
) -> AppConfig:
    config_copy = copy.deepcopy(config)
    if strategy_id not in config_copy.strategies.configs:
        raise ValueError(f"Unknown strategy: {strategy_id}")

    strat_cfg = config_copy.strategies.configs[strategy_id]
    if strat_cfg.type not in ML_STRATEGY_TYPES:
        raise ValueError(f"Strategy {strategy_id} is not an ML strategy")

    config_copy.execution.mode = "simulation"
    config_copy.execution.validate_only = False
    config_copy.execution.allow_live_trading = False
    config_copy.execution.max_plan_age_seconds = 0
    if slippage_bps is not None:
        config_copy.execution.max_slippage_bps = int(round(slippage_bps))
    config_copy.ml.enabled = True
    config_copy.strategies.enabled = [strategy_id]
    strat_cfg.enabled = True
    params = dict(strat_cfg.params or {})
    params["timeframe"] = timeframe
    if strat_cfg.type in {"machine_learning", "machine_learning_alt"}:
        params["label_fee_bps"] = float(fee_bps)
        if slippage_bps is not None:
            params["label_slippage_bps"] = float(slippage_bps)
    if strat_cfg.type == "machine_learning_regression":
        params["edge_fee_bps"] = float(fee_bps)
        if slippage_bps is not None:
            params["edge_slippage_bps"] = float(slippage_bps)
    params.pop("timeframes", None)
    strat_cfg.params = params
    return config_copy


def _fold_db_path(base_path: Path, fold_index: int) -> Path:
    suffix = base_path.suffix or ".db"
    return base_path.with_name(f"{base_path.stem}.fold-{fold_index:03d}{suffix}")


def _reset_sqlite_path(db_path: Path) -> None:
    for candidate in (
        db_path,
        Path(str(db_path) + "-wal"),
        Path(str(db_path) + "-shm"),
        Path(str(db_path) + "-journal"),
    ):
        try:
            candidate.unlink(missing_ok=True)
        except PermissionError:
            raise ValueError(f"Cannot reset existing fold database: {candidate}")


def _coerce_bool(value: object) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no"}:
            return False
    return None


def _coerce_float(value: object) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or parsed < 0:
        return None
    return parsed


def _prediction_target(intent: StrategyIntent) -> str:
    target = intent.metadata.get("prediction_target")
    if isinstance(target, str) and target:
        return target
    if "predicted_delta" in intent.metadata:
        return "signed_return_delta"
    prediction = intent.metadata.get("prediction")
    if prediction in {POSITIVE_EDGE_PREDICTION, NO_POSITIVE_EDGE_PREDICTION}:
        return FEE_ADJUSTED_EDGE_PREDICTION_TARGET
    if prediction in {"up", "down"}:
        return "signed_return_direction"
    return "strategy_intent"


def _predicted_positive_edge(intent: StrategyIntent) -> bool:
    metadata_value = _coerce_bool(intent.metadata.get("predicted_positive_edge"))
    if metadata_value is not None:
        return metadata_value
    prediction = intent.metadata.get("prediction")
    if prediction == POSITIVE_EDGE_PREDICTION:
        return True
    if prediction == NO_POSITIVE_EDGE_PREDICTION:
        return False
    if prediction == "up":
        return True
    if prediction == "down":
        return False
    return intent.side == "long"


def _prediction_direction(intent: StrategyIntent) -> Optional[str]:
    prediction = intent.metadata.get("prediction")
    if prediction == POSITIVE_EDGE_PREDICTION:
        return "up"
    if prediction == NO_POSITIVE_EDGE_PREDICTION:
        return None
    if "predicted_delta" in intent.metadata:
        try:
            predicted_delta = float(intent.metadata["predicted_delta"])
            if predicted_delta > 0:
                return "up"
            if predicted_delta < 0:
                return "down"
            return None
        except (TypeError, ValueError):
            return "up" if intent.side == "long" else None
    if prediction in {"up", "down"}:
        return str(prediction)
    return "up" if intent.side == "long" else None


def _evaluation_hurdle(
    intent: StrategyIntent,
    *,
    prediction_target: str,
    round_trip_cost_pct: float,
) -> tuple[float, str]:
    if (
        prediction_target == "signed_return_delta"
        or "predicted_delta" in intent.metadata
    ):
        effective_min_edge_pct = _coerce_float(
            intent.metadata.get("effective_min_edge_pct")
        )
        if effective_min_edge_pct is not None:
            return effective_min_edge_pct, "effective_min_edge_pct"

    if prediction_target == FEE_ADJUSTED_EDGE_PREDICTION_TARGET:
        label_metadata = intent.metadata.get("label")
        if isinstance(label_metadata, dict):
            label_hurdle_bps = _coerce_float(label_metadata.get("label_hurdle_bps"))
            if label_hurdle_bps is not None:
                return label_hurdle_bps / 10_000.0, "label_hurdle_bps"

        label_hurdle_bps = _coerce_float(intent.metadata.get("label_hurdle_bps"))
        if label_hurdle_bps is not None:
            return label_hurdle_bps / 10_000.0, "label_hurdle_bps"

    return round_trip_cost_pct, "round_trip_cost_pct"


def _score_intent(
    *,
    fold_index: int,
    intent: StrategyIntent,
    market_data: BacktestMarketData,
    generated_at: datetime,
    fee_bps: float,
    slippage_bps: float,
) -> Optional[MLWalkForwardPrediction]:
    timeframe = intent.timeframe
    current_ts = int(generated_at.timestamp())
    target_ts = current_ts + _timeframe_seconds(timeframe)
    current_bar = market_data.get_bar_at_or_before(intent.pair, timeframe, current_ts)
    future_bar = market_data.get_bar_at_or_after(intent.pair, timeframe, target_ts)
    if current_bar is None or future_bar is None:
        return None

    current_close = float(current_bar.close)
    future_close = float(future_bar.close)
    if current_close <= 0:
        return None

    realized_return = (future_close - current_close) / current_close
    prediction_target = _prediction_target(intent)
    predicted_positive_edge = _predicted_positive_edge(intent)
    predicted_direction = _prediction_direction(intent)
    realized_up = realized_return > 0.0
    round_trip_cost = _round_trip_cost_pct(fee_bps=fee_bps, slippage_bps=slippage_bps)
    evaluation_hurdle, evaluation_hurdle_source = _evaluation_hurdle(
        intent,
        prediction_target=prediction_target,
        round_trip_cost_pct=round_trip_cost,
    )
    tradeable_up = realized_return > evaluation_hurdle
    directional_correct = None
    if predicted_direction is not None:
        predicted_up = predicted_direction == "up"
        directional_correct = predicted_up == realized_up
    return MLWalkForwardPrediction(
        fold_index=fold_index,
        generated_at=generated_at,
        strategy_id=intent.strategy_id,
        pair=market_data.get_display_pair(intent.pair),
        timeframe=timeframe,
        side=intent.side,
        intent_type=intent.intent_type,
        confidence=float(intent.confidence),
        prediction_target=prediction_target,
        predicted_positive_edge=predicted_positive_edge,
        predicted_direction=predicted_direction,
        current_close=current_close,
        future_close=future_close,
        realized_return=realized_return,
        round_trip_cost_pct=round_trip_cost,
        evaluation_hurdle_pct=evaluation_hurdle,
        evaluation_hurdle_source=evaluation_hurdle_source,
        directional_correct=directional_correct,
        evaluation_hurdle_correct=predicted_positive_edge == tradeable_up,
        metadata=copy.deepcopy(intent.metadata),
    )


def _rate(successes: int, total: int) -> Optional[float]:
    if total <= 0:
        return None
    return successes / total


def _average(values: Iterable[float]) -> Optional[float]:
    collected = list(values)
    if not collected:
        return None
    return sum(collected) / len(collected)


def _median(values: Iterable[float]) -> Optional[float]:
    numbers = sorted(values)
    count = len(numbers)
    if count == 0:
        return None
    midpoint = count // 2
    if count % 2 == 1:
        return numbers[midpoint]
    return (numbers[midpoint - 1] + numbers[midpoint]) / 2.0


def _finite_float(value: object) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    parsed = float(value)
    if not math.isfinite(parsed):
        return None
    return parsed


def _flatten_numbers(value: object) -> list[float]:
    tolist = getattr(value, "tolist", None)
    if callable(tolist):
        value = tolist()
    parsed = _finite_float(value)
    if parsed is not None:
        return [parsed]
    if isinstance(value, (list, tuple)):
        flattened: list[float] = []
        for item in value:
            flattened.extend(_flatten_numbers(item))
        return flattened
    return []


def _json_numeric(value: object) -> Any:
    tolist = getattr(value, "tolist", None)
    if callable(tolist):
        value = tolist()
    parsed = _finite_float(value)
    if parsed is not None:
        return parsed
    if isinstance(value, (list, tuple)):
        return [_json_numeric(item) for item in value]
    return None


def _percentile_sorted(numbers: list[float], q: float) -> float:
    count = len(numbers)
    if count == 1:
        return numbers[0]
    position = (count - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return numbers[int(position)]
    lower_value = numbers[lower]
    upper_value = numbers[upper]
    return lower_value + (upper_value - lower_value) * (position - lower)


def _quantiles(values: Iterable[object]) -> dict[str, Any]:
    numbers = sorted(
        parsed for value in values if (parsed := _finite_float(value)) is not None
    )
    count = len(numbers)
    if count == 0:
        return {"count": 0}

    avg = sum(numbers) / count
    variance = sum((value - avg) ** 2 for value in numbers) / count
    return {
        "count": count,
        "min": numbers[0],
        "p1": _percentile_sorted(numbers, 0.01),
        "p25": _percentile_sorted(numbers, 0.25),
        "p50": _percentile_sorted(numbers, 0.50),
        "p75": _percentile_sorted(numbers, 0.75),
        "p90": _percentile_sorted(numbers, 0.90),
        "p95": _percentile_sorted(numbers, 0.95),
        "p99": _percentile_sorted(numbers, 0.99),
        "max": numbers[-1],
        "avg": avg,
        "std": math.sqrt(variance),
    }


def _threshold_counts(values: list[float]) -> list[dict[str, Any]]:
    total = len(values)
    return [
        {
            "threshold": threshold,
            "count": sum(1 for value in values if value > threshold),
            "rate": _rate(sum(1 for value in values if value > threshold), total),
        }
        for threshold in DIAGNOSTIC_RETURN_THRESHOLDS
    ]


def _regression_rows(
    predictions: list[MLWalkForwardPrediction],
) -> list[dict[str, float]]:
    rows: list[dict[str, float]] = []
    for prediction in predictions:
        predicted_delta = _finite_float(prediction.metadata.get("predicted_delta"))
        if predicted_delta is None:
            continue
        rows.append(
            {
                "predicted_delta": predicted_delta,
                "realized_return": prediction.realized_return,
                "evaluation_hurdle_pct": prediction.evaluation_hurdle_pct,
            }
        )
    return rows


def _regression_sweep_row(
    rows: list[dict[str, float]],
    *,
    name: str,
    selection_threshold_source: str,
    realized_threshold_source: str,
    selection_threshold_pct: Optional[float] = None,
    realized_threshold_pct: Optional[float] = None,
    quantile: Optional[float] = None,
) -> dict[str, Any]:
    if selection_threshold_pct is None:
        selected = [
            row
            for row in rows
            if row["predicted_delta"] > row["evaluation_hurdle_pct"]
        ]
    else:
        selected = [
            row for row in rows if row["predicted_delta"] > selection_threshold_pct
        ]

    if realized_threshold_pct is None:
        hits = [
            row for row in rows if row["realized_return"] > row["evaluation_hurdle_pct"]
        ]
        true_positive = [
            row
            for row in selected
            if row["realized_return"] > row["evaluation_hurdle_pct"]
        ]
    else:
        hits = [row for row in rows if row["realized_return"] > realized_threshold_pct]
        true_positive = [
            row
            for row in selected
            if row["realized_return"] > realized_threshold_pct
        ]

    total = len(rows)
    predicted_long_count = len(selected)
    realized_hit_count = len(hits)
    precision = _rate(len(true_positive), predicted_long_count)
    base_rate = _rate(realized_hit_count, total)
    payload: dict[str, Any] = {
        "name": name,
        "selection_threshold_source": selection_threshold_source,
        "realized_threshold_source": realized_threshold_source,
        "prediction_count": total,
        "predicted_long_count": predicted_long_count,
        "predicted_long_rate": _rate(predicted_long_count, total),
        "realized_hit_count": realized_hit_count,
        "realized_hit_rate": base_rate,
        "true_positive_count": len(true_positive),
        "precision": precision,
        "recall": _rate(len(true_positive), realized_hit_count),
        "lift_over_base_rate": (
            precision / base_rate
            if precision is not None and base_rate is not None and base_rate != 0.0
            else None
        ),
        "avg_predicted_delta_selected": _average(
            row["predicted_delta"] for row in selected
        ),
        "avg_realized_return_selected": _average(
            row["realized_return"] for row in selected
        ),
        "median_realized_return_selected": _median(
            row["realized_return"] for row in selected
        ),
    }
    if selection_threshold_pct is not None:
        payload["selection_threshold_pct"] = selection_threshold_pct
    else:
        payload["selection_threshold_quantiles"] = _quantiles(
            row["evaluation_hurdle_pct"] for row in rows
        )
    if realized_threshold_pct is not None:
        payload["realized_threshold_pct"] = realized_threshold_pct
    else:
        payload["realized_threshold_quantiles"] = _quantiles(
            row["evaluation_hurdle_pct"] for row in rows
        )
    if quantile is not None:
        payload["quantile"] = quantile
    return payload


def _build_predicted_delta_deciles(
    rows: list[dict[str, float]],
) -> list[dict[str, Any]]:
    if not rows:
        return []

    sorted_rows = sorted(rows, key=lambda row: row["predicted_delta"])
    total = len(sorted_rows)
    buckets: list[dict[str, Any]] = []
    for index in range(10):
        start = math.floor(total * index / 10)
        end = math.floor(total * (index + 1) / 10)
        bucket_rows = sorted_rows[start:end]
        if not bucket_rows:
            continue
        hit_count = sum(
            1
            for row in bucket_rows
            if row["realized_return"] > row["evaluation_hurdle_pct"]
        )
        buckets.append(
            {
                "bucket": f"decile_{index + 1:02d}",
                "rank": index + 1,
                "prediction_count": len(bucket_rows),
                "min_predicted_delta": bucket_rows[0]["predicted_delta"],
                "max_predicted_delta": bucket_rows[-1]["predicted_delta"],
                "avg_predicted_delta": _average(
                    row["predicted_delta"] for row in bucket_rows
                ),
                "avg_realized_return": _average(
                    row["realized_return"] for row in bucket_rows
                ),
                "median_realized_return": _median(
                    row["realized_return"] for row in bucket_rows
                ),
                "avg_evaluation_hurdle_pct": _average(
                    row["evaluation_hurdle_pct"] for row in bucket_rows
                ),
                "hit_rate_above_evaluation_hurdle": _rate(
                    hit_count,
                    len(bucket_rows),
                ),
            }
        )
    return buckets


def _decile_monotonicity(deciles: list[dict[str, Any]]) -> dict[str, Any]:
    eligible = [
        (
            float(value),
            int(decile.get("prediction_count") or 0),
        )
        for decile in deciles
        if (value := _finite_float(decile.get("avg_realized_return"))) is not None
    ]
    if len(eligible) < 2:
        return {"available": False}

    avg_values = [value for value, _count in eligible]
    decile_counts = [count for _value, count in eligible]
    midpoint = len(avg_values) // 2
    lower_avg = _average(avg_values[:midpoint])
    upper_avg = _average(avg_values[midpoint:])
    non_decreasing = all(
        later + NEAR_ZERO_THRESHOLD >= earlier
        for earlier, later in zip(avg_values, avg_values[1:])
    )

    total_rows = sum(decile_counts)
    lower_half_rows = sum(decile_counts[:midpoint])
    upper_half_rows = sum(decile_counts[midpoint:])
    insufficient_reasons: list[str] = []
    if total_rows < MIN_TOTAL_ROWS_FOR_MONOTONICITY:
        insufficient_reasons.append(
            f"Fewer than {MIN_TOTAL_ROWS_FOR_MONOTONICITY} regression rows across deciles "
            f"({total_rows})."
        )
    if (
        lower_half_rows < MIN_ROWS_PER_HALF_FOR_MONOTONICITY
        or upper_half_rows < MIN_ROWS_PER_HALF_FOR_MONOTONICITY
    ):
        insufficient_reasons.append(
            f"Each decile half needs at least {MIN_ROWS_PER_HALF_FOR_MONOTONICITY} rows "
            f"(lower={lower_half_rows}, upper={upper_half_rows})."
        )

    if insufficient_reasons or upper_avg is None or lower_avg is None:
        upper_improves: bool | str | None = MONOTONICITY_INSUFFICIENT_DATA
        non_decreasing_flag: bool | str | None = MONOTONICITY_INSUFFICIENT_DATA
    else:
        upper_improves = bool(upper_avg > lower_avg)
        non_decreasing_flag = non_decreasing

    payload: dict[str, Any] = {
        "available": True,
        "avg_realized_return_non_decreasing": non_decreasing_flag,
        "lower_half_avg_realized_return": lower_avg,
        "upper_half_avg_realized_return": upper_avg,
        "upper_minus_lower_avg_realized_return": (
            upper_avg - lower_avg
            if upper_avg is not None and lower_avg is not None
            else None
        ),
        "upper_half_improves": upper_improves,
        "total_decile_rows": total_rows,
        "lower_half_decile_rows": lower_half_rows,
        "upper_half_decile_rows": upper_half_rows,
        "min_total_rows_for_monotonicity": MIN_TOTAL_ROWS_FOR_MONOTONICITY,
        "min_rows_per_half_for_monotonicity": MIN_ROWS_PER_HALF_FOR_MONOTONICITY,
    }
    if insufficient_reasons:
        payload["insufficient_data_reasons"] = insufficient_reasons
    return payload


def _build_regression_calibration(
    predictions: list[MLWalkForwardPrediction],
) -> dict[str, Any]:
    rows = _regression_rows(predictions)
    if not rows:
        return {
            "prediction_count": 0,
            "threshold_sweeps": [],
            "predicted_delta_deciles": [],
            "monotonicity": {"available": False},
        }

    predicted_deltas = sorted(row["predicted_delta"] for row in rows)
    threshold_sweeps = [
        _regression_sweep_row(
            rows,
            name="evaluation_hurdle",
            selection_threshold_source="evaluation_hurdle_pct",
            realized_threshold_source="evaluation_hurdle_pct",
        )
    ]
    for threshold in DIAGNOSTIC_RETURN_THRESHOLDS:
        threshold_sweeps.append(
            _regression_sweep_row(
                rows,
                name=f"fixed_{str(threshold).replace('.', 'p')}",
                selection_threshold_source="fixed_threshold",
                realized_threshold_source="fixed_threshold",
                selection_threshold_pct=threshold,
                realized_threshold_pct=threshold,
            )
        )
    for quantile in PREDICTED_DELTA_QUANTILE_THRESHOLDS:
        threshold = _percentile_sorted(predicted_deltas, quantile)
        threshold_sweeps.append(
            _regression_sweep_row(
                rows,
                name=f"predicted_delta_p{int(quantile * 100)}",
                selection_threshold_source="predicted_delta_quantile",
                realized_threshold_source="evaluation_hurdle_pct",
                selection_threshold_pct=threshold,
                quantile=quantile,
            )
        )

    deciles = _build_predicted_delta_deciles(rows)
    return {
        "prediction_count": len(rows),
        "predicted_delta_quantiles": _quantiles(
            row["predicted_delta"] for row in rows
        ),
        "realized_return_quantiles": _quantiles(
            row["realized_return"] for row in rows
        ),
        "threshold_sweeps": threshold_sweeps,
        "predicted_delta_deciles": deciles,
        "monotonicity": _decile_monotonicity(deciles),
    }


def _binary_class_balance(labels: list[float]) -> Optional[dict[str, Any]]:
    if not labels:
        return None
    rounded: list[int] = []
    for label in labels:
        rounded_label = round(label)
        if rounded_label not in {0, 1} or abs(label - rounded_label) > 1e-9:
            return None
        rounded.append(int(rounded_label))
    positive_count = sum(1 for label in rounded if label == 1)
    negative_count = len(rounded) - positive_count
    return {
        "negative_label_count": negative_count,
        "positive_label_count": positive_count,
        "positive_label_rate": _rate(positive_count, len(rounded)),
    }


def _store_connection(store: object) -> Optional[sqlite3.Connection]:
    get_conn = getattr(store, "_get_conn", None)
    if not callable(get_conn):
        return None
    try:
        conn = get_conn()
    except Exception:
        return None
    return conn if isinstance(conn, sqlite3.Connection) else None


def _model_initialized(model: object, metadata: dict[str, Any]) -> bool:
    metadata_value = metadata.get("model_initialized")
    if isinstance(metadata_value, bool):
        return metadata_value
    return bool(
        hasattr(model, "coef_")
        or hasattr(model, "intercept_")
        or hasattr(model, "classes_")
    )


def _model_diagnostic(
    *,
    source: str,
    model_key: str,
    label_type: str,
    framework: str,
    version: int,
    updated_at: str,
    model_blob: bytes,
    checkpoint_state: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> tuple[dict[str, Any], Optional[object]]:
    metadata = metadata or {}
    diagnostic: dict[str, Any] = {
        "source": source,
        "model_key": model_key,
        "label_type": label_type,
        "framework": framework,
        "version": version,
        "updated_at": updated_at,
    }
    if checkpoint_state is not None:
        diagnostic["checkpoint_state"] = checkpoint_state
    for metadata_field in (
        "feature_schema_version",
        "model_backend",
        "model_framework",
        "model_config_key",
        "regression_epsilon_pct",
        "sgd_l2_alpha",
        "sgd_learning_rate_initial",
    ):
        if metadata_field in metadata:
            diagnostic[metadata_field] = metadata[metadata_field]

    try:
        # Trust boundary: model blobs are written by this bot into the
        # operator-owned SQLite DB and are not accepted from remote callers.
        model = pickle.loads(model_blob)
    except Exception as exc:
        diagnostic["load_error"] = str(exc)
        diagnostic["initialized"] = False
        return diagnostic, None

    coef = getattr(model, "coef_", None)
    intercept = getattr(model, "intercept_", None)
    coef_values = _flatten_numbers(coef)
    diagnostic.update(
        {
            "initialized": _model_initialized(model, metadata),
            "coef": _json_numeric(coef),
            "intercept": _json_numeric(intercept),
            "coefficient_norm": (
                math.sqrt(sum(value * value for value in coef_values))
                if coef_values
                else None
            ),
            "n_iter": _json_numeric(getattr(model, "n_iter_", None)),
            "t": _json_numeric(getattr(model, "t_", None)),
        }
    )
    scaler_schema_version = getattr(
        model, "scaler_schema_version", metadata.get("scaler_schema_version")
    )
    if scaler_schema_version is not None:
        diagnostic["scaler_schema_version"] = str(scaler_schema_version)
    if hasattr(model, "scaler_initialized") or "scaler_initialized" in metadata:
        diagnostic["scaler_initialized"] = bool(
            getattr(model, "scaler_initialized", metadata.get("scaler_initialized"))
        )
    return diagnostic, model


def _collect_model_diagnostics(
    store: object, strategy_id: str
) -> list[tuple[dict[str, Any], Optional[object]]]:
    conn = _store_connection(store)
    if conn is None:
        return []

    entries: list[tuple[dict[str, Any], Optional[object]]] = []
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT model_key, label_type, framework, version, updated_at, model_blob
            FROM ml_models
            WHERE strategy_id = ?
            ORDER BY model_key
            """,
            (strategy_id,),
        )
        for model_key, label_type, framework, version, updated_at, model_blob in (
            cursor.fetchall()
        ):
            entries.append(
                _model_diagnostic(
                    source="live_model",
                    model_key=str(model_key),
                    label_type=str(label_type),
                    framework=str(framework),
                    version=int(version or 1),
                    updated_at=str(updated_at),
                    model_blob=model_blob,
                )
            )

        cursor.execute(
            """
            SELECT
                model_key,
                label_type,
                framework,
                version,
                updated_at,
                checkpoint_state,
                metadata_json,
                model_blob
            FROM ml_model_checkpoints
            WHERE strategy_id = ?
            ORDER BY model_key, checkpoint_kind
            """,
            (strategy_id,),
        )
        for row in cursor.fetchall():
            metadata: dict[str, Any] = {}
            if row[6]:
                try:
                    parsed = json.loads(row[6])
                    if isinstance(parsed, dict):
                        metadata = parsed
                except json.JSONDecodeError:
                    metadata = {}
            entries.append(
                _model_diagnostic(
                    source="checkpoint",
                    model_key=str(row[0]),
                    label_type=str(row[1]),
                    framework=str(row[2]),
                    version=int(row[3] or 1),
                    updated_at=str(row[4]),
                    checkpoint_state=str(row[5] or "ready"),
                    metadata=metadata,
                    model_blob=row[7],
                )
            )
    except Exception:
        logger.warning(
            "Failed to collect ML model diagnostics for %s",
            strategy_id,
            exc_info=True,
        )
        return []
    return entries


def _training_label_summary(labels: list[float]) -> dict[str, Any]:
    balance = _binary_class_balance(labels)
    summary: dict[str, Any] = {
        "example_count": len(labels),
        "label_quantiles": _quantiles(labels),
    }
    if balance is not None:
        summary["class_balance"] = balance
    return summary


def _collect_training_diagnostics(store: object, strategy_id: str) -> dict[str, Any]:
    conn = _store_connection(store)
    if conn is None:
        return {"example_count": 0, "label_quantiles": {"count": 0}}

    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT model_key, label
            FROM ml_training_examples
            WHERE strategy_id = ?
            ORDER BY model_key, created_at
            """,
            (strategy_id,),
        )
        rows = [(str(model_key), float(label)) for model_key, label in cursor.fetchall()]
    except Exception:
        logger.warning(
            "Failed to collect ML training diagnostics for %s",
            strategy_id,
            exc_info=True,
        )
        return {"example_count": 0, "label_quantiles": {"count": 0}}

    labels = [label for _model_key, label in rows]
    by_model_key: dict[str, list[float]] = {}
    for model_key, label in rows:
        by_model_key.setdefault(model_key, []).append(label)

    summary = _training_label_summary(labels)
    summary["by_model_key"] = {
        model_key: _training_label_summary(model_labels)
        for model_key, model_labels in by_model_key.items()
    }
    return summary


def _features_from_prediction(prediction: MLWalkForwardPrediction) -> Optional[list[float]]:
    features = prediction.metadata.get("features")
    if not isinstance(features, dict):
        return None
    feature_names = _feature_names_from_prediction(prediction)
    values: list[float] = []
    for name in feature_names:
        value = _finite_float(features.get(name))
        if value is None:
            return None
        values.append(value)
    return values


def _feature_names_from_prediction(prediction: MLWalkForwardPrediction) -> list[str]:
    features = prediction.metadata.get("features")
    if isinstance(features, dict):
        raw_names = features.get("feature_names")
        if isinstance(raw_names, list) and raw_names:
            names = [str(name) for name in raw_names if isinstance(name, str) and name]
            if names:
                return names
    return list(ML_FEATURE_NAMES)


def _model_matches_prediction(
    model_key: str, prediction: MLWalkForwardPrediction
) -> bool:
    parts = model_key.split("|")
    if len(parts) < 2:
        return False
    if parts[0] == "global":
        return parts[1] == prediction.timeframe
    return parts[0] == prediction.pair and parts[1] == prediction.timeframe


def _select_model_entry_for_prediction(
    model_entries: list[tuple[dict[str, Any], Optional[object]]],
    prediction: MLWalkForwardPrediction,
) -> Optional[tuple[dict[str, Any], object]]:
    candidates = [
        (entry, model)
        for entry, model in model_entries
        if model is not None
        and entry.get("source") == "live_model"
        and _model_matches_prediction(str(entry.get("model_key") or ""), prediction)
    ]
    if not candidates:
        candidates = [
            (entry, model)
            for entry, model in model_entries
            if model is not None
            and _model_matches_prediction(str(entry.get("model_key") or ""), prediction)
        ]
    if not candidates:
        live_model_entries = [
            (entry, model)
            for entry, model in model_entries
            if model is not None and entry.get("source") == "live_model"
        ]
        if len(live_model_entries) == 1:
            return live_model_entries[0]
        return None
    return candidates[0]


def _select_model_for_prediction(
    model_entries: list[tuple[dict[str, Any], Optional[object]]],
    prediction: MLWalkForwardPrediction,
) -> Optional[object]:
    selected = _select_model_entry_for_prediction(model_entries, prediction)
    if selected is None:
        return None
    return selected[1]


def _decision_scores(
    predictions: list[MLWalkForwardPrediction],
    model_entries: list[tuple[dict[str, Any], Optional[object]]],
) -> list[float]:
    # Post-hoc reconstruction from final fold model state; this is meaningful only
    # because test folds are expected not to mutate models while learning is frozen.
    scores: list[float] = []
    for prediction in predictions:
        if prediction.prediction_target == "signed_return_delta":
            continue
        features = _features_from_prediction(prediction)
        if features is None:
            continue
        model = _select_model_for_prediction(model_entries, prediction)
        decision_function = getattr(model, "decision_function", None)
        if not callable(decision_function):
            continue
        try:
            result: Any = decision_function([features])
            raw_score = result[0]
        except Exception:
            continue
        score = _finite_float(raw_score)
        if score is not None:
            scores.append(score)
    return scores


def _build_prediction_diagnostics(
    predictions: list[MLWalkForwardPrediction],
    model_entries: list[tuple[dict[str, Any], Optional[object]]],
) -> dict[str, Any]:
    predicted_deltas = [
        float(prediction.metadata["predicted_delta"])
        for prediction in predictions
        if _finite_float(prediction.metadata.get("predicted_delta")) is not None
    ]
    decision_scores = _decision_scores(predictions, model_entries)
    positive_count = sum(1 for prediction in predictions if prediction.predicted_positive_edge)
    diagnostics: dict[str, Any] = {
        "prediction_count": len(predictions),
        "positive_edge_prediction_count": positive_count,
        "no_positive_edge_prediction_count": len(predictions) - positive_count,
        "confidence_quantiles": _quantiles(
            prediction.confidence for prediction in predictions
        ),
    }
    if predicted_deltas:
        diagnostics["predicted_delta_quantiles"] = _quantiles(predicted_deltas)
    if decision_scores:
        diagnostics["decision_score_quantiles"] = _quantiles(decision_scores)
    if predictions:
        diagnostics["predicted_class_counts"] = {
            "positive_edge": positive_count,
            "no_positive_edge": len(predictions) - positive_count,
        }
    return diagnostics


def _feature_schema_from_predictions(
    predictions: list[MLWalkForwardPrediction],
) -> Optional[str]:
    schema_versions = [
        prediction.metadata.get("feature_schema_version")
        for prediction in predictions
        if isinstance(prediction.metadata.get("feature_schema_version"), str)
    ]
    return str(schema_versions[0]) if schema_versions else None


def _feature_profile_from_predictions(
    predictions: list[MLWalkForwardPrediction],
) -> Optional[str]:
    profiles = [
        str(profile)
        for prediction in predictions
        if isinstance((features := prediction.metadata.get("features")), dict)
        and (profile := features.get("feature_profile"))
    ]
    return profiles[0] if profiles else None


def _feature_names_from_predictions(
    predictions: list[MLWalkForwardPrediction],
) -> list[str]:
    for prediction in predictions:
        names = _feature_names_from_prediction(prediction)
        if names:
            return names
    return list(ML_FEATURE_NAMES)


def _feature_exclusions_from_predictions(
    predictions: list[MLWalkForwardPrediction],
) -> list[str]:
    for prediction in predictions:
        features = prediction.metadata.get("features")
        if not isinstance(features, dict):
            continue
        excluded = features.get("feature_profile_excluded_features")
        if isinstance(excluded, list):
            return [str(name) for name in excluded if isinstance(name, str) and name]
    return []


def _feature_quantiles(
    rows: list[list[float]],
    feature_names: list[str],
) -> dict[str, Any]:
    return {
        name: _quantiles(row[index] for row in rows if index < len(row))
        for index, name in enumerate(feature_names)
    }


def _feature_health_thresholds() -> dict[str, Any]:
    return {
        "scaled_p50_abs_warn": SCALED_FEATURE_MEDIAN_ABS_WARNING_THRESHOLD,
        "scaled_std_min_warn": SCALED_FEATURE_STD_MIN_WARNING_THRESHOLD,
        "scaled_std_max_warn": SCALED_FEATURE_STD_MAX_WARNING_THRESHOLD,
        "scaled_tail_abs_warn": SCALED_FEATURE_TAIL_ABS_WARNING_THRESHOLD,
        "clipped_rate_warn": FEATURE_CLIPPED_RATE_WARNING_THRESHOLD,
        "clipped_rate_research_gate_fail": (
            FEATURE_CLIPPED_RATE_RESEARCH_GATE_THRESHOLD
        ),
        "high_risk_features": sorted(HIGH_RISK_SCALED_FEATURES),
    }


def _feature_tail_abs(quantiles: dict[str, Any]) -> Optional[float]:
    values = [
        _finite_float(quantiles.get(key))
        for key in ("p1", "p95", "p99")
    ]
    collected = [abs(value) for value in values if value is not None]
    return max(collected) if collected else None


def _build_feature_health_warnings(
    scaled_feature_quantiles: dict[str, Any],
    feature_names: list[str],
) -> list[str]:
    warnings: list[str] = []
    for name in feature_names:
        quantiles = scaled_feature_quantiles.get(name)
        if not isinstance(quantiles, dict):
            continue
        p50 = _finite_float(quantiles.get("p50"))
        if (
            p50 is not None
            and abs(p50) > SCALED_FEATURE_MEDIAN_ABS_WARNING_THRESHOLD
        ):
            warnings.append(
                f"Scaled feature {name} median is shifted from 0 "
                f"(p50={p50:.4g})."
            )
        std = _finite_float(quantiles.get("std"))
        if std is not None and (
            std < SCALED_FEATURE_STD_MIN_WARNING_THRESHOLD
            or std > SCALED_FEATURE_STD_MAX_WARNING_THRESHOLD
        ):
            warnings.append(
                f"Scaled feature {name} std is outside the expected range "
                f"(std={std:.4g})."
            )
        tail_abs = _feature_tail_abs(quantiles)
        if (
            tail_abs is not None
            and tail_abs > SCALED_FEATURE_TAIL_ABS_WARNING_THRESHOLD
        ):
            prefix = (
                "High-risk scaled feature"
                if name in HIGH_RISK_SCALED_FEATURES
                else "Scaled feature"
            )
            warnings.append(
                f"{prefix} {name} has tail values above "
                f"{SCALED_FEATURE_TAIL_ABS_WARNING_THRESHOLD:.1f} "
                f"(abs_tail={tail_abs:.4g})."
            )
    return warnings


def _feature_clipping_metadata(
    prediction: MLWalkForwardPrediction,
) -> Optional[dict[str, Any]]:
    features = prediction.metadata.get("features")
    if not isinstance(features, dict):
        return None
    clipping = features.get("feature_clipping")
    return clipping if isinstance(clipping, dict) else None


def _build_feature_clipping_diagnostics(
    predictions: list[MLWalkForwardPrediction],
) -> dict[str, Any]:
    versions = {
        str(features.get("feature_clipping_version"))
        for prediction in predictions
        if isinstance((features := prediction.metadata.get("features")), dict)
        and features.get("feature_clipping_version")
    }
    feature_stats: dict[str, Any] = {}
    for name, (default_cap_min, default_cap_max) in ML_FEATURE_CLIP_RANGES.items():
        entries: list[dict[str, Any]] = []
        for prediction in predictions:
            clipping = _feature_clipping_metadata(prediction)
            if clipping is None:
                continue
            details = clipping.get(name)
            if isinstance(details, dict):
                entries.append(details)
        if not entries:
            continue
        raw_values = [
            value
            for entry in entries
            if (value := _finite_float(entry.get("raw_value"))) is not None
        ]
        clipped_values = [
            value
            for entry in entries
            if (value := _finite_float(entry.get("clipped_value"))) is not None
        ]
        clipped_count = sum(1 for entry in entries if bool(entry.get("was_clipped")))
        observed_count = len(entries)
        cap_min = _finite_float(entries[0].get("cap_min"))
        cap_max = _finite_float(entries[0].get("cap_max"))
        clipped_rate = _rate(clipped_count, observed_count)
        feature_stats[name] = {
            "observed_count": observed_count,
            "clipped_count": clipped_count,
            "clipped_rate": clipped_rate,
            "research_gate_failed": (
                clipped_rate is not None
                and clipped_rate > FEATURE_CLIPPED_RATE_RESEARCH_GATE_THRESHOLD
            ),
            "cap_min": cap_min if cap_min is not None else default_cap_min,
            "cap_max": cap_max if cap_max is not None else default_cap_max,
            "raw_min": min(raw_values) if raw_values else None,
            "raw_max": max(raw_values) if raw_values else None,
            "clipped_min": min(clipped_values) if clipped_values else None,
            "clipped_max": max(clipped_values) if clipped_values else None,
        }
    return {
        "version": next(iter(versions)) if len(versions) == 1 else "mixed",
        "feature_count": len(feature_stats),
        "features": feature_stats,
        "thresholds": {
            "clipped_rate_warn": FEATURE_CLIPPED_RATE_WARNING_THRESHOLD,
            "clipped_rate_research_gate_fail": (
                FEATURE_CLIPPED_RATE_RESEARCH_GATE_THRESHOLD
            ),
        },
    }


def _build_feature_clipping_warnings(
    clipping_diagnostics: dict[str, Any],
) -> list[str]:
    warnings: list[str] = []
    features = clipping_diagnostics.get("features")
    if not isinstance(features, dict):
        return warnings
    for name, stats in features.items():
        if not isinstance(stats, dict):
            continue
        clipped_rate = _finite_float(stats.get("clipped_rate"))
        clipped_count = int(stats.get("clipped_count") or 0)
        observed_count = int(stats.get("observed_count") or 0)
        if (
            clipped_rate is not None
            and clipped_rate > FEATURE_CLIPPED_RATE_WARNING_THRESHOLD
        ):
            gate_note = (
                " Research gate fails for this feature."
                if clipped_rate > FEATURE_CLIPPED_RATE_RESEARCH_GATE_THRESHOLD
                else ""
            )
            warnings.append(
                f"Feature {name} clipped on {clipped_rate:.1%} of predictions "
                f"({clipped_count}/{observed_count}).{gate_note}"
            )
    return warnings


def _transform_scaled_features(
    model: object, rows: list[list[float]]
) -> Optional[list[list[float]]]:
    if not rows:
        return None
    if not bool(getattr(model, "scaler_initialized", False)):
        return None
    scaled = getattr(model, "_scaled", None)
    if not callable(scaled):
        return None
    try:
        transformed: Any = scaled(rows)
    except Exception:
        logger.warning("Failed to transform ML feature diagnostics", exc_info=True)
        return None
    scaled_rows: list[list[float]] = []
    try:
        for row in transformed:
            scaled_rows.append([float(value) for value in row])
    except (TypeError, ValueError):
        return None
    return scaled_rows


def _model_coefficients(
    model: object,
    feature_names: list[str],
) -> Optional[list[float]]:
    coefficients = _flatten_numbers(getattr(model, "coef_", None))
    if len(coefficients) < len(feature_names):
        return None
    return coefficients[: len(feature_names)]


def _p95_abs_contribution(values: list[float]) -> Optional[float]:
    if not values:
        return None
    return _percentile_sorted(sorted(values), 0.95)


def _build_feature_contribution_diagnostics(
    *,
    scaled_rows: list[list[float]],
    coefficient_rows: list[list[float]],
    feature_names: list[str],
) -> list[dict[str, Any]]:
    if not scaled_rows or len(scaled_rows) != len(coefficient_rows):
        return []

    diagnostics: list[dict[str, Any]] = []
    for index, name in enumerate(feature_names):
        coefficients = [
            row[index] for row in coefficient_rows if index < len(row)
        ]
        scaled_values = [row[index] for row in scaled_rows if index < len(row)]
        if not coefficients or len(coefficients) != len(scaled_values):
            continue
        coefficient = _average(coefficients)
        scaled_std = (_quantiles(scaled_values) or {}).get("std")
        scaled_std_value = _finite_float(scaled_std)
        contributions = [
            coefficient_value * scaled_value
            for coefficient_value, scaled_value in zip(coefficients, scaled_values)
        ]
        abs_contributions = [abs(value) for value in contributions]
        diagnostics.append(
            {
                "feature": name,
                "coefficient": coefficient,
                "coefficient_source": (
                    "single"
                    if len({round(value, 12) for value in coefficients}) == 1
                    else "mixed"
                ),
                "scaled_feature_std": scaled_std_value,
                "coef_times_scaled_std": (
                    coefficient * scaled_std_value
                    if coefficient is not None and scaled_std_value is not None
                    else None
                ),
                "avg_abs_row_contribution": _average(abs_contributions),
                "p95_abs_row_contribution": _p95_abs_contribution(abs_contributions),
                "row_count": len(abs_contributions),
            }
        )
    return sorted(
        diagnostics,
        key=lambda row: float(row.get("avg_abs_row_contribution") or 0.0),
        reverse=True,
    )


def _build_feature_diagnostics(
    predictions: list[MLWalkForwardPrediction],
    model_entries: list[tuple[dict[str, Any], Optional[object]]],
) -> dict[str, Any]:
    feature_rows: list[tuple[MLWalkForwardPrediction, list[float]]] = []
    for prediction in predictions:
        features = _features_from_prediction(prediction)
        if features is not None:
            feature_rows.append((prediction, features))
    rows = [features for _prediction, features in feature_rows]
    feature_names = _feature_names_from_predictions(predictions)

    diagnostics: dict[str, Any] = {
        "schema_version": _feature_schema_from_predictions(predictions),
        "feature_profile": _feature_profile_from_predictions(predictions),
        "feature_names": list(feature_names),
        "excluded_features": _feature_exclusions_from_predictions(predictions),
        "prediction_count": len(rows),
        "raw_feature_quantiles": _feature_quantiles(rows, feature_names),
        "scaled_available": False,
    }
    clipping_diagnostics = _build_feature_clipping_diagnostics(predictions)
    clipping_warnings = _build_feature_clipping_warnings(clipping_diagnostics)
    if clipping_diagnostics["feature_count"]:
        diagnostics["clipping"] = clipping_diagnostics
    if not feature_rows:
        if clipping_warnings:
            diagnostics["health_warnings"] = clipping_warnings
        return diagnostics

    scaled_rows: list[list[float]] = []
    coefficient_rows: list[list[float]] = []
    source_values: set[str] = set()
    model_key_values: set[str] = set()
    for prediction, features in feature_rows:
        selected = _select_model_entry_for_prediction(model_entries, prediction)
        if selected is None:
            if clipping_warnings:
                diagnostics["health_warnings"] = clipping_warnings
            return diagnostics
        entry, model = selected
        scaled_row = _transform_scaled_features(model, [features])
        if scaled_row is None or len(scaled_row) != 1:
            if clipping_warnings:
                diagnostics["health_warnings"] = clipping_warnings
            return diagnostics
        scaled_rows.append(scaled_row[0])
        coefficients = _model_coefficients(model, feature_names)
        if coefficients is not None:
            coefficient_rows.append(coefficients)
        source_values.add(str(entry.get("source") or "unknown"))
        model_key_values.add(str(entry.get("model_key") or "unknown"))

    diagnostics.update(
        {
            "scaled_available": True,
            "scaled_feature_quantiles": _feature_quantiles(
                scaled_rows,
                feature_names,
            ),
            "scaled_feature_source": (
                next(iter(source_values)) if len(source_values) == 1 else "mixed"
            ),
            "scaled_feature_source_model_key": (
                next(iter(model_key_values)) if len(model_key_values) == 1 else "mixed"
            ),
        }
    )
    if len(model_key_values) > 1:
        diagnostics["scaled_feature_source_model_keys"] = sorted(model_key_values)
    diagnostics["health_thresholds"] = _feature_health_thresholds()
    diagnostics["health_warnings"] = (
        _build_feature_health_warnings(
            diagnostics["scaled_feature_quantiles"],
            feature_names,
        )
        + clipping_warnings
    )
    contributions = _build_feature_contribution_diagnostics(
        scaled_rows=scaled_rows,
        coefficient_rows=coefficient_rows,
        feature_names=feature_names,
    )
    if contributions:
        diagnostics["linear_contributions"] = contributions
    return diagnostics


def _build_outcome_diagnostics(
    predictions: list[MLWalkForwardPrediction],
) -> dict[str, Any]:
    realized_returns = [prediction.realized_return for prediction in predictions]
    above_hurdle_count = sum(
        1
        for prediction in predictions
        if prediction.realized_return > prediction.evaluation_hurdle_pct
    )
    return {
        "realized_return_quantiles": _quantiles(realized_returns),
        "above_evaluation_hurdle": {
            "count": above_hurdle_count,
            "rate": _rate(above_hurdle_count, len(predictions)),
        },
        "evaluation_hurdle_sources": sorted(
            {prediction.evaluation_hurdle_source for prediction in predictions}
        ),
        "evaluation_hurdle_quantiles": _quantiles(
            prediction.evaluation_hurdle_pct for prediction in predictions
        ),
        "fixed_threshold_counts": _threshold_counts(realized_returns),
    }


def _build_fold_diagnostics(
    *,
    store: object,
    strategy_id: str,
    predictions: list[MLWalkForwardPrediction],
) -> dict[str, Any]:
    model_entries = _collect_model_diagnostics(store, strategy_id)
    return {
        "models": [copy.deepcopy(entry) for entry, _model in model_entries],
        "training": _collect_training_diagnostics(store, strategy_id),
        "predictions": _build_prediction_diagnostics(predictions, model_entries),
        "features": _build_feature_diagnostics(predictions, model_entries),
        "outcomes": _build_outcome_diagnostics(predictions),
    }


def _fold_indexes_with(
    fold_dicts: list[dict[str, Any]], predicate: Any
) -> list[int]:
    indexes: list[int] = []
    for fold in fold_dicts:
        try:
            fold_index = int(fold.get("fold_index") or 0)
        except (TypeError, ValueError):
            fold_index = 0
        if fold_index > 0 and predicate(fold):
            indexes.append(fold_index)
    return indexes


def _format_fold_list(indexes: list[int]) -> str:
    return ", ".join(str(index) for index in indexes)


def _build_diagnostic_warnings(fold_dicts: list[dict[str, Any]]) -> list[str]:
    warnings: list[str] = []
    model_snapshots = [
        model
        for fold in fold_dicts
        for model in ((fold.get("diagnostics") or {}).get("models") or [])
        if isinstance(model, dict)
    ]
    uninitialized_count = sum(1 for model in model_snapshots if not model.get("initialized"))
    zero_coef_count = sum(
        1
        for model in model_snapshots
        if _finite_float(model.get("coefficient_norm")) is not None
        and float(model["coefficient_norm"]) <= NEAR_ZERO_THRESHOLD
    )
    if uninitialized_count:
        warnings.append(f"{uninitialized_count} model snapshot(s) are uninitialized.")
    if zero_coef_count:
        warnings.append(
            f"Linear model coefficients are all zero or near-zero in {zero_coef_count} model snapshot(s)."
        )

    feature_health_warning_folds = _fold_indexes_with(
        fold_dicts,
        lambda fold: bool(
            ((fold.get("diagnostics") or {}).get("features") or {}).get(
                "health_warnings"
            )
        ),
    )
    if feature_health_warning_folds:
        warnings.append(
            "Scaled feature health warnings were reported in folds: "
            + _format_fold_list(feature_health_warning_folds)
            + "."
        )

    no_prediction_folds = _fold_indexes_with(
        fold_dicts,
        lambda fold: int(
            (((fold.get("diagnostics") or {}).get("predictions") or {}).get(
                "prediction_count"
            ))
            or 0
        )
        == 0,
    )
    if no_prediction_folds:
        warnings.append(
            "No scored predictions were produced in folds: "
            + _format_fold_list(no_prediction_folds)
            + "."
        )

    no_positive_prediction_folds = _fold_indexes_with(
        fold_dicts,
        lambda fold: int(
            (((fold.get("diagnostics") or {}).get("predictions") or {}).get(
                "prediction_count"
            ))
            or 0
        )
        > 0
        and int(
            (((fold.get("diagnostics") or {}).get("predictions") or {}).get(
                "positive_edge_prediction_count"
            ))
            or 0
        )
        == 0,
    )
    if no_positive_prediction_folds:
        warnings.append(
            "No positive-edge predictions were produced in folds: "
            + _format_fold_list(no_positive_prediction_folds)
            + "."
        )

    constant_delta_folds = _fold_indexes_with(
        fold_dicts,
        lambda fold: (
            _finite_float(
                (
                    ((fold.get("diagnostics") or {}).get("predictions") or {}).get(
                        "predicted_delta_quantiles"
                    )
                    or {}
                ).get("std")
            )
            is not None
            and float(
                (
                    ((fold.get("diagnostics") or {}).get("predictions") or {}).get(
                        "predicted_delta_quantiles"
                    )
                    or {}
                )["std"]
            )
            <= NEAR_ZERO_THRESHOLD
        ),
    )
    if constant_delta_folds:
        warnings.append(
            "Regression predicted deltas are constant or near-constant in folds: "
            + _format_fold_list(constant_delta_folds)
            + "."
        )

    constant_score_folds = _fold_indexes_with(
        fold_dicts,
        lambda fold: (
            _finite_float(
                (
                    ((fold.get("diagnostics") or {}).get("predictions") or {}).get(
                        "decision_score_quantiles"
                    )
                    or {}
                ).get("std")
            )
            is not None
            and float(
                (
                    ((fold.get("diagnostics") or {}).get("predictions") or {}).get(
                        "decision_score_quantiles"
                    )
                    or {}
                )["std"]
            )
            <= NEAR_ZERO_THRESHOLD
        ),
    )
    if constant_score_folds:
        warnings.append(
            "Classifier decision scores are constant or near-constant in folds: "
            + _format_fold_list(constant_score_folds)
            + "."
        )

    no_realized_edge_folds = _fold_indexes_with(
        fold_dicts,
        lambda fold: int(
            (
                ((fold.get("diagnostics") or {}).get("outcomes") or {}).get(
                    "above_evaluation_hurdle"
                )
                or {}
            ).get("count")
            or 0
        )
        == 0,
    )
    if no_realized_edge_folds:
        warnings.append(
            "No realized returns beat the evaluation hurdle in folds: "
            + _format_fold_list(no_realized_edge_folds)
            + "."
        )

    no_positive_label_folds = _fold_indexes_with(
        fold_dicts,
        lambda fold: (
            (((fold.get("diagnostics") or {}).get("training") or {}).get(
                "class_balance"
            ))
            is not None
            and int(
                (
                    (((fold.get("diagnostics") or {}).get("training") or {}).get(
                        "class_balance"
                    ))
                    or {}
                ).get("positive_label_count")
                or 0
            )
            == 0
        ),
    )
    if no_positive_label_folds:
        warnings.append(
            "No positive labels were recorded in folds: "
            + _format_fold_list(no_positive_label_folds)
            + "."
        )

    rare_positive_label_folds = _fold_indexes_with(
        fold_dicts,
        lambda fold: (
            (((fold.get("diagnostics") or {}).get("training") or {}).get(
                "class_balance"
            ))
            is not None
            and (
                _finite_float(
                    (
                        (((fold.get("diagnostics") or {}).get("training") or {}).get(
                            "class_balance"
                        ))
                        or {}
                    ).get("positive_label_rate")
                )
                or 0.0
            )
            > 0.0
            and (
                _finite_float(
                    (
                        (((fold.get("diagnostics") or {}).get("training") or {}).get(
                            "class_balance"
                        ))
                        or {}
                    ).get("positive_label_rate")
                )
                or 0.0
            )
            < RARE_POSITIVE_LABEL_RATE
        ),
    )
    if rare_positive_label_folds:
        warnings.append(
            "Positive labels are extremely rare in folds: "
            + _format_fold_list(rare_positive_label_folds)
            + "."
        )

    non_monotonic_delta_folds = _fold_indexes_with(
        fold_dicts,
        lambda fold: (
            (
                (
                    (fold.get("regression_calibration") or {}).get("monotonicity")
                    or {}
                ).get("upper_half_improves")
            )
            is False
        ),
    )
    if non_monotonic_delta_folds:
        warnings.append(
            "Higher predicted-delta buckets did not improve realized returns in folds: "
            + _format_fold_list(non_monotonic_delta_folds)
            + "."
        )

    insufficient_monotonicity_folds = _fold_indexes_with(
        fold_dicts,
        lambda fold: (
            (
                (
                    (fold.get("regression_calibration") or {}).get("monotonicity")
                    or {}
                ).get("upper_half_improves")
            )
            == MONOTONICITY_INSUFFICIENT_DATA
        ),
    )
    if insufficient_monotonicity_folds:
        warnings.append(
            "Predicted-delta monotonicity check skipped for insufficient data in folds: "
            + _format_fold_list(insufficient_monotonicity_folds)
            + "."
        )

    return warnings


def _build_prediction_metrics(
    predictions: list[MLWalkForwardPrediction],
) -> dict[str, Any]:
    total = len(predictions)
    positive_edge_predictions = [
        prediction for prediction in predictions if prediction.predicted_positive_edge
    ]
    no_positive_edge_predictions = [
        prediction
        for prediction in predictions
        if not prediction.predicted_positive_edge
    ]
    directional_predictions = [
        prediction
        for prediction in predictions
        if prediction.directional_correct is not None
    ]
    tradeable_long_hits = [
        prediction
        for prediction in positive_edge_predictions
        if prediction.realized_return > prediction.evaluation_hurdle_pct
    ]
    return {
        "prediction_count": total,
        "positive_edge_prediction_count": len(positive_edge_predictions),
        "no_positive_edge_prediction_count": len(no_positive_edge_predictions),
        "long_prediction_count": len(positive_edge_predictions),
        "flat_prediction_count": len(no_positive_edge_predictions),
        "directional_prediction_count": len(directional_predictions),
        "directional_accuracy": _rate(
            sum(
                1
                for prediction in directional_predictions
                if prediction.directional_correct
            ),
            len(directional_predictions),
        ),
        "edge_prediction_accuracy": _rate(
            sum(1 for prediction in predictions if prediction.fee_adjusted_correct),
            total,
        ),
        "fee_adjusted_hit_rate": _rate(
            sum(1 for prediction in predictions if prediction.fee_adjusted_correct),
            total,
        ),
        "precision_long": _rate(
            len(tradeable_long_hits), len(positive_edge_predictions)
        ),
        "avg_realized_return": _average(
            prediction.realized_return for prediction in predictions
        ),
        "avg_realized_return_when_long": _average(
            prediction.realized_return for prediction in positive_edge_predictions
        ),
        "avg_realized_return_when_flat": _average(
            prediction.realized_return for prediction in no_positive_edge_predictions
        ),
        "avg_confidence": _average(prediction.confidence for prediction in predictions),
    }


def _build_confidence_buckets(
    predictions: list[MLWalkForwardPrediction],
) -> list[dict[str, Any]]:
    ranges = [
        (0.0, 0.5),
        (0.5, 0.6),
        (0.6, 0.7),
        (0.7, 0.8),
        (0.8, 0.9),
        (0.9, 1.000000001),
    ]
    buckets: list[dict[str, Any]] = []
    for lower, upper in ranges:
        bucket_predictions = [
            prediction
            for prediction in predictions
            if lower <= prediction.confidence < upper
        ]
        if not bucket_predictions:
            continue
        display_upper = 1.0 if upper > 1.0 else upper
        total = len(bucket_predictions)
        buckets.append(
            {
                "bucket": f"{lower:.2f}-{display_upper:.2f}",
                "min_confidence": lower,
                "max_confidence": display_upper,
                "prediction_count": total,
                "edge_prediction_accuracy": _rate(
                    sum(
                        1
                        for prediction in bucket_predictions
                        if prediction.fee_adjusted_correct
                    ),
                    total,
                ),
                "directional_accuracy": _rate(
                    sum(
                        1
                        for prediction in bucket_predictions
                        if prediction.directional_correct
                    ),
                    sum(
                        1
                        for prediction in bucket_predictions
                        if prediction.directional_correct is not None
                    ),
                ),
                "fee_adjusted_hit_rate": _rate(
                    sum(
                        1
                        for prediction in bucket_predictions
                        if prediction.fee_adjusted_correct
                    ),
                    total,
                ),
                "avg_realized_return": _average(
                    prediction.realized_return for prediction in bucket_predictions
                ),
            }
        )
    return buckets


def _metric_value(metrics: dict[str, Any], key: str) -> float:
    value = metrics.get(key)
    return float(value) if isinstance(value, (int, float)) else 0.0


def _sweep_row_value(
    calibration: dict[str, Any], sweep_name: str, field_name: str
) -> Optional[float]:
    for row in calibration.get("threshold_sweeps") or []:
        if isinstance(row, dict) and row.get("name") == sweep_name:
            return _finite_float(row.get(field_name))
    return None


def _base_hit_rate_from_calibration(calibration: dict[str, Any]) -> Optional[float]:
    return _sweep_row_value(calibration, "evaluation_hurdle", "realized_hit_rate")


def _fold_non_monotonic(fold: dict[str, Any]) -> bool:
    return (
        (
            (fold.get("regression_calibration") or {}).get("monotonicity") or {}
        ).get("upper_half_improves")
        is False
    )


def _format_fold_failure(fold_index: int, message: str) -> str:
    return f"fold {fold_index}: {message}"


def _per_fold_self_standing_failures(
    fold_dicts: list[dict[str, Any]],
) -> list[str]:
    failures: list[str] = []
    for fold in fold_dicts:
        try:
            fold_index = int(fold.get("fold_index") or 0)
        except (TypeError, ValueError):
            fold_index = 0
        if fold_index <= 0:
            continue
        fold_metrics = fold.get("metrics") or {}
        if int(fold_metrics.get("positive_edge_prediction_count") or 0) <= 0:
            failures.append(
                _format_fold_failure(fold_index, "no positive-edge predictions")
            )
        if _metric_value(fold_metrics, "edge_prediction_accuracy") < 0.50:
            failures.append(
                _format_fold_failure(fold_index, "edge prediction accuracy below 50%")
            )
        if _fold_non_monotonic(fold):
            failures.append(
                _format_fold_failure(
                    fold_index, "predicted-delta deciles non-monotonic"
                )
            )
    return failures


@dataclass(frozen=True)
class PromotionTierResult:
    """Per-tier promotion outcome with the reasons that decided it."""

    tier: str
    clears: bool
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "clears": self.clears,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class PromotionAssessment:
    """Highest promotion tier cleared and per-tier breakdown of reasons."""

    tier: str
    tier_results: tuple[PromotionTierResult, ...]

    @property
    def is_operational(self) -> bool:
        return self.tier in PROMOTION_TIERS_OPERATIONAL

    def reasons_for_tier(self, tier: str) -> list[str]:
        for result in self.tier_results:
            if result.tier == tier:
                return list(result.reasons)
        return []

    def to_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "tiers": {result.tier: result.to_dict() for result in self.tier_results},
        }


def _assess_tier_research_promising(metrics: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    prediction_count = int(metrics.get("prediction_count") or 0)
    directional_count = int(metrics.get("directional_prediction_count") or 0)
    if prediction_count < 20:
        reasons.append("Fewer than 20 scored out-of-sample predictions.")
    if int(metrics.get("positive_edge_prediction_count") or 0) <= 0:
        reasons.append("No positive-edge predictions were scored.")
    if (
        directional_count == prediction_count
        and _metric_value(metrics, "directional_accuracy") < 0.52
    ):
        reasons.append("Directional accuracy is below 52%.")
    if _metric_value(metrics, "edge_prediction_accuracy") < 0.50:
        reasons.append("Edge prediction accuracy is below 50%.")
    return reasons


def _assess_tier_risk_overlay(
    metrics: dict[str, Any],
    calibration: dict[str, Any],
    fold_dicts: list[dict[str, Any]],
) -> list[str]:
    reasons: list[str] = []
    base_rate = _base_hit_rate_from_calibration(calibration)
    precision_long = _finite_float(metrics.get("precision_long"))
    if precision_long is None:
        reasons.append("Long precision is not available.")
    elif base_rate is None or base_rate <= 0:
        reasons.append("Base hit rate is unavailable to evaluate precision lift.")
    else:
        lift = precision_long / base_rate
        if lift < RISK_OVERLAY_MIN_PRECISION_LIFT:
            reasons.append(
                f"Precision lift over base hit rate is {lift:.2f}x "
                f"(need >= {RISK_OVERLAY_MIN_PRECISION_LIFT:.2f}x)."
            )

    selected_avg = _sweep_row_value(
        calibration, "predicted_delta_p95", "avg_realized_return_selected"
    )
    if selected_avg is None:
        reasons.append("p95 selected average realized return is not available.")
    elif selected_avg <= 0:
        reasons.append(
            f"p95 selected average realized return is non-positive "
            f"({selected_avg:.4%})."
        )

    p95_lift = _sweep_row_value(
        calibration, "predicted_delta_p95", "lift_over_base_rate"
    )
    if p95_lift is None:
        reasons.append("p95 lift over base rate is not available.")
    elif p95_lift < RISK_OVERLAY_MIN_P95_LIFT:
        reasons.append(
            f"p95 lift over base rate is {p95_lift:.2f}x "
            f"(need >= {RISK_OVERLAY_MIN_P95_LIFT:.2f}x)."
        )

    non_monotonic_folds = sorted(
        int(fold.get("fold_index") or 0)
        for fold in fold_dicts
        if _fold_non_monotonic(fold)
    )
    non_monotonic_folds = [index for index in non_monotonic_folds if index > 0]
    if non_monotonic_folds:
        reasons.append(
            "Predicted-delta deciles non-monotonic in folds: "
            + ", ".join(str(index) for index in non_monotonic_folds)
            + "."
        )

    return reasons


def _assess_tier_self_standing(
    metrics: dict[str, Any],
    calibration: dict[str, Any],
    fold_dicts: list[dict[str, Any]],
    round_trip_cost_pct: float,
) -> list[str]:
    reasons: list[str] = []
    precision_long = _finite_float(metrics.get("precision_long"))
    if precision_long is None or precision_long < SELF_STANDING_MIN_PRECISION_LONG:
        reasons.append(
            f"Long precision is below {SELF_STANDING_MIN_PRECISION_LONG:.0%} "
            "after estimated costs."
        )

    selected_avg = _sweep_row_value(
        calibration, "predicted_delta_p95", "avg_realized_return_selected"
    )
    cost_floor = SELF_STANDING_SELECTED_RETURN_COST_MULTIPLE * max(
        round_trip_cost_pct, 0.0
    )
    if selected_avg is None:
        reasons.append("p95 selected average realized return is not available.")
    elif selected_avg < cost_floor:
        reasons.append(
            f"p95 selected average realized return ({selected_avg:.4%}) is below "
            f"{SELF_STANDING_SELECTED_RETURN_COST_MULTIPLE:.0f}x round-trip cost "
            f"({cost_floor:.4%})."
        )

    fold_failures = _per_fold_self_standing_failures(fold_dicts)
    if fold_failures:
        reasons.append("Per-fold strict checks failed: " + "; ".join(fold_failures))

    return reasons


def _tier_pass_message(tier: str) -> str:
    return f"Walk-forward metrics clear the {tier.replace('_', ' ')} thresholds."


def _tier_blocked_message(blocked_by_tier: str) -> str:
    return f"Earlier tier {blocked_by_tier.replace('_', ' ')} did not clear."


def _assess_promotability(
    *,
    metrics: dict[str, Any],
    regression_calibration: dict[str, Any],
    fold_dicts: list[dict[str, Any]],
    round_trip_cost_pct: float,
) -> PromotionAssessment:
    """Evaluate promotion tiers from loosest (research) to strictest (self-standing).

    Each tier is gated by the prior one: if research-promising fails, the higher
    tiers carry a placeholder reason so operators see which gate actually fired.
    """

    research_reasons = _assess_tier_research_promising(metrics)
    if research_reasons:
        return PromotionAssessment(
            tier=PROMOTION_TIER_BLOCKED,
            tier_results=(
                PromotionTierResult(
                    tier=PROMOTION_TIER_RESEARCH,
                    clears=False,
                    reasons=tuple(research_reasons),
                ),
                PromotionTierResult(
                    tier=PROMOTION_TIER_RISK_OVERLAY,
                    clears=False,
                    reasons=(_tier_blocked_message(PROMOTION_TIER_RESEARCH),),
                ),
                PromotionTierResult(
                    tier=PROMOTION_TIER_SELF_STANDING,
                    clears=False,
                    reasons=(_tier_blocked_message(PROMOTION_TIER_RESEARCH),),
                ),
            ),
        )

    risk_overlay_reasons = _assess_tier_risk_overlay(
        metrics, regression_calibration, fold_dicts
    )
    if risk_overlay_reasons:
        return PromotionAssessment(
            tier=PROMOTION_TIER_RESEARCH,
            tier_results=(
                PromotionTierResult(
                    tier=PROMOTION_TIER_RESEARCH,
                    clears=True,
                    reasons=(_tier_pass_message(PROMOTION_TIER_RESEARCH),),
                ),
                PromotionTierResult(
                    tier=PROMOTION_TIER_RISK_OVERLAY,
                    clears=False,
                    reasons=tuple(risk_overlay_reasons),
                ),
                PromotionTierResult(
                    tier=PROMOTION_TIER_SELF_STANDING,
                    clears=False,
                    reasons=(_tier_blocked_message(PROMOTION_TIER_RISK_OVERLAY),),
                ),
            ),
        )

    self_standing_reasons = _assess_tier_self_standing(
        metrics, regression_calibration, fold_dicts, round_trip_cost_pct
    )
    if self_standing_reasons:
        return PromotionAssessment(
            tier=PROMOTION_TIER_RISK_OVERLAY,
            tier_results=(
                PromotionTierResult(
                    tier=PROMOTION_TIER_RESEARCH,
                    clears=True,
                    reasons=(_tier_pass_message(PROMOTION_TIER_RESEARCH),),
                ),
                PromotionTierResult(
                    tier=PROMOTION_TIER_RISK_OVERLAY,
                    clears=True,
                    reasons=(_tier_pass_message(PROMOTION_TIER_RISK_OVERLAY),),
                ),
                PromotionTierResult(
                    tier=PROMOTION_TIER_SELF_STANDING,
                    clears=False,
                    reasons=tuple(self_standing_reasons),
                ),
            ),
        )

    return PromotionAssessment(
        tier=PROMOTION_TIER_SELF_STANDING,
        tier_results=(
            PromotionTierResult(
                tier=PROMOTION_TIER_RESEARCH,
                clears=True,
                reasons=(_tier_pass_message(PROMOTION_TIER_RESEARCH),),
            ),
            PromotionTierResult(
                tier=PROMOTION_TIER_RISK_OVERLAY,
                clears=True,
                reasons=(_tier_pass_message(PROMOTION_TIER_RISK_OVERLAY),),
            ),
            PromotionTierResult(
                tier=PROMOTION_TIER_SELF_STANDING,
                clears=True,
                reasons=(_tier_pass_message(PROMOTION_TIER_SELF_STANDING),),
            ),
        ),
    )


def run_ml_walk_forward(
    config: AppConfig,
    start: datetime,
    end: datetime,
    *,
    strategy_id: str,
    timeframe: str,
    train_bars: int,
    test_bars: int,
    fee_bps: float = 25.0,
    slippage_bps: Optional[float] = None,
    db_path: Optional[str] = None,
    strict_data: bool = False,
) -> MLWalkForwardResult:
    """Evaluate an ML strategy by training and freezing over rolling windows."""

    if end <= start:
        raise ValueError("Walk-forward end must be after start")
    if fee_bps < 0:
        raise ValueError("fee_bps must be greater than or equal to 0")
    if slippage_bps is not None and slippage_bps < 0:
        raise ValueError("slippage_bps must be greater than or equal to 0")

    config_copy = _prepare_ml_config(
        config,
        strategy_id=strategy_id,
        timeframe=timeframe,
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
    )
    effective_slippage_bps = float(
        slippage_bps
        if slippage_bps is not None
        else config_copy.execution.max_slippage_bps
    )
    pairs = _configured_backtest_pairs(config_copy)
    market_data = BacktestMarketData(config_copy, pairs, [timeframe], start, end)
    preflight = market_data.get_preflight()
    if preflight.usable_series_count == 0:
        raise ValueError("No usable OHLC series found for ML walk-forward evaluation")
    if strict_data and (preflight.missing_series or preflight.partial_series):
        details: list[str] = []
        if preflight.missing_series:
            details.append("missing: " + ", ".join(preflight.missing_series))
        if preflight.partial_series:
            details.append("partial: " + ", ".join(preflight.partial_series))
        raise ValueError(
            "Historical data coverage failed in strict mode: " + "; ".join(details)
        )

    timestamps = list(market_data.iter_timestamps())
    fold_ranges = _build_walk_forward_folds(
        timestamps,
        train_bars=train_bars,
        test_bars=test_bars,
    )
    if not fold_ranges:
        raise ValueError(
            "Not enough replay bars to build one walk-forward fold "
            f"({len(timestamps)} available, {train_bars + test_bars} required)."
        )

    temp_dir: Optional[TemporaryDirectory[str]] = None
    if db_path:
        resolved_db_base_path = Path(db_path).expanduser().resolve()
        resolved_db_base_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        temp_dir = TemporaryDirectory(prefix="krakked-ml-walk-forward-")
        resolved_db_base_path = Path(temp_dir.name) / "ml-walk-forward.db"

    folds: list[MLWalkForwardFold] = []
    try:
        for fold_index, (train_timestamps, test_timestamps) in enumerate(
            fold_ranges,
            start=1,
        ):
            fold_config = copy.deepcopy(config_copy)
            fold_db_path = _fold_db_path(resolved_db_base_path, fold_index)
            _reset_sqlite_path(fold_db_path)
            portfolio_service = BacktestPortfolioService(
                fold_config,
                market_data,
                db_path=str(fold_db_path),
                starting_cash_usd=10_000.0,
            )
            try:
                _set_strategy_learning(fold_config, strategy_id, True)
                strategy_engine = StrategyEngine(
                    fold_config, market_data, portfolio_service
                )
                strategy_engine.initialize()

                for ts in train_timestamps:
                    now = datetime.fromtimestamp(ts, tz=UTC)
                    market_data.set_time(now)
                    strategy_engine.run_cycle(now=now)

                predictions: list[MLWalkForwardPrediction] = []
                _set_strategy_learning(fold_config, strategy_id, False)
                for ts in test_timestamps:
                    now = datetime.fromtimestamp(ts, tz=UTC)
                    market_data.set_time(now)
                    strategy_engine.run_cycle(now=now)
                    intents = [
                        intent
                        for intent in strategy_engine.last_cycle_intents
                        if intent.strategy_id == strategy_id
                        and intent.timeframe == timeframe
                    ]
                    for intent in intents:
                        scored = _score_intent(
                            fold_index=fold_index,
                            intent=intent,
                            market_data=market_data,
                            generated_at=now,
                            fee_bps=fee_bps,
                            slippage_bps=effective_slippage_bps,
                        )
                        if scored is not None:
                            predictions.append(scored)

                diagnostics = _build_fold_diagnostics(
                    store=portfolio_service.store,
                    strategy_id=strategy_id,
                    predictions=predictions,
                )
                folds.append(
                    MLWalkForwardFold(
                        fold_index=fold_index,
                        train_start=datetime.fromtimestamp(train_timestamps[0], tz=UTC),
                        train_end=datetime.fromtimestamp(train_timestamps[-1], tz=UTC),
                        test_start=datetime.fromtimestamp(test_timestamps[0], tz=UTC),
                        test_end=datetime.fromtimestamp(test_timestamps[-1], tz=UTC),
                        train_cycles=len(train_timestamps),
                        test_cycles=len(test_timestamps),
                        predictions=predictions,
                        diagnostics=diagnostics,
                    )
                )
            finally:
                close_store = getattr(portfolio_service.store, "close", None)
                if callable(close_store):
                    close_store()
    finally:
        shutdown = getattr(market_data, "shutdown", None)
        if callable(shutdown):
            shutdown()
        if temp_dir is not None:
            temp_dir.cleanup()

    display_pairs = [
        pair_meta.ws_symbol for pair_meta in market_data.get_universe_metadata()
    ]
    summary = MLWalkForwardSummary(
        start=start,
        end=end,
        strategy_id=strategy_id,
        timeframe=timeframe,
        train_bars=train_bars,
        test_bars=test_bars,
        folds=folds,
        fee_bps=float(fee_bps),
        slippage_bps=effective_slippage_bps,
        pairs=display_pairs,
        coverage_status=preflight.status,
        warnings=list(preflight.warnings),
    )
    return MLWalkForwardResult(summary=summary)


__all__ = [
    "MLWalkForwardFold",
    "MLWalkForwardPrediction",
    "MLWalkForwardResult",
    "MLWalkForwardSummary",
    "run_ml_walk_forward",
]
