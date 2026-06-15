"""Research-only ML risk-signal forecasting for next-window volatility."""

from __future__ import annotations

import copy
import math
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from statistics import mean
from typing import Any, Mapping, Sequence

import numpy as np

from krakked.config import AppConfig
from krakked.market_data.models import OHLCBar
from krakked.market_regime import (
    MarketRegimeOverlayParams,
    _clean_pairs,
    _default_pairs,
)

from .evidence_windows import (
    REQUIRED_REGIME_BUCKETS,
    build_evidence_window_context,
    context_by_window_key,
    parse_evidence_datetime,
    summarize_regime_coverage,
)
from .market_regime_overlay import _preflight_to_dict, _sort_bars, _strict_data_message
from .runner import BacktestMarketData

REPORT_TYPE = "ml_risk_signal_research"
REPORT_VERSION = 2
MODEL_ID = "har_rv_ols"
BASELINE_PREVIOUS = "previous_horizon_realized_vol"
BASELINE_ROLLING = "rolling_realized_vol"
BASELINE_EWMA = "riskmetrics_ewma"
NON_CURRENT_EXPOSURE_BUCKETS = tuple(REQUIRED_REGIME_BUCKETS)
MIN_EXPOSURE_BUCKETS = 2
MIN_EWMA_BEAT_PCT = 2.0
MAX_CURRENT_EWMA_LAG_PCT = 1.0
MAX_DISPLAY_EWMA_LAG_PCT = 1.0
MIN_CALIBRATION_RATIO = 0.75
MAX_CALIBRATION_RATIO = 1.33
MAX_LOG_VARIANCE_FORECAST = 50.0
MAX_NON_CURRENT_EVALUATION_OVERLAP_FRACTION = 0.25


@dataclass(frozen=True)
class MLRiskSignalResearchParams:
    horizon_bars: int = 6
    short_lookback_bars: int = 1
    medium_lookback_bars: int = 6
    long_lookback_bars: int = 42
    rolling_lookback_bars: int = 42
    ewma_lambda: float = 0.94
    min_training_examples: int = 30
    epsilon_variance: float = 1e-12

    def __post_init__(self) -> None:
        for field_name in (
            "horizon_bars",
            "short_lookback_bars",
            "medium_lookback_bars",
            "long_lookback_bars",
            "rolling_lookback_bars",
            "min_training_examples",
        ):
            if int(getattr(self, field_name)) < 1:
                raise ValueError(f"{field_name} must be at least 1")
        if not 0.0 < float(self.ewma_lambda) < 1.0:
            raise ValueError("ewma_lambda must be between 0 and 1")
        if float(self.epsilon_variance) <= 0.0:
            raise ValueError("epsilon_variance must be greater than 0")

    @property
    def max_feature_lookback_bars(self) -> int:
        return max(
            int(self.short_lookback_bars),
            int(self.medium_lookback_bars),
            int(self.long_lookback_bars),
            int(self.rolling_lookback_bars),
            int(self.horizon_bars),
        )


@dataclass(frozen=True)
class _WindowSpec:
    window_set: str
    window_id: str
    start: datetime
    end: datetime


@dataclass(frozen=True)
class _HARLinearModel:
    intercept: float
    coefficients: tuple[float, ...]
    feature_names: tuple[str, ...]
    training_examples: int

    def predict_log_variance(self, features: Sequence[float]) -> float:
        return self.intercept + sum(
            coefficient * float(value)
            for coefficient, value in zip(self.coefficients, features)
        )


@dataclass(frozen=True)
class _VarianceForecastResult:
    variances: list[float]
    clipped_low_count: int = 0
    clipped_high_count: int = 0


@dataclass
class MLRiskSignalResearchResult:
    generated_at: datetime
    summary: dict[str, Any]
    windows: list[dict[str, Any]]

    def to_report_dict(self) -> dict[str, Any]:
        return {
            "report_version": REPORT_VERSION,
            "report_type": REPORT_TYPE,
            "generated_at": self.generated_at.astimezone(UTC).isoformat(),
            "summary": copy.deepcopy(self.summary),
            "windows": copy.deepcopy(self.windows),
        }


def run_ml_risk_signal_research(
    config: AppConfig,
    *,
    window_sets: Mapping[str, Sequence[tuple[str, str, str]]],
    pairs: Sequence[str] | None = None,
    timeframe: str = "4h",
    benchmark_pair: str = "BTC/USD",
    params: MLRiskSignalResearchParams | None = None,
    strict_data: bool = False,
) -> MLRiskSignalResearchResult:
    params = params or MLRiskSignalResearchParams()
    selected_pairs = _clean_pairs(list(pairs or _default_pairs(config)))
    if benchmark_pair not in selected_pairs:
        selected_pairs.insert(0, benchmark_pair)

    regime_params = MarketRegimeOverlayParams(
        timeframe=timeframe,
        benchmark_pair=benchmark_pair,
    )
    context_map = context_by_window_key(
        build_evidence_window_context(
            config,
            window_sets=window_sets,
            pairs=selected_pairs,
            timeframe=timeframe,
            regime_params=regime_params,
        )
    )

    training_examples: list[dict[str, Any]] = []
    evaluation_rows: list[dict[str, Any]] = []
    window_reports: list[dict[str, Any]] = []
    for spec in _chronological_window_specs(window_sets):
        bars, preflight = _load_benchmark_bars(
            config,
            start=spec.start,
            end=spec.end,
            benchmark_pair=benchmark_pair,
            timeframe=timeframe,
            strict_data=strict_data,
        )
        examples = _build_forecast_examples(bars, params=params)
        eligible_training_examples = _examples_with_labels_before(
            training_examples,
            cutoff=spec.start,
        )
        model = _fit_har_rv_model(eligible_training_examples, params=params)
        forecast_result: _VarianceForecastResult | None = None
        status = "ready"
        if not examples:
            status = "insufficient_data"
        elif model is None:
            status = "insufficient_training"
        else:
            forecast_result = _predict_model_variances(
                model,
                examples,
                params=params,
            )

        context = context_map.get((spec.window_set, spec.window_id), {})
        report = _window_report(
            spec=spec,
            preflight=preflight,
            examples=examples,
            training_examples_available=len(training_examples),
            training_examples_used=len(eligible_training_examples),
            status=status,
            forecast_result=forecast_result,
            params=params,
            context=context,
        )
        window_reports.append(report)
        if forecast_result is not None:
            evaluation_rows.extend(
                _evaluation_rows(
                    examples,
                    model_forecasts=forecast_result.variances,
                    window=report,
                )
            )
        training_examples.extend(examples)

    summary = _summary(
        window_reports,
        evaluation_rows=evaluation_rows,
        params=params,
        timeframe=timeframe,
        benchmark_pair=benchmark_pair,
    )
    return MLRiskSignalResearchResult(
        generated_at=datetime.now(UTC),
        summary=summary,
        windows=window_reports,
    )


def _chronological_window_specs(
    window_sets: Mapping[str, Sequence[tuple[str, str, str]]],
) -> list[_WindowSpec]:
    specs: list[_WindowSpec] = []
    for window_set, windows in window_sets.items():
        for window_id, start_text, end_text in windows:
            specs.append(
                _WindowSpec(
                    window_set=window_set,
                    window_id=window_id,
                    start=parse_evidence_datetime(start_text),
                    end=parse_evidence_datetime(end_text),
                )
            )
    return sorted(
        specs,
        key=lambda spec: (
            spec.start,
            spec.end,
            spec.window_set,
            spec.window_id,
        ),
    )


def _examples_with_labels_before(
    examples: Sequence[Mapping[str, Any]],
    *,
    cutoff: datetime,
) -> list[Mapping[str, Any]]:
    cutoff_ts = int(cutoff.astimezone(UTC).timestamp())
    eligible: list[Mapping[str, Any]] = []
    for example in examples:
        label_end = _example_label_end_timestamp(example)
        if label_end is not None and label_end <= cutoff_ts:
            eligible.append(example)
    return eligible


def _example_label_end_timestamp(example: Mapping[str, Any]) -> int | None:
    label = example.get("label")
    if isinstance(label, Mapping):
        value = label.get("label_end_timestamp")
        if value is not None:
            return int(value)
    value = example.get("label_end_timestamp")
    return int(value) if value is not None else None


def _load_benchmark_bars(
    config: AppConfig,
    *,
    start: datetime,
    end: datetime,
    benchmark_pair: str,
    timeframe: str,
    strict_data: bool,
) -> tuple[list[OHLCBar], dict[str, Any]]:
    market_data = BacktestMarketData(config, [benchmark_pair], [timeframe], start, end)
    try:
        preflight = market_data.get_preflight()
        if strict_data and (preflight.missing_series or preflight.partial_series):
            raise ValueError(_strict_data_message("ML risk-signal research", preflight))
        market_data.set_time(end)
        bars = market_data.get_ohlc(benchmark_pair, timeframe, lookback=1_000_000)
        return _sort_bars(bars), _preflight_to_dict(preflight)
    finally:
        shutdown = getattr(market_data, "shutdown", None)
        if callable(shutdown):
            shutdown()


def _build_forecast_examples(
    bars: Sequence[OHLCBar],
    *,
    params: MLRiskSignalResearchParams,
) -> list[dict[str, Any]]:
    sorted_bars = _sort_bars(list(bars))
    ewma_per_bar = _ewma_per_bar_variances(sorted_bars, params=params)
    examples: list[dict[str, Any]] = []
    for index in range(len(sorted_bars)):
        example = _example_at_index(
            sorted_bars,
            index,
            params=params,
            ewma_per_bar_variance=ewma_per_bar[index],
        )
        if example is not None:
            examples.append(example)
    return examples


def _example_at_index(
    bars: Sequence[OHLCBar],
    index: int,
    *,
    params: MLRiskSignalResearchParams,
    ewma_per_bar_variance: float | None = None,
) -> dict[str, Any] | None:
    if index < params.max_feature_lookback_bars:
        return None
    if index + int(params.horizon_bars) >= len(bars):
        return None

    label_variance = _future_realized_variance(
        bars,
        index=index,
        horizon_bars=int(params.horizon_bars),
        epsilon=float(params.epsilon_variance),
    )
    if label_variance is None:
        return None

    short_rv = _past_realized_variance(
        bars,
        index=index,
        lookback_bars=int(params.short_lookback_bars),
        epsilon=float(params.epsilon_variance),
    )
    medium_rv = _past_realized_variance(
        bars,
        index=index,
        lookback_bars=int(params.medium_lookback_bars),
        epsilon=float(params.epsilon_variance),
    )
    long_rv = _past_realized_variance(
        bars,
        index=index,
        lookback_bars=int(params.long_lookback_bars),
        epsilon=float(params.epsilon_variance),
    )
    feature_vector = [math.log(short_rv), math.log(medium_rv), math.log(long_rv)]
    feature_values = dict(zip(_feature_names(params), feature_vector))
    previous_variance = _previous_horizon_variance_forecast(
        bars,
        index=index,
        params=params,
    )
    rolling_variance = _rolling_variance_forecast(
        bars,
        index=index,
        params=params,
    )
    if ewma_per_bar_variance is None:
        ewma_per_bar_variance = _ewma_per_bar_variances(
            bars[: index + 1],
            params=params,
        )[-1]
    ewma_variance = max(
        float(ewma_per_bar_variance) * int(params.horizon_bars),
        float(params.epsilon_variance),
    )
    label_start = bars[index + 1]
    label_end = bars[index + int(params.horizon_bars)]
    return {
        "timestamp": int(bars[index].timestamp),
        "time": _bar_time(bars[index]),
        "index": int(index),
        "feature_values": feature_values,
        "feature_vector": feature_vector,
        "label": {
            "horizon_bars": int(params.horizon_bars),
            "realized_variance": label_variance,
            "realized_volatility": math.sqrt(label_variance),
            "label_formula": (
                "sqrt(sum(log(close[t+i] / close[t+i-1])^2 for i=1..horizon))"
            ),
            "label_start_timestamp": int(label_start.timestamp),
            "label_start_time": _bar_time(label_start),
            "label_end_timestamp": int(label_end.timestamp),
            "label_end_time": _bar_time(label_end),
        },
        "baseline_variance_forecasts": {
            BASELINE_PREVIOUS: previous_variance,
            BASELINE_ROLLING: rolling_variance,
            BASELINE_EWMA: ewma_variance,
        },
    }


def _future_realized_variance(
    bars: Sequence[OHLCBar],
    *,
    index: int,
    horizon_bars: int,
    epsilon: float,
) -> float | None:
    if index + horizon_bars >= len(bars):
        return None
    variance = 0.0
    for offset in range(1, horizon_bars + 1):
        variance += _squared_log_return(bars[index + offset - 1], bars[index + offset])
    return max(variance, epsilon)


def _past_realized_variance(
    bars: Sequence[OHLCBar],
    *,
    index: int,
    lookback_bars: int,
    epsilon: float,
) -> float:
    if index < lookback_bars:
        return epsilon
    variance = 0.0
    for offset in range(index - lookback_bars + 1, index + 1):
        variance += _squared_log_return(bars[offset - 1], bars[offset])
    return max(variance, epsilon)


def _previous_horizon_variance_forecast(
    bars: Sequence[OHLCBar],
    *,
    index: int,
    params: MLRiskSignalResearchParams,
) -> float:
    return _past_realized_variance(
        bars,
        index=index,
        lookback_bars=int(params.horizon_bars),
        epsilon=float(params.epsilon_variance),
    )


def _rolling_variance_forecast(
    bars: Sequence[OHLCBar],
    *,
    index: int,
    params: MLRiskSignalResearchParams,
) -> float:
    realized = _past_realized_variance(
        bars,
        index=index,
        lookback_bars=int(params.rolling_lookback_bars),
        epsilon=float(params.epsilon_variance),
    )
    per_bar = realized / float(params.rolling_lookback_bars)
    return max(
        per_bar * float(params.horizon_bars),
        float(params.epsilon_variance),
    )


def _ewma_per_bar_variances(
    bars: Sequence[OHLCBar],
    *,
    params: MLRiskSignalResearchParams,
) -> list[float]:
    if not bars:
        return []
    values = [float(params.epsilon_variance)] * len(bars)
    current = float(params.epsilon_variance)
    for index in range(1, len(bars)):
        squared_return = _squared_log_return(bars[index - 1], bars[index])
        if index == 1:
            current = max(squared_return, float(params.epsilon_variance))
        else:
            current = (
                float(params.ewma_lambda) * current
                + (1.0 - float(params.ewma_lambda)) * squared_return
            )
            current = max(current, float(params.epsilon_variance))
        values[index] = current
    return values


def _squared_log_return(previous: OHLCBar, current: OHLCBar) -> float:
    if previous.close <= 0.0 or current.close <= 0.0:
        return 0.0
    return math.log(float(current.close) / float(previous.close)) ** 2


def _fit_har_rv_model(
    examples: Sequence[Mapping[str, Any]],
    *,
    params: MLRiskSignalResearchParams,
) -> _HARLinearModel | None:
    if len(examples) < int(params.min_training_examples):
        return None
    features = np.asarray(
        [list(example["feature_vector"]) for example in examples],
        dtype=float,
    )
    targets = np.asarray(
        [
            math.log(
                max(
                    float(example["label"]["realized_variance"]),
                    params.epsilon_variance,
                )
            )
            for example in examples
        ],
        dtype=float,
    )
    design = np.column_stack([np.ones(len(features)), features])
    coefficients, *_ = np.linalg.lstsq(design, targets, rcond=None)
    return _HARLinearModel(
        intercept=float(coefficients[0]),
        coefficients=tuple(float(value) for value in coefficients[1:]),
        feature_names=tuple(_feature_names(params)),
        training_examples=len(examples),
    )


def _predict_model_variances(
    model: _HARLinearModel,
    examples: Sequence[Mapping[str, Any]],
    *,
    params: MLRiskSignalResearchParams,
) -> _VarianceForecastResult:
    variances: list[float] = []
    clipped_low_count = 0
    clipped_high_count = 0
    for example in examples:
        result = _variance_from_log_prediction(
            model.predict_log_variance(example["feature_vector"]),
            params=params,
        )
        variances.append(result["variance"])
        clipped_low_count += int(result["clipped_low"])
        clipped_high_count += int(result["clipped_high"])
    return _VarianceForecastResult(
        variances=variances,
        clipped_low_count=clipped_low_count,
        clipped_high_count=clipped_high_count,
    )


def _variance_from_log_prediction(
    log_variance: float,
    *,
    params: MLRiskSignalResearchParams,
) -> dict[str, Any]:
    floor = math.log(float(params.epsilon_variance))
    ceiling = MAX_LOG_VARIANCE_FORECAST
    value = float(log_variance)
    clipped_low = False
    clipped_high = False
    if not math.isfinite(value):
        clipped_high = value > 0.0
        clipped_low = not clipped_high
        value = ceiling if clipped_high else floor
    elif value < floor:
        clipped_low = True
        value = floor
    elif value > ceiling:
        clipped_high = True
        value = ceiling
    return {
        "variance": max(math.exp(value), float(params.epsilon_variance)),
        "clipped_low": clipped_low,
        "clipped_high": clipped_high,
        "log_variance": value,
    }


def _window_report(
    *,
    spec: _WindowSpec,
    preflight: Mapping[str, Any],
    examples: Sequence[Mapping[str, Any]],
    training_examples_available: int,
    training_examples_used: int,
    status: str,
    forecast_result: _VarianceForecastResult | None,
    params: MLRiskSignalResearchParams,
    context: Mapping[str, Any],
) -> dict[str, Any]:
    actuals = [float(example["label"]["realized_variance"]) for example in examples]
    baselines = {
        baseline: {
            "variance_forecast_units": "sum_of_next_horizon_log_return_squares",
            "metrics": _forecast_metrics(
                actuals,
                [
                    float(example["baseline_variance_forecasts"][baseline])
                    for example in examples
                ],
                epsilon=float(params.epsilon_variance),
            ),
        }
        for baseline in (BASELINE_PREVIOUS, BASELINE_ROLLING, BASELINE_EWMA)
    }
    model_metrics = (
        _forecast_metrics(
            actuals,
            [float(value) for value in forecast_result.variances],
            epsilon=float(params.epsilon_variance),
        )
        if forecast_result is not None
        else None
    )
    report = {
        "window_set": spec.window_set,
        "window_id": spec.window_id,
        "start": spec.start.isoformat(),
        "end": spec.end.isoformat(),
        "status": status,
        "strict_data_ready": not bool(preflight.get("missing_series"))
        and not bool(preflight.get("partial_series")),
        "preflight": dict(preflight),
        "training_examples_before": int(training_examples_available),
        "training_examples_used": int(training_examples_used),
        "training_examples_excluded_overlap": int(
            training_examples_available - training_examples_used
        ),
        "training_examples_added": len(examples),
        "example_count": len(examples),
        "first_example_time": examples[0]["time"] if examples else None,
        "last_example_time": examples[-1]["time"] if examples else None,
        "first_label_start_time": (
            _example_label_time(examples[0], "label_start") if examples else None
        ),
        "last_label_end_time": (
            _example_label_time(examples[-1], "label_end") if examples else None
        ),
        "forecast_skill": {
            "baselines": baselines,
            "model": {
                "model_id": MODEL_ID,
                "metrics": model_metrics,
                "prediction_clipping": {
                    "clipped_low_count": (
                        forecast_result.clipped_low_count if forecast_result else 0
                    ),
                    "clipped_high_count": (
                        forecast_result.clipped_high_count if forecast_result else 0
                    ),
                },
            },
        },
    }
    if context:
        report["market_bucket"] = context.get("market_bucket")
        report["evidence_bucket"] = context.get("evidence_bucket")
        report["benchmark_return_pct"] = context.get("benchmark_return_pct")
        report["basket_return_pct"] = context.get("basket_return_pct")
        report["benchmark_max_drawdown_pct"] = context.get("benchmark_max_drawdown_pct")
    return report


def _evaluation_rows(
    examples: Sequence[Mapping[str, Any]],
    *,
    model_forecasts: Sequence[float],
    window: Mapping[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for example, model_variance in zip(examples, model_forecasts):
        rows.append(
            {
                "window_set": window.get("window_set"),
                "window_id": window.get("window_id"),
                "evidence_bucket": window.get("evidence_bucket"),
                "timestamp": example["timestamp"],
                "actual_variance": float(example["label"]["realized_variance"]),
                "model_variance": float(model_variance),
                "baseline_variances": dict(example["baseline_variance_forecasts"]),
            }
        )
    return rows


def _summary(
    windows: Sequence[Mapping[str, Any]],
    *,
    evaluation_rows: Sequence[Mapping[str, Any]],
    params: MLRiskSignalResearchParams,
    timeframe: str,
    benchmark_pair: str,
) -> dict[str, Any]:
    bucket_counts, regime_coverage_sufficient = summarize_regime_coverage(
        window.get("evidence_bucket") for window in windows
    )
    forecast_skill = _forecast_skill_summary(
        evaluation_rows,
        params=params,
    )
    outcomes = _pre_registered_outcomes(
        windows,
        forecast_skill=forecast_skill,
    )
    return {
        "research_only": True,
        "runtime_wiring_approved": False,
        "timeframe": timeframe,
        "benchmark_pair": benchmark_pair,
        "target": {
            "name": "next_window_realized_volatility",
            "horizon_bars": int(params.horizon_bars),
            "label_formula": (
                "sqrt(sum(log(close[t+i] / close[t+i-1])^2 for i=1..horizon))"
            ),
            "feature_contract": (
                "features use bars at or before decision bar t; labels use the "
                "next horizon returns after t"
            ),
        },
        "feature_names": _feature_names(params),
        "model": {
            "model_id": MODEL_ID,
            "model_family": "HAR-RV-style ordinary least squares regression",
            "model_backend": "numpy_lstsq_ols",
            "trained_target": "log_next_horizon_realized_variance",
            "min_training_examples": int(params.min_training_examples),
        },
        "baselines": {
            BASELINE_PREVIOUS: {"horizon_bars": int(params.horizon_bars)},
            BASELINE_ROLLING: {
                "lookback_bars": int(params.rolling_lookback_bars),
                "scaled_to_horizon_bars": int(params.horizon_bars),
            },
            BASELINE_EWMA: {
                "lambda": float(params.ewma_lambda),
                "scaled_to_horizon_bars": int(params.horizon_bars),
            },
        },
        "params": _params_dict(params),
        "window_count": len(windows),
        "model_ready_windows": sum(
            1 for window in windows if window.get("status") == "ready"
        ),
        "model_evaluation_observations": len(evaluation_rows),
        "evidence_bucket_counts": bucket_counts,
        "regime_coverage_sufficient": regime_coverage_sufficient,
        "forecast_verdict_readiness": outcomes["forecast_verdict_readiness"],
        "forecast_skill": forecast_skill,
        "rule_performance": {
            "status": "deferred",
            "reason": (
                "Slice 1 evaluates forecast skill only; exposure rules are not "
                "simulated until the EWMA comparison gate passes."
            ),
        },
        "pre_registered_outcomes": outcomes,
        "lane_status": outcomes["lane_status"],
    }


def _feature_names(params: MLRiskSignalResearchParams) -> list[str]:
    return [
        f"har_short_log_realized_variance_{int(params.short_lookback_bars)}_bar",
        f"har_medium_log_realized_variance_{int(params.medium_lookback_bars)}_bar",
        f"har_long_log_realized_variance_{int(params.long_lookback_bars)}_bar",
    ]


def _params_dict(params: MLRiskSignalResearchParams) -> dict[str, Any]:
    return {
        "horizon_bars": int(params.horizon_bars),
        "short_lookback_bars": int(params.short_lookback_bars),
        "medium_lookback_bars": int(params.medium_lookback_bars),
        "long_lookback_bars": int(params.long_lookback_bars),
        "rolling_lookback_bars": int(params.rolling_lookback_bars),
        "ewma_lambda": float(params.ewma_lambda),
        "min_training_examples": int(params.min_training_examples),
        "epsilon_variance": float(params.epsilon_variance),
    }


def _forecast_skill_summary(
    rows: Sequence[Mapping[str, Any]],
    *,
    params: MLRiskSignalResearchParams,
) -> dict[str, Any]:
    overall = _forecast_skill_for_rows(rows, params=params)
    by_regime: dict[str, Any] = {}
    buckets = sorted(
        {str(row.get("evidence_bucket")) for row in rows if row.get("evidence_bucket")}
    )
    for bucket in buckets:
        bucket_rows = [row for row in rows if row.get("evidence_bucket") == bucket]
        by_regime[bucket] = _forecast_skill_for_rows(bucket_rows, params=params)
    return {
        "primary_metric": "qlike_variance_loss",
        "primary_metric_direction": "lower_is_better",
        "secondary_metric": "rmse_log_volatility",
        "overall": overall,
        "by_regime": by_regime,
    }


def _forecast_skill_for_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    params: MLRiskSignalResearchParams,
) -> dict[str, Any]:
    actuals = [float(row["actual_variance"]) for row in rows]
    model_forecasts = [float(row["model_variance"]) for row in rows]
    baselines: dict[str, Any] = {}
    for baseline in (BASELINE_PREVIOUS, BASELINE_ROLLING, BASELINE_EWMA):
        baselines[baseline] = _forecast_metrics(
            actuals,
            [float(row["baseline_variances"][baseline]) for row in rows],
            epsilon=float(params.epsilon_variance),
        )
    model_metrics = _forecast_metrics(
        actuals,
        model_forecasts,
        epsilon=float(params.epsilon_variance),
    )
    return {
        "observation_count": len(rows),
        "model": model_metrics,
        "baselines": baselines,
        "model_vs_ewma_qlike_improvement_pct": _qlike_improvement_pct(
            model_metrics,
            baselines[BASELINE_EWMA],
        ),
    }


def _forecast_metrics(
    actual_variances: Sequence[float],
    forecast_variances: Sequence[float],
    *,
    epsilon: float,
) -> dict[str, Any]:
    pairs = list(zip(actual_variances, forecast_variances))
    if not pairs:
        return {
            "count": 0,
            "qlike": None,
            "rmse_log_volatility": None,
            "mean_realized_volatility": None,
            "mean_forecast_volatility": None,
            "forecast_realized_vol_ratio": None,
        }
    qlike = mean(
        _qlike_loss(actual, forecast, epsilon=epsilon) for actual, forecast in pairs
    )
    rmse_log_vol = _log_vol_rmse(
        [actual for actual, _ in pairs],
        [forecast for _, forecast in pairs],
        epsilon=epsilon,
    )
    realized_vols = [math.sqrt(max(actual, epsilon)) for actual, _ in pairs]
    forecast_vols = [math.sqrt(max(forecast, epsilon)) for _, forecast in pairs]
    mean_realized = mean(realized_vols)
    mean_forecast = mean(forecast_vols)
    return {
        "count": len(pairs),
        "qlike": qlike,
        "rmse_log_volatility": rmse_log_vol,
        "mean_realized_volatility": mean_realized,
        "mean_forecast_volatility": mean_forecast,
        "forecast_realized_vol_ratio": (
            mean_forecast / mean_realized if mean_realized > 0.0 else None
        ),
    }


def _qlike_loss(
    actual_variance: float, forecast_variance: float, *, epsilon: float
) -> float:
    actual = max(float(actual_variance), float(epsilon))
    forecast = max(float(forecast_variance), float(epsilon))
    ratio = actual / forecast
    return ratio - math.log(ratio) - 1.0


def _log_vol_rmse(
    actual_variances: Sequence[float],
    forecast_variances: Sequence[float],
    *,
    epsilon: float,
) -> float:
    pairs = list(zip(actual_variances, forecast_variances))
    if not pairs:
        return 0.0
    squared_errors = [
        (
            math.log(math.sqrt(max(float(actual), float(epsilon))))
            - math.log(math.sqrt(max(float(forecast), float(epsilon))))
        )
        ** 2
        for actual, forecast in pairs
    ]
    return math.sqrt(sum(squared_errors) / len(squared_errors))


def _qlike_improvement_pct(
    candidate_metrics: Mapping[str, Any],
    baseline_metrics: Mapping[str, Any],
) -> float | None:
    candidate = candidate_metrics.get("qlike")
    baseline = baseline_metrics.get("qlike")
    if candidate is None or baseline is None or float(baseline) <= 0.0:
        return None
    return ((float(baseline) - float(candidate)) / float(baseline)) * 100.0


def _pre_registered_outcomes(
    windows: Sequence[Mapping[str, Any]],
    *,
    forecast_skill: Mapping[str, Any],
) -> dict[str, Any]:
    readiness = _forecast_verdict_readiness(
        windows,
        forecast_skill=forecast_skill,
    )
    by_regime = forecast_skill.get("by_regime") or {}
    passing_buckets: list[str] = []
    bucket_improvements: dict[str, float | None] = {}
    for bucket in NON_CURRENT_EXPOSURE_BUCKETS:
        improvement = (by_regime.get(bucket) or {}).get(
            "model_vs_ewma_qlike_improvement_pct"
        )
        bucket_improvements[bucket] = improvement
        if improvement is not None and float(improvement) >= MIN_EWMA_BEAT_PCT:
            passing_buckets.append(bucket)

    current_improvement = (by_regime.get("current_rolling") or {}).get(
        "model_vs_ewma_qlike_improvement_pct"
    )
    current_rolling_present = any(
        window.get("evidence_bucket") == "current_rolling" for window in windows
    )
    current_rolling_evaluable = current_improvement is not None
    current_not_worse = (
        current_rolling_evaluable
        and float(current_improvement) >= -MAX_CURRENT_EWMA_LAG_PCT
    )
    strict_data_ready = bool(windows) and all(
        bool(window.get("strict_data_ready")) for window in windows
    )
    has_model_observations = (
        int((forecast_skill.get("overall") or {}).get("observation_count", 0) or 0) > 0
    )
    exposure_gate = {
        "readiness_passed": bool(readiness["ready_for_exposure_verdict"]),
        "strict_data_ready": strict_data_ready,
        "has_model_observations": has_model_observations,
        "current_rolling_present": current_rolling_present,
        "current_rolling_evaluable": current_rolling_evaluable,
        "beats_ewma_qlike_by_2pct_in_2_non_current_buckets": (
            len(passing_buckets) >= MIN_EXPOSURE_BUCKETS
        ),
        "current_rolling_not_worse_than_ewma_by_1pct": current_not_worse,
    }
    exposure_gate["passed"] = all(exposure_gate.values())

    overall = forecast_skill.get("overall") or {}
    overall_improvement = overall.get("model_vs_ewma_qlike_improvement_pct")
    model_metrics = overall.get("model") or {}
    ratio = model_metrics.get("forecast_realized_vol_ratio")
    calibrated = (
        ratio is not None
        and MIN_CALIBRATION_RATIO <= float(ratio) <= MAX_CALIBRATION_RATIO
    )
    not_materially_worse = (
        overall_improvement is not None
        and float(overall_improvement) >= -MAX_DISPLAY_EWMA_LAG_PCT
    )
    display_gate = {
        "has_model_observations": has_model_observations,
        "not_materially_worse_than_ewma": not_materially_worse,
        "calibrated_mean_vol_ratio": calibrated,
        "readiness_passed": bool(readiness["ready_for_exposure_verdict"]),
    }
    display_gate["metric_passed"] = (
        has_model_observations and not_materially_worse and calibrated
    )
    display_gate["passed"] = (
        display_gate["metric_passed"] and display_gate["readiness_passed"]
    )

    if exposure_gate["passed"]:
        lane_status = "continue_to_rule_research"
    elif not readiness["ready_for_exposure_verdict"]:
        lane_status = str(readiness["status"])
    elif display_gate["passed"]:
        lane_status = "display_only_candidate"
    else:
        lane_status = "close_volatility_forecast_lane"

    return {
        "exposure_research_gate": exposure_gate,
        "display_only_gate": display_gate,
        "forecast_verdict_readiness": readiness,
        "non_current_bucket_qlike_improvement_pct": bucket_improvements,
        "passing_non_current_buckets": passing_buckets,
        "current_rolling_present": current_rolling_present,
        "current_rolling_evaluable": current_rolling_evaluable,
        "current_rolling_qlike_improvement_pct": current_improvement,
        "kill_criterion": {
            "if_exposure_gate_fails": (
                "close this volatility-forecasting lane instead of iterating "
                "model variants on the same target"
            ),
            "triggered": (
                readiness["ready_for_exposure_verdict"]
                and lane_status == "close_volatility_forecast_lane"
            ),
        },
        "lane_status": lane_status,
    }


def _forecast_verdict_readiness(
    windows: Sequence[Mapping[str, Any]],
    *,
    forecast_skill: Mapping[str, Any],
) -> dict[str, Any]:
    status_counts = Counter(
        str(window.get("status") or "unknown") for window in windows
    )
    overall = forecast_skill.get("overall") or {}
    observation_count = int(overall.get("observation_count", 0) or 0)
    model_ready_windows = int(status_counts.get("ready", 0))
    strict_data_ready = bool(windows) and all(
        bool(window.get("strict_data_ready")) for window in windows
    )
    missing_series_by_window = _series_gaps_by_window(windows, "missing_series")
    partial_series_by_window = _series_gaps_by_window(windows, "partial_series")
    has_data_shortfall = (
        bool(missing_series_by_window)
        or bool(partial_series_by_window)
        or status_counts.get("insufficient_data", 0) > 0
    )

    by_regime = forecast_skill.get("by_regime") or {}
    evaluable_non_current_buckets = sorted(
        bucket
        for bucket in NON_CURRENT_EXPOSURE_BUCKETS
        if (by_regime.get(bucket) or {}).get("model_vs_ewma_qlike_improvement_pct")
        is not None
    )
    current_rolling_present = any(
        window.get("evidence_bucket") == "current_rolling" for window in windows
    )
    current_rolling_evaluable = (by_regime.get("current_rolling") or {}).get(
        "model_vs_ewma_qlike_improvement_pct"
    ) is not None
    overlap_diagnostics = _evaluation_overlap_diagnostics(windows)
    has_blocking_overlaps = bool(overlap_diagnostics["blocking_overlaps"])

    blocking_reasons: list[str] = []
    status = "ready_for_verdict"
    if observation_count <= 0:
        blocking_reasons.append("no_model_evaluation_observations")
        if has_data_shortfall:
            status = "insufficient_data"
            blocking_reasons.append("benchmark_data_missing_or_insufficient")
        elif status_counts.get("insufficient_training", 0) > 0:
            status = "insufficient_training"
            blocking_reasons.append("insufficient_training_history")
        else:
            status = "insufficient_evaluation"
    else:
        if not strict_data_ready:
            status = "insufficient_data"
            blocking_reasons.append("strict_data_not_ready")
        if len(evaluable_non_current_buckets) < MIN_EXPOSURE_BUCKETS:
            if status == "ready_for_verdict":
                status = "insufficient_regime_coverage"
            blocking_reasons.append("fewer_than_2_evaluable_non_current_regime_buckets")
        if not current_rolling_present:
            if status == "ready_for_verdict":
                status = "insufficient_regime_coverage"
            blocking_reasons.append("current_rolling_window_missing")
        elif not current_rolling_evaluable:
            if status == "ready_for_verdict":
                status = "insufficient_regime_coverage"
            blocking_reasons.append("current_rolling_window_not_evaluable")
        if has_blocking_overlaps:
            if status == "ready_for_verdict":
                status = "insufficient_independence"
            blocking_reasons.append("overlapping_evaluation_windows")

    ready_for_exposure_verdict = status == "ready_for_verdict"
    return {
        "status": status,
        "ready_for_exposure_verdict": ready_for_exposure_verdict,
        "observation_count": observation_count,
        "model_ready_windows": model_ready_windows,
        "window_count": len(windows),
        "window_status_counts": dict(sorted(status_counts.items())),
        "strict_data_ready": strict_data_ready,
        "required_evaluable_non_current_regime_buckets": MIN_EXPOSURE_BUCKETS,
        "evaluable_non_current_regime_buckets": evaluable_non_current_buckets,
        "evaluable_non_current_regime_bucket_count": len(evaluable_non_current_buckets),
        "current_rolling_present": current_rolling_present,
        "current_rolling_evaluable": current_rolling_evaluable,
        "window_independence": overlap_diagnostics,
        "blocking_reasons": blocking_reasons,
        "coverage_gaps": {
            "missing_series_by_window": missing_series_by_window,
            "partial_series_by_window": partial_series_by_window,
        },
    }


def _evaluation_overlap_diagnostics(
    windows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    ranges: list[dict[str, Any]] = []
    for window in windows:
        if window.get("status") != "ready":
            continue
        start = _parse_report_datetime(window.get("first_example_time"))
        decision_end = _parse_report_datetime(window.get("last_example_time"))
        label_end = _parse_report_datetime(window.get("last_label_end_time"))
        end = label_end or decision_end
        if start is None or end is None or end < start:
            continue
        bucket = str(window.get("evidence_bucket") or "")
        ranges.append(
            {
                "window_set": window.get("window_set"),
                "window_id": window.get("window_id"),
                "evidence_bucket": bucket,
                "start": start,
                "end": end,
                "decision_end": decision_end,
                "label_end": label_end,
                "is_current_rolling": bucket == "current_rolling",
            }
        )

    overlaps: list[dict[str, Any]] = []
    blocking_overlaps: list[dict[str, Any]] = []
    for left_index, left in enumerate(ranges):
        left_start = left["start"]
        left_end = left["end"]
        for right in ranges[left_index + 1 :]:
            right_start = right["start"]
            right_end = right["end"]
            overlap_seconds = (
                min(left_end, right_end) - max(left_start, right_start)
            ).total_seconds()
            if overlap_seconds <= 0.0:
                continue
            shorter_seconds = max(
                min(
                    max((left_end - left_start).total_seconds(), 0.0),
                    max((right_end - right_start).total_seconds(), 0.0),
                ),
                1.0,
            )
            overlap_fraction = overlap_seconds / shorter_seconds
            blocks_verdict = (
                not bool(left["is_current_rolling"])
                and not bool(right["is_current_rolling"])
                and overlap_fraction > MAX_NON_CURRENT_EVALUATION_OVERLAP_FRACTION
            )
            overlap = {
                "left_window_id": left["window_id"],
                "left_evidence_bucket": left["evidence_bucket"],
                "right_window_id": right["window_id"],
                "right_evidence_bucket": right["evidence_bucket"],
                "overlap_hours": overlap_seconds / 3600.0,
                "overlap_fraction_of_shorter_range": overlap_fraction,
                "blocks_verdict": blocks_verdict,
            }
            overlaps.append(overlap)
            if blocks_verdict:
                blocking_overlaps.append(overlap)

    return {
        "status": "ready" if not blocking_overlaps else "overlapping",
        "max_allowed_non_current_overlap_fraction": (
            MAX_NON_CURRENT_EVALUATION_OVERLAP_FRACTION
        ),
        "range_basis": (
            "first decision-bar time through last label-end time; reports that "
            "lack label-end metadata fall back to last decision-bar time"
        ),
        "limitations": [
            "Pairwise overlap checks do not fully measure cumulative chained reuse."
        ],
        "ready_scored_window_count": len(ranges),
        "ready_scored_window_ranges": [
            _overlap_range_to_report_dict(item) for item in ranges
        ],
        "scored_example_range_overlaps": overlaps,
        "blocking_overlaps": blocking_overlaps,
    }


def _overlap_range_to_report_dict(item: Mapping[str, Any]) -> dict[str, Any]:
    start = item["start"]
    end = item["end"]
    decision_end = item.get("decision_end")
    label_end = item.get("label_end")
    return {
        "window_set": item.get("window_set"),
        "window_id": item.get("window_id"),
        "evidence_bucket": item.get("evidence_bucket"),
        "first_example_time": start.isoformat(),
        "last_example_time": decision_end.isoformat() if decision_end else None,
        "last_label_end_time": label_end.isoformat() if label_end else None,
        "overlap_range_end_time": end.isoformat(),
        "is_current_rolling": bool(item.get("is_current_rolling")),
        "duration_hours": (end - start).total_seconds() / 3600.0,
    }


def _series_gaps_by_window(
    windows: Sequence[Mapping[str, Any]],
    field_name: str,
) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    for window in windows:
        preflight = window.get("preflight")
        if not isinstance(preflight, Mapping):
            continue
        values = [str(value) for value in (preflight.get(field_name) or [])]
        if not values:
            continue
        gaps.append(
            {
                "window_set": window.get("window_set"),
                "window_id": window.get("window_id"),
                field_name: sorted(values),
            }
        )
    return gaps


def _parse_report_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _bar_time(bar: OHLCBar) -> str:
    return datetime.fromtimestamp(int(bar.timestamp), tz=UTC).isoformat()


def _example_label_time(example: Mapping[str, Any], field_prefix: str) -> str | None:
    label = example.get("label")
    if not isinstance(label, Mapping):
        return None
    time_value = label.get(f"{field_prefix}_time")
    if time_value is not None:
        return str(time_value)
    timestamp_value = label.get(f"{field_prefix}_timestamp")
    if timestamp_value is None:
        return None
    return datetime.fromtimestamp(int(timestamp_value), tz=UTC).isoformat()
