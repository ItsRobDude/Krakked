from __future__ import annotations

from datetime import UTC, datetime
import math
from types import SimpleNamespace

import pytest

import krakked.backtest.ml_risk_signal_research as risk_research
from krakked.backtest.ml_risk_signal_research import (
    BASELINE_EWMA,
    BASELINE_PREVIOUS,
    BASELINE_ROLLING,
    MLRiskSignalResearchParams,
    _HARLinearModel,
    _build_forecast_examples,
    _examples_with_labels_before,
    _example_at_index,
    _fit_har_rv_model,
    _forecast_metrics,
    _log_vol_rmse,
    _predict_model_variances,
    _qlike_loss,
    _summary,
)
from krakked.market_data.models import OHLCBar


def _bars(closes: list[float]) -> list[OHLCBar]:
    return [
        OHLCBar(
            timestamp=index * 14_400,
            open=close,
            high=close,
            low=close,
            close=close,
            volume=1.0,
        )
        for index, close in enumerate(closes)
    ]


def _forecast_row(
    bucket: str,
    *,
    actual: float = 1.0,
    model: float = 1.0,
    ewma: float = 1.6,
) -> dict[str, object]:
    return {
        "evidence_bucket": bucket,
        "actual_variance": actual,
        "model_variance": model,
        "baseline_variances": {
            BASELINE_PREVIOUS: ewma,
            BASELINE_ROLLING: ewma,
            BASELINE_EWMA: ewma,
        },
    }


def test_ml_risk_signal_params_reject_invalid_values() -> None:
    with pytest.raises(ValueError, match="horizon_bars"):
        MLRiskSignalResearchParams(horizon_bars=0)

    with pytest.raises(ValueError, match="ewma_lambda"):
        MLRiskSignalResearchParams(ewma_lambda=1.0)


def test_features_ignore_future_bars_and_label_uses_future_returns() -> None:
    params = MLRiskSignalResearchParams(
        horizon_bars=3,
        medium_lookback_bars=3,
        long_lookback_bars=5,
        rolling_lookback_bars=5,
    )
    closes = [100.0, 101.0, 102.0, 101.5, 103.0, 104.0, 103.5, 105.0, 104.0, 106.0]
    base = _example_at_index(_bars(closes), 6, params=params)  # noqa: SLF001
    assert base is not None

    future_closes = list(closes)
    future_closes[8] = 111.0
    future_changed = _example_at_index(  # noqa: SLF001
        _bars(future_closes),
        6,
        params=params,
    )
    assert future_changed is not None
    assert future_changed["feature_vector"] == base["feature_vector"]
    assert future_changed["label"]["realized_variance"] != pytest.approx(
        base["label"]["realized_variance"]
    )

    history_closes = list(closes)
    history_closes[4] = 111.0
    history_changed = _example_at_index(  # noqa: SLF001
        _bars(history_closes),
        6,
        params=params,
    )
    assert history_changed is not None
    assert history_changed["feature_vector"] != base["feature_vector"]
    assert history_changed["label"]["realized_variance"] == pytest.approx(
        base["label"]["realized_variance"]
    )


def test_feature_values_use_dynamic_har_names() -> None:
    params = MLRiskSignalResearchParams(
        horizon_bars=12,
        medium_lookback_bars=12,
        long_lookback_bars=84,
        rolling_lookback_bars=84,
    )
    closes = [100.0 + index * 0.5 for index in range(110)]
    example = _example_at_index(_bars(closes), 90, params=params)  # noqa: SLF001

    assert example is not None
    assert list(example["feature_values"]) == [
        "har_short_log_realized_variance_1_bar",
        "har_medium_log_realized_variance_12_bar",
        "har_long_log_realized_variance_84_bar",
    ]


def test_insufficient_future_bars_are_skipped() -> None:
    params = MLRiskSignalResearchParams(
        horizon_bars=3,
        medium_lookback_bars=3,
        long_lookback_bars=5,
        rolling_lookback_bars=5,
    )
    bars = _bars([100.0 + index for index in range(9)])

    assert _example_at_index(bars, 7, params=params) is None  # noqa: SLF001
    examples = _build_forecast_examples(bars, params=params)  # noqa: SLF001
    assert all(
        example["index"] + params.horizon_bars < len(bars) for example in examples
    )


def test_baselines_and_har_rv_forecasts_are_stable() -> None:
    params = MLRiskSignalResearchParams(
        horizon_bars=3,
        medium_lookback_bars=3,
        long_lookback_bars=5,
        rolling_lookback_bars=5,
        min_training_examples=3,
    )
    closes = [100.0 * (1.0 + 0.01 * math.sin(index / 2.0)) for index in range(24)]
    examples = _build_forecast_examples(_bars(closes), params=params)  # noqa: SLF001

    assert examples
    first = examples[0]["baseline_variance_forecasts"]
    assert first[BASELINE_PREVIOUS] > 0.0
    assert first[BASELINE_ROLLING] > 0.0
    assert first[BASELINE_EWMA] > 0.0

    model = _fit_har_rv_model(examples[:6], params=params)  # noqa: SLF001
    assert model is not None
    forecasts_one = _predict_model_variances(
        model, examples[6:9], params=params
    )  # noqa: SLF001
    forecasts_two = _predict_model_variances(
        model, examples[6:9], params=params
    )  # noqa: SLF001
    assert forecasts_one.variances == pytest.approx(forecasts_two.variances)
    assert all(
        math.isfinite(value) and value > 0.0 for value in forecasts_one.variances
    )


def test_har_rv_ols_recovers_linear_log_variance_relationship() -> None:
    params = MLRiskSignalResearchParams(min_training_examples=10)
    examples = []
    for index in range(60):
        features = [
            math.sin(index / 3.0),
            math.cos(index / 5.0),
            (index % 7) / 7.0,
        ]
        log_variance = -7.0 + 0.4 * features[0] - 0.2 * features[1] + 0.1 * features[2]
        examples.append(
            {
                "feature_vector": features,
                "label": {"realized_variance": math.exp(log_variance)},
            }
        )

    model = _fit_har_rv_model(examples, params=params)  # noqa: SLF001

    assert model is not None
    assert model.intercept == pytest.approx(-7.0)
    assert model.coefficients == pytest.approx((0.4, -0.2, 0.1))


def test_har_rv_ols_beats_constant_mean_on_autocorrelated_vol_fixture() -> None:
    params = MLRiskSignalResearchParams(min_training_examples=30)
    train = []
    test = []
    for index in range(180):
        latent = math.sin(index / 12.0) + 0.3 * math.sin(index / 3.0)
        features = [latent, 0.7 * latent, 0.4 * latent]
        log_variance = -7.0 + 0.6 * latent
        row = {
            "feature_vector": features,
            "label": {"realized_variance": math.exp(log_variance)},
        }
        (train if index < 120 else test).append(row)

    model = _fit_har_rv_model(train, params=params)  # noqa: SLF001
    assert model is not None
    model_forecasts = _predict_model_variances(
        model, test, params=params
    ).variances  # noqa: SLF001
    constant = math.exp(
        sum(math.log(row["label"]["realized_variance"]) for row in train) / len(train)
    )
    constant_forecasts = [constant for _ in test]
    actuals = [row["label"]["realized_variance"] for row in test]

    model_metrics = _forecast_metrics(  # noqa: SLF001
        actuals,
        model_forecasts,
        epsilon=params.epsilon_variance,
    )
    constant_metrics = _forecast_metrics(  # noqa: SLF001
        actuals,
        constant_forecasts,
        epsilon=params.epsilon_variance,
    )
    assert model_metrics["qlike"] < constant_metrics["qlike"]


def test_har_prediction_clips_extreme_log_variance() -> None:
    params = MLRiskSignalResearchParams()
    model = _HARLinearModel(  # noqa: SLF001
        intercept=1_000.0,
        coefficients=(0.0, 0.0, 0.0),
        feature_names=tuple(),
        training_examples=1,
    )
    result = _predict_model_variances(  # noqa: SLF001
        model,
        [{"feature_vector": [0.0, 0.0, 0.0]}],
        params=params,
    )

    assert result.clipped_high_count == 1
    assert result.clipped_low_count == 0
    assert math.isfinite(result.variances[0])
    assert result.variances[0] > 0.0


def test_overlapping_risk_examples_are_excluded_from_training() -> None:
    examples = [
        {"label": {"label_end_timestamp": 100}},
        {"label": {"label_end_timestamp": 200}},
    ]

    eligible = _examples_with_labels_before(  # noqa: SLF001
        examples,
        cutoff=datetime.fromtimestamp(150, tz=UTC),
    )

    assert eligible == [examples[0]]


def test_run_ml_risk_signal_research_sorts_filters_and_accumulates_evaluation_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fit_example_counts: list[int] = []

    def _fake_context(*args, **kwargs):  # noqa: ANN002, ANN003
        return {
            "windows": [
                {
                    "window_set": "overlap",
                    "window_id": "early",
                    "evidence_bucket": "uptrend",
                },
                {
                    "window_set": "overlap",
                    "window_id": "overlapping",
                    "evidence_bucket": "downtrend",
                },
                {
                    "window_set": "overlap",
                    "window_id": "final",
                    "evidence_bucket": "current_rolling",
                },
            ]
        }

    def _fake_load(*args, **kwargs):  # noqa: ANN002, ANN003
        start = kwargs["start"]
        return (
            [
                OHLCBar(
                    timestamp=int(start.timestamp()),
                    open=100.0,
                    high=100.0,
                    low=100.0,
                    close=100.0,
                    volume=1.0,
                )
            ],
            {"missing_series": [], "partial_series": []},
        )

    def _fake_examples(bars, *, params):  # noqa: ANN001
        start_ts = int(bars[0].timestamp)
        label_end_by_start = {
            100: 200,
            150: 300,
            250: 400,
        }
        return [
            {
                "timestamp": start_ts,
                "time": datetime.fromtimestamp(start_ts, tz=UTC).isoformat(),
                "feature_vector": [0.0, 0.0, 0.0],
                "label": {
                    "realized_variance": 1.0,
                    "label_end_timestamp": label_end_by_start[start_ts],
                },
                "baseline_variance_forecasts": {
                    BASELINE_PREVIOUS: 1.0,
                    BASELINE_ROLLING: 1.0,
                    BASELINE_EWMA: 1.0,
                },
            }
        ]

    def _fake_fit(examples, *, params):  # noqa: ANN001
        fit_example_counts.append(len(examples))
        if not examples:
            return None
        return _HARLinearModel(  # noqa: SLF001
            intercept=0.0,
            coefficients=(0.0, 0.0, 0.0),
            feature_names=tuple(),
            training_examples=len(examples),
        )

    monkeypatch.setattr(risk_research, "build_evidence_window_context", _fake_context)
    monkeypatch.setattr(risk_research, "_load_benchmark_bars", _fake_load)
    monkeypatch.setattr(risk_research, "_build_forecast_examples", _fake_examples)
    monkeypatch.setattr(risk_research, "_fit_har_rv_model", _fake_fit)

    result = risk_research.run_ml_risk_signal_research(
        SimpleNamespace(universe=SimpleNamespace(include_pairs=["BTC/USD"])),
        window_sets={
            "overlap": [
                ("final", "1970-01-01T00:04:10Z", "1970-01-01T00:05:00Z"),
                ("overlapping", "1970-01-01T00:02:30Z", "1970-01-01T00:03:20Z"),
                ("early", "1970-01-01T00:01:40Z", "1970-01-01T00:02:30Z"),
            ]
        },
        params=MLRiskSignalResearchParams(min_training_examples=1),
    )

    assert [window["window_id"] for window in result.windows] == [
        "early",
        "overlapping",
        "final",
    ]
    assert [window["training_examples_before"] for window in result.windows] == [
        0,
        1,
        2,
    ]
    assert [window["training_examples_used"] for window in result.windows] == [0, 0, 1]
    assert [
        window["training_examples_excluded_overlap"] for window in result.windows
    ] == [0, 1, 1]
    assert fit_example_counts == [0, 0, 1]
    assert result.summary["model_evaluation_observations"] == 1


def test_vol_forecast_metrics_floor_tiny_variance() -> None:
    assert _qlike_loss(0.0, 0.0, epsilon=1e-12) == pytest.approx(0.0)  # noqa: SLF001
    assert _log_vol_rmse([0.0], [0.0], epsilon=1e-12) == pytest.approx(
        0.0
    )  # noqa: SLF001


def test_ml_risk_signal_summary_separates_forecast_skill_from_rules() -> None:
    params = MLRiskSignalResearchParams()
    windows = [
        {"status": "ready", "strict_data_ready": True, "evidence_bucket": "uptrend"},
        {"status": "ready", "strict_data_ready": True, "evidence_bucket": "downtrend"},
        {
            "status": "ready",
            "strict_data_ready": True,
            "evidence_bucket": "chop_or_transition",
        },
        {
            "status": "ready",
            "strict_data_ready": True,
            "evidence_bucket": "current_rolling",
        },
    ]
    rows = []
    for bucket in ("uptrend", "downtrend", "chop_or_transition", "current_rolling"):
        for index in range(3):
            rows.append(
                {
                    "evidence_bucket": bucket,
                    "actual_variance": 1.0 + index * 0.01,
                    "model_variance": 1.0 + index * 0.01,
                    "baseline_variances": {
                        BASELINE_PREVIOUS: 1.5,
                        BASELINE_ROLLING: 1.4,
                        BASELINE_EWMA: 1.6,
                    },
                }
            )

    summary = _summary(  # noqa: SLF001
        windows,
        evaluation_rows=rows,
        params=params,
        timeframe="4h",
        benchmark_pair="BTC/USD",
    )

    assert summary["research_only"] is True
    assert summary["runtime_wiring_approved"] is False
    assert summary["forecast_skill"]["primary_metric"] == "qlike_variance_loss"
    assert summary["rule_performance"]["status"] == "deferred"
    assert (
        summary["pre_registered_outcomes"]["exposure_research_gate"]["passed"] is True
    )
    assert summary["forecast_verdict_readiness"]["status"] == "ready_for_verdict"
    assert summary["forecast_verdict_readiness"]["ready_for_exposure_verdict"] is True
    assert summary["lane_status"] == "continue_to_rule_research"


def test_ml_risk_signal_zero_observations_are_insufficient_data_not_kill() -> None:
    summary = _summary(  # noqa: SLF001
        [
            {
                "status": "insufficient_data",
                "strict_data_ready": False,
                "evidence_bucket": "insufficient_data",
                "window_set": "tiny",
                "window_id": "w1",
                "preflight": {
                    "missing_series": ["BTC/USD@4h"],
                    "partial_series": [],
                },
            }
        ],
        evaluation_rows=[],
        params=MLRiskSignalResearchParams(),
        timeframe="4h",
        benchmark_pair="BTC/USD",
    )

    readiness = summary["forecast_verdict_readiness"]
    assert readiness["status"] == "insufficient_data"
    assert readiness["ready_for_exposure_verdict"] is False
    assert "no_model_evaluation_observations" in readiness["blocking_reasons"]
    assert readiness["coverage_gaps"]["missing_series_by_window"] == [
        {
            "window_set": "tiny",
            "window_id": "w1",
            "missing_series": ["BTC/USD@4h"],
        }
    ]
    assert summary["lane_status"] == "insufficient_data"
    assert summary["pre_registered_outcomes"]["kill_criterion"]["triggered"] is False


def test_ml_risk_signal_zero_observations_from_training_is_not_kill() -> None:
    summary = _summary(  # noqa: SLF001
        [
            {
                "status": "insufficient_training",
                "strict_data_ready": True,
                "evidence_bucket": "uptrend",
                "preflight": {"missing_series": [], "partial_series": []},
            }
        ],
        evaluation_rows=[],
        params=MLRiskSignalResearchParams(),
        timeframe="4h",
        benchmark_pair="BTC/USD",
    )

    readiness = summary["forecast_verdict_readiness"]
    assert readiness["status"] == "insufficient_training"
    assert readiness["ready_for_exposure_verdict"] is False
    assert "insufficient_training_history" in readiness["blocking_reasons"]
    assert summary["lane_status"] == "insufficient_training"
    assert summary["pre_registered_outcomes"]["kill_criterion"]["triggered"] is False


def test_ml_risk_signal_inadequate_regime_coverage_is_not_kill() -> None:
    windows = [
        {"status": "ready", "strict_data_ready": True, "evidence_bucket": "uptrend"},
        {
            "status": "ready",
            "strict_data_ready": True,
            "evidence_bucket": "current_rolling",
        },
    ]
    rows = [
        _forecast_row("uptrend", model=1.6, ewma=1.1),
        _forecast_row("current_rolling", model=1.6, ewma=1.1),
    ]

    summary = _summary(  # noqa: SLF001
        windows,
        evaluation_rows=rows,
        params=MLRiskSignalResearchParams(),
        timeframe="4h",
        benchmark_pair="BTC/USD",
    )

    readiness = summary["forecast_verdict_readiness"]
    assert readiness["status"] == "insufficient_regime_coverage"
    assert readiness["ready_for_exposure_verdict"] is False
    assert (
        "fewer_than_2_evaluable_non_current_regime_buckets"
        in readiness["blocking_reasons"]
    )
    assert summary["lane_status"] == "insufficient_regime_coverage"
    assert summary["pre_registered_outcomes"]["kill_criterion"]["triggered"] is False


def test_ml_risk_signal_overlapping_non_current_windows_block_verdict() -> None:
    windows = [
        {
            "status": "ready",
            "strict_data_ready": True,
            "evidence_bucket": "uptrend",
            "window_id": "up",
            "first_example_time": "2026-05-01T00:00:00+00:00",
            "last_example_time": "2026-05-10T00:00:00+00:00",
        },
        {
            "status": "ready",
            "strict_data_ready": True,
            "evidence_bucket": "downtrend",
            "window_id": "down",
            "first_example_time": "2026-05-05T00:00:00+00:00",
            "last_example_time": "2026-05-14T00:00:00+00:00",
        },
        {
            "status": "ready",
            "strict_data_ready": True,
            "evidence_bucket": "current_rolling",
            "window_id": "current",
            "first_example_time": "2026-05-20T00:00:00+00:00",
            "last_example_time": "2026-05-29T00:00:00+00:00",
        },
    ]
    rows = [
        _forecast_row("uptrend"),
        _forecast_row("downtrend"),
        _forecast_row("current_rolling"),
    ]

    summary = _summary(  # noqa: SLF001
        windows,
        evaluation_rows=rows,
        params=MLRiskSignalResearchParams(),
        timeframe="4h",
        benchmark_pair="BTC/USD",
    )

    readiness = summary["forecast_verdict_readiness"]
    exposure_gate = summary["pre_registered_outcomes"]["exposure_research_gate"]
    assert readiness["status"] == "insufficient_independence"
    assert readiness["ready_for_exposure_verdict"] is False
    assert "overlapping_evaluation_windows" in readiness["blocking_reasons"]
    assert readiness["window_independence"]["status"] == "overlapping"
    assert len(readiness["window_independence"]["blocking_overlaps"]) == 1
    assert exposure_gate["readiness_passed"] is False
    assert exposure_gate["passed"] is False
    assert summary["lane_status"] == "insufficient_independence"
    assert summary["pre_registered_outcomes"]["kill_criterion"]["triggered"] is False


def test_ml_risk_signal_overlap_uses_label_inclusive_ranges() -> None:
    windows = [
        {
            "status": "ready",
            "strict_data_ready": True,
            "evidence_bucket": "uptrend",
            "window_id": "up",
            "first_example_time": "2026-05-01T00:00:00+00:00",
            "last_example_time": "2026-05-09T00:00:00+00:00",
            "last_label_end_time": "2026-05-10T00:00:00+00:00",
        },
        {
            "status": "ready",
            "strict_data_ready": True,
            "evidence_bucket": "downtrend",
            "window_id": "down",
            "first_example_time": "2026-05-07T00:00:00+00:00",
            "last_example_time": "2026-05-15T00:00:00+00:00",
            "last_label_end_time": "2026-05-16T00:00:00+00:00",
        },
        {
            "status": "ready",
            "strict_data_ready": True,
            "evidence_bucket": "current_rolling",
            "window_id": "current",
            "first_example_time": "2026-05-20T00:00:00+00:00",
            "last_example_time": "2026-05-29T00:00:00+00:00",
            "last_label_end_time": "2026-05-30T00:00:00+00:00",
        },
    ]
    rows = [
        _forecast_row("uptrend"),
        _forecast_row("downtrend"),
        _forecast_row("current_rolling"),
    ]

    summary = _summary(  # noqa: SLF001
        windows,
        evaluation_rows=rows,
        params=MLRiskSignalResearchParams(),
        timeframe="4h",
        benchmark_pair="BTC/USD",
    )

    independence = summary["forecast_verdict_readiness"]["window_independence"]
    blocking = independence["blocking_overlaps"][0]
    assert summary["forecast_verdict_readiness"]["status"] == (
        "insufficient_independence"
    )
    assert blocking["left_window_id"] == "up"
    assert blocking["right_window_id"] == "down"
    assert blocking["overlap_fraction_of_shorter_range"] == pytest.approx(1 / 3)


def test_ml_risk_signal_current_overlap_is_reported_but_not_blocking() -> None:
    windows = [
        {
            "status": "ready",
            "strict_data_ready": True,
            "evidence_bucket": "uptrend",
            "window_id": "up",
            "first_example_time": "2026-04-01T00:00:00+00:00",
            "last_example_time": "2026-04-10T00:00:00+00:00",
        },
        {
            "status": "ready",
            "strict_data_ready": True,
            "evidence_bucket": "downtrend",
            "window_id": "down",
            "first_example_time": "2026-05-01T00:00:00+00:00",
            "last_example_time": "2026-05-20T00:00:00+00:00",
        },
        {
            "status": "ready",
            "strict_data_ready": True,
            "evidence_bucket": "current_rolling",
            "window_id": "current",
            "first_example_time": "2026-05-10T00:00:00+00:00",
            "last_example_time": "2026-05-29T00:00:00+00:00",
        },
    ]
    rows = [
        _forecast_row("uptrend"),
        _forecast_row("downtrend"),
        _forecast_row("current_rolling"),
    ]

    summary = _summary(  # noqa: SLF001
        windows,
        evaluation_rows=rows,
        params=MLRiskSignalResearchParams(),
        timeframe="4h",
        benchmark_pair="BTC/USD",
    )

    independence = summary["forecast_verdict_readiness"]["window_independence"]
    assert summary["forecast_verdict_readiness"]["status"] == "ready_for_verdict"
    assert independence["status"] == "ready"
    assert len(independence["scored_example_range_overlaps"]) == 1
    assert independence["scored_example_range_overlaps"][0]["blocks_verdict"] is False
    assert independence["blocking_overlaps"] == []
    assert (
        summary["pre_registered_outcomes"]["exposure_research_gate"]["passed"] is True
    )


def test_ml_risk_signal_display_candidate_is_blocked_by_regime_readiness_gap() -> None:
    windows = [
        {"status": "ready", "strict_data_ready": True, "evidence_bucket": "uptrend"},
        {
            "status": "ready",
            "strict_data_ready": True,
            "evidence_bucket": "current_rolling",
        },
    ]
    rows = [
        _forecast_row("uptrend", model=1.04, ewma=1.08),
        _forecast_row("current_rolling", model=1.04, ewma=1.08),
    ]

    summary = _summary(  # noqa: SLF001
        windows,
        evaluation_rows=rows,
        params=MLRiskSignalResearchParams(),
        timeframe="4h",
        benchmark_pair="BTC/USD",
    )

    assert summary["forecast_verdict_readiness"]["status"] == (
        "insufficient_regime_coverage"
    )
    display_gate = summary["pre_registered_outcomes"]["display_only_gate"]
    assert display_gate["metric_passed"] is True
    assert display_gate["readiness_passed"] is False
    assert display_gate["passed"] is False
    assert summary["lane_status"] == "insufficient_regime_coverage"
    assert summary["pre_registered_outcomes"]["kill_criterion"]["triggered"] is False


def test_ml_risk_signal_display_candidate_is_blocked_by_partial_data() -> None:
    windows = [
        {
            "status": "ready",
            "strict_data_ready": False,
            "evidence_bucket": "uptrend",
            "preflight": {"missing_series": [], "partial_series": ["BTC/USD@4h"]},
        },
        {
            "status": "ready",
            "strict_data_ready": False,
            "evidence_bucket": "current_rolling",
            "preflight": {"missing_series": [], "partial_series": ["BTC/USD@4h"]},
        },
    ]
    rows = [
        _forecast_row("uptrend", model=1.04, ewma=1.08),
        _forecast_row("current_rolling", model=1.04, ewma=1.08),
    ]

    summary = _summary(  # noqa: SLF001
        windows,
        evaluation_rows=rows,
        params=MLRiskSignalResearchParams(),
        timeframe="4h",
        benchmark_pair="BTC/USD",
    )

    readiness = summary["forecast_verdict_readiness"]
    display_gate = summary["pre_registered_outcomes"]["display_only_gate"]
    assert readiness["status"] == "insufficient_data"
    assert "strict_data_not_ready" in readiness["blocking_reasons"]
    assert display_gate["metric_passed"] is True
    assert display_gate["readiness_passed"] is False
    assert display_gate["passed"] is False
    assert summary["lane_status"] == "insufficient_data"
    assert summary["pre_registered_outcomes"]["kill_criterion"]["triggered"] is False


def test_ml_risk_signal_ready_display_candidate_survives_failed_exposure_gate() -> None:
    windows = [
        {"status": "ready", "strict_data_ready": True, "evidence_bucket": "uptrend"},
        {"status": "ready", "strict_data_ready": True, "evidence_bucket": "downtrend"},
        {
            "status": "ready",
            "strict_data_ready": True,
            "evidence_bucket": "current_rolling",
        },
    ]
    rows = [
        _forecast_row("uptrend", model=1.08, ewma=1.08),
        _forecast_row("downtrend", model=1.08, ewma=1.08),
        _forecast_row("current_rolling", model=1.08, ewma=1.08),
    ]

    summary = _summary(  # noqa: SLF001
        windows,
        evaluation_rows=rows,
        params=MLRiskSignalResearchParams(),
        timeframe="4h",
        benchmark_pair="BTC/USD",
    )

    display_gate = summary["pre_registered_outcomes"]["display_only_gate"]
    assert summary["forecast_verdict_readiness"]["status"] == "ready_for_verdict"
    assert (
        summary["pre_registered_outcomes"]["exposure_research_gate"]["passed"] is False
    )
    assert display_gate["metric_passed"] is True
    assert display_gate["readiness_passed"] is True
    assert display_gate["passed"] is True
    assert summary["lane_status"] == "display_only_candidate"
    assert summary["pre_registered_outcomes"]["kill_criterion"]["triggered"] is False


def test_ml_risk_signal_adequate_losing_model_closes_lane() -> None:
    windows = [
        {"status": "ready", "strict_data_ready": True, "evidence_bucket": "uptrend"},
        {"status": "ready", "strict_data_ready": True, "evidence_bucket": "downtrend"},
        {
            "status": "ready",
            "strict_data_ready": True,
            "evidence_bucket": "current_rolling",
        },
    ]
    rows = [
        _forecast_row("uptrend", model=1.6, ewma=1.1),
        _forecast_row("downtrend", model=1.6, ewma=1.1),
        _forecast_row("current_rolling", model=1.6, ewma=1.1),
    ]

    summary = _summary(  # noqa: SLF001
        windows,
        evaluation_rows=rows,
        params=MLRiskSignalResearchParams(),
        timeframe="4h",
        benchmark_pair="BTC/USD",
    )

    assert summary["forecast_verdict_readiness"]["status"] == "ready_for_verdict"
    assert summary["lane_status"] == "close_volatility_forecast_lane"
    assert summary["pre_registered_outcomes"]["kill_criterion"]["triggered"] is True
