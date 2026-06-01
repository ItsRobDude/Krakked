from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from krakked.backtest.ml_walk_forward import (
    MLWalkForwardFold,
    MLWalkForwardPrediction,
    MLWalkForwardSummary,
    PROMOTION_TIER_BLOCKED,
    PROMOTION_TIER_RESEARCH,
    PROMOTION_TIER_RISK_OVERLAY,
    PROMOTION_TIER_SELF_STANDING,
    _assess_promotability,
    _build_feature_diagnostics,
    _build_diagnostic_warnings,
    _build_prediction_metrics,
    _build_regression_calibration,
    _build_walk_forward_folds,
    _score_intent,
    _set_strategy_learning,
    run_ml_walk_forward,
)
from krakked.config import AppConfig, StrategyConfig, load_config
from krakked.market_data.metadata_store import PairMetadataStore
from krakked.market_data.models import PairMetadata
from krakked.strategy.models import StrategyIntent
from krakked.strategy.features import (
    ML_FEATURE_CLIPPING_VERSION,
    ML_FEATURE_NAMES,
    ML_FEATURE_SCHEMA_VERSION,
    feature_names_for_profile,
)
from krakked.strategy.strategies.ml_alt_strategy import AIPredictorAltStrategy
from krakked.strategy.strategies.ml_regression_strategy import AIRegressionStrategy
from krakked.strategy.strategies.ml_strategy import AIPredictorStrategy


def _build_ml_config(tmp_path: Path) -> AppConfig:
    config = load_config(config_path=Path("config_examples/config.yaml"), env="paper")
    config.market_data.ohlc_store = {"root_dir": str(tmp_path / "ohlc")}
    config.market_data.metadata_path = str(tmp_path / "pair_metadata.json")
    config.universe.include_pairs = ["BTC/USD"]
    config.market_data.backfill_timeframes = ["1h"]
    config.risk.max_per_strategy_pct["ai_regression"] = 5.0
    config.strategies.configs["ai_regression"].params = {
        "pairs": ["BTC/USD"],
        "timeframe": "1h",
        "lookback_bars": 5,
        "short_window": 2,
        "long_window": 5,
        "continuous_learning": True,
        "min_edge_pct": 0.001,
        "target_exposure_usd": 100.0,
        "max_positions": 1,
    }
    return config


def _seed_pair_metadata(config: AppConfig) -> None:
    assert config.market_data.metadata_path is not None
    PairMetadataStore(Path(config.market_data.metadata_path)).save(
        [
            PairMetadata(
                canonical="XBTUSD",
                base="XXBT",
                quote="USD",
                rest_symbol="XBT/USD",
                ws_symbol="BTC/USD",
                raw_name="XBTUSD",
                price_decimals=2,
                volume_decimals=8,
                lot_size=1.0,
                min_order_size=0.0001,
                status="online",
                liquidity_24h_usd=1_000_000.0,
            )
        ]
    )


def _write_ohlc_series(
    tmp_path: Path, timestamps: list[int], closes: list[float]
) -> None:
    bars_path = tmp_path / "ohlc" / "1h"
    bars_path.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(
        [
            {
                "timestamp": ts,
                "open": close,
                "high": close * 1.01,
                "low": close * 0.99,
                "close": close,
                "volume": 1000.0,
            }
            for ts, close in zip(timestamps, closes)
        ]
    ).set_index("timestamp")
    frame.to_parquet(bars_path / "XBTUSD.parquet")


def _configure_classifier_strategy(config: AppConfig, strategy_id: str, type_: str) -> None:
    config.strategies.configs[strategy_id] = StrategyConfig(
        name=strategy_id,
        type=type_,
        enabled=True,
        params={
            "pairs": ["BTC/USD"],
            "timeframe": "1h",
            "lookback_bars": 5,
            "short_window": 2,
            "long_window": 5,
            "continuous_learning": True,
            "target_exposure_usd": 100.0,
            "max_positions": 1,
        },
    )
    config.risk.max_per_strategy_pct[strategy_id] = 5.0


def _assert_fold_examples_before_test_start(
    result: Any, base_db_path: Path, stem: str
) -> None:
    for fold in result.summary.folds[:2]:
        fold_path = base_db_path.with_name(f"{stem}.fold-{fold.fold_index:03d}.db")
        with sqlite3.connect(fold_path) as conn:
            rows = conn.execute(
                "SELECT created_at FROM ml_training_examples ORDER BY created_at"
            ).fetchall()
        assert rows
        created_at_values = [datetime.fromisoformat(row[0]) for row in rows]
        assert max(created_at_values) < fold.test_start


def test_build_walk_forward_folds_rolls_by_test_window() -> None:
    timestamps = list(range(10))

    folds = _build_walk_forward_folds(timestamps, train_bars=4, test_bars=2)

    assert folds == [
        ([0, 1, 2, 3], [4, 5]),
        ([2, 3, 4, 5], [6, 7]),
        ([4, 5, 6, 7], [8, 9]),
    ]


def test_run_ml_walk_forward_scores_out_of_sample_predictions(tmp_path: Path) -> None:
    config = _build_ml_config(tmp_path)
    _seed_pair_metadata(config)
    timestamps = [1_700_000_000 + idx * 3600 for idx in range(48)]
    closes = [100.0 + idx * 0.4 for idx in range(48)]
    _write_ohlc_series(tmp_path, timestamps, closes)

    result = run_ml_walk_forward(
        config,
        start=datetime.fromtimestamp(timestamps[0], tz=UTC),
        end=datetime.fromtimestamp(timestamps[-1], tz=UTC),
        strategy_id="ai_regression",
        timeframe="1h",
        train_bars=12,
        test_bars=6,
        fee_bps=25.0,
        strict_data=True,
    )

    report = result.to_report_dict()
    summary = report["summary"]

    assert report["report_version"] == 8
    assert report["provenance"]["generated_by"] == "krakked ml-walk-forward"
    assert summary["strategy_id"] == "ai_regression"
    assert summary["strategy_type"] == "machine_learning_regression"
    assert summary["timeframe"] == "1h"
    assert summary["evaluation_mode"] == "rolling_window_isolated"
    assert summary["edge_scoring_mode"] == "intent_hurdle_aligned"
    assert summary["model_semantics"]["model_family"] == "regression"
    assert summary["model_semantics"]["training_target"] == "signed_return_delta"
    assert summary["model_semantics"]["prediction_target"] == "signed_return_delta"
    assert summary["model_semantics"]["feature_schema"] == ML_FEATURE_SCHEMA_VERSION
    assert (
        summary["cost_semantics"]["evaluation_hurdle_source"]
        == "effective_min_edge_pct"
    )
    assert summary["cost_semantics"]["edge_cost_multipliers"] == [1.0]
    assert summary["model_state_reused_across_folds"] is False
    assert summary["fold_count"] >= 1
    assert summary["metrics"]["prediction_count"] > 0
    assert "directional_accuracy" in summary["metrics"]
    assert summary["metrics"]["edge_prediction_accuracy"] is not None
    assert "positive_edge_prediction_count" in summary["metrics"]
    assert isinstance(summary["diagnostic_warnings"], list)
    assert summary["round_trip_cost_bps"] == pytest.approx(150.0)
    assert summary["confidence_buckets"]
    assert summary["regression_calibration"]["prediction_count"] > 0
    assert summary["regression_calibration"]["threshold_sweeps"]
    assert summary["regression_calibration"]["predicted_delta_deciles"]
    assert summary["folds"][0]["prediction_count"] > 0
    assert summary["folds"][0]["confidence_buckets"]
    assert summary["folds"][0]["regression_calibration"]["threshold_sweeps"]
    assert summary["folds"][0]["baselines"]["cash"]["return_pct"] == pytest.approx(
        0.0
    )
    assert "buy_hold_by_pair" in summary["folds"][0]["baselines"]
    assert summary["baselines"]["cash"]["avg_return_pct"] == pytest.approx(0.0)
    assert "buy_hold_equal_weight" in summary["baselines"]
    btc_baseline = summary["baselines"]["buy_hold_by_pair"]["BTC/USD"]
    assert btc_baseline["avg_return_pct"] is not None
    assert btc_baseline["avg_max_drawdown_pct"] is not None
    assert summary["baselines"]["buy_hold_equal_weight"][
        "avg_return_pct"
    ] == pytest.approx(btc_baseline["avg_return_pct"])
    fold_diagnostics = summary["folds"][0]["diagnostics"]
    assert fold_diagnostics["models"]
    assert "coef" in fold_diagnostics["models"][0]
    assert "intercept" in fold_diagnostics["models"][0]
    assert "n_iter" in fold_diagnostics["models"][0]
    assert fold_diagnostics["models"][0]["scaler_schema_version"] == "standard_v1"
    assert fold_diagnostics["models"][0]["scaler_initialized"] is True
    assert "predicted_delta_quantiles" in fold_diagnostics["predictions"]
    assert fold_diagnostics["features"]["schema_version"] == ML_FEATURE_SCHEMA_VERSION
    assert fold_diagnostics["features"]["prediction_count"] > 0
    assert set(fold_diagnostics["features"]["raw_feature_quantiles"]) == set(
        ML_FEATURE_NAMES
    )
    assert fold_diagnostics["features"]["scaled_available"] is True
    assert set(fold_diagnostics["features"]["scaled_feature_quantiles"]) == set(
        ML_FEATURE_NAMES
    )
    assert "realized_return_quantiles" in fold_diagnostics["outcomes"]
    assert "above_evaluation_hurdle" in fold_diagnostics["outcomes"]
    scored_learning_flags = [
        prediction.metadata.get("learning_enabled")
        for fold in result.summary.folds
        for prediction in fold.predictions
        if "learning_enabled" in prediction.metadata
    ]
    assert scored_learning_flags
    assert all(flag is False for flag in scored_learning_flags)


def test_run_ml_walk_forward_uses_isolated_fold_databases(
    tmp_path: Path,
) -> None:
    config = _build_ml_config(tmp_path)
    _seed_pair_metadata(config)
    timestamps = [1_700_000_000 + idx * 3600 for idx in range(42)]
    closes = [100.0 + idx * 0.5 for idx in range(42)]
    _write_ohlc_series(tmp_path, timestamps, closes)
    base_db_path = tmp_path / "reports" / "ml-walk-forward.db"

    result = run_ml_walk_forward(
        config,
        start=datetime.fromtimestamp(timestamps[0], tz=UTC),
        end=datetime.fromtimestamp(timestamps[-1], tz=UTC),
        strategy_id="ai_regression",
        timeframe="1h",
        train_bars=12,
        test_bars=6,
        fee_bps=25.0,
        db_path=str(base_db_path),
        strict_data=True,
    )

    assert result.summary.to_dict()["evaluation_mode"] == "rolling_window_isolated"
    assert not base_db_path.exists()
    fold_paths = [
        base_db_path.with_name("ml-walk-forward.fold-001.db"),
        base_db_path.with_name("ml-walk-forward.fold-002.db"),
    ]
    for fold_path in fold_paths:
        assert fold_path.exists()
        with sqlite3.connect(fold_path) as conn:
            rows = conn.execute(
                "SELECT created_at FROM ml_training_examples ORDER BY created_at"
            ).fetchall()
        assert rows
        count = len(rows)
        assert 0 < count <= 12
        fold_index = int(fold_path.stem.rsplit("-", 1)[-1])
        fold = result.summary.folds[fold_index - 1]
        created_at_values = [datetime.fromisoformat(row[0]) for row in rows]
        assert max(created_at_values) < fold.test_start


def test_ml_walk_forward_alt_does_not_record_test_examples_when_frozen(
    tmp_path: Path,
) -> None:
    config = _build_ml_config(tmp_path)
    _configure_classifier_strategy(config, "ai_predictor_alt", "machine_learning_alt")
    _seed_pair_metadata(config)
    timestamps = [1_700_000_000 + idx * 3600 for idx in range(42)]
    closes = [100.0 + idx * 0.5 for idx in range(42)]
    _write_ohlc_series(tmp_path, timestamps, closes)
    base_db_path = tmp_path / "reports" / "ml-alt-walk-forward.db"

    result = run_ml_walk_forward(
        config,
        start=datetime.fromtimestamp(timestamps[0], tz=UTC),
        end=datetime.fromtimestamp(timestamps[-1], tz=UTC),
        strategy_id="ai_predictor_alt",
        timeframe="1h",
        train_bars=12,
        test_bars=6,
        fee_bps=25.0,
        db_path=str(base_db_path),
        strict_data=True,
    )

    _assert_fold_examples_before_test_start(result, base_db_path, "ml-alt-walk-forward")


@pytest.mark.parametrize(
    ("strategy_id", "type_"),
    [
        ("ai_predictor", "machine_learning"),
        ("ai_regression", "machine_learning_regression"),
    ],
)
def test_ml_walk_forward_does_not_record_test_examples_when_frozen(
    tmp_path: Path, strategy_id: str, type_: str
) -> None:
    config = _build_ml_config(tmp_path)
    if strategy_id == "ai_predictor":
        _configure_classifier_strategy(config, strategy_id, type_)
    _seed_pair_metadata(config)
    timestamps = [1_700_000_000 + idx * 3600 for idx in range(42)]
    closes = [100.0 + idx * 0.5 for idx in range(42)]
    _write_ohlc_series(tmp_path, timestamps, closes)
    base_db_path = tmp_path / "reports" / f"{strategy_id}-walk-forward.db"

    result = run_ml_walk_forward(
        config,
        start=datetime.fromtimestamp(timestamps[0], tz=UTC),
        end=datetime.fromtimestamp(timestamps[-1], tz=UTC),
        strategy_id=strategy_id,
        timeframe="1h",
        train_bars=12,
        test_bars=6,
        fee_bps=25.0,
        db_path=str(base_db_path),
        strict_data=True,
    )

    _assert_fold_examples_before_test_start(
        result, base_db_path, f"{strategy_id}-walk-forward"
    )


def test_ml_walk_forward_fee_bps_controls_regression_edge_metadata(
    tmp_path: Path,
) -> None:
    config = _build_ml_config(tmp_path)
    _seed_pair_metadata(config)
    timestamps = [1_700_000_000 + idx * 3600 for idx in range(30)]
    closes = [100.0 + idx * 0.4 for idx in range(30)]
    _write_ohlc_series(tmp_path, timestamps, closes)

    result = run_ml_walk_forward(
        config,
        start=datetime.fromtimestamp(timestamps[0], tz=UTC),
        end=datetime.fromtimestamp(timestamps[-1], tz=UTC),
        strategy_id="ai_regression",
        timeframe="1h",
        train_bars=12,
        test_bars=6,
        fee_bps=75.0,
        strict_data=True,
    )

    prediction = result.summary.folds[0].predictions[0]
    assert prediction.metadata["edge_fee_bps"] == pytest.approx(75.0)
    assert prediction.metadata["round_trip_cost_pct"] == pytest.approx(0.025)
    assert prediction.metadata["effective_min_edge_pct"] == pytest.approx(0.025)
    assert prediction.metadata["confidence_source"] == "predicted_delta_magnitude"
    assert prediction.metadata["prediction_target"] == "signed_return_delta"
    assert "predicted_positive_edge" in prediction.metadata
    assert prediction.evaluation_hurdle_pct == pytest.approx(0.025)
    assert prediction.evaluation_hurdle_source == "effective_min_edge_pct"


def test_ml_walk_forward_slippage_bps_controls_regression_edge_metadata(
    tmp_path: Path,
) -> None:
    config = _build_ml_config(tmp_path)
    _seed_pair_metadata(config)
    timestamps = [1_700_000_000 + idx * 3600 for idx in range(30)]
    closes = [100.0 + idx * 0.4 for idx in range(30)]
    _write_ohlc_series(tmp_path, timestamps, closes)

    result = run_ml_walk_forward(
        config,
        start=datetime.fromtimestamp(timestamps[0], tz=UTC),
        end=datetime.fromtimestamp(timestamps[-1], tz=UTC),
        strategy_id="ai_regression",
        timeframe="1h",
        train_bars=12,
        test_bars=6,
        fee_bps=10.0,
        slippage_bps=20.0,
        strict_data=True,
    )

    summary = result.to_report_dict()["summary"]
    prediction = result.summary.folds[0].predictions[0]
    assert summary["slippage_bps"] == pytest.approx(20.0)
    assert summary["round_trip_cost_bps"] == pytest.approx(60.0)
    assert prediction.metadata["edge_fee_bps"] == pytest.approx(10.0)
    assert prediction.metadata["edge_slippage_bps"] == pytest.approx(20.0)
    assert prediction.metadata["round_trip_cost_pct"] == pytest.approx(0.006)
    assert prediction.evaluation_hurdle_pct == pytest.approx(0.006)


@pytest.mark.parametrize(
    ("strategy_id", "strategy_cls"),
    [
        ("ai_predictor", AIPredictorStrategy),
        ("ai_predictor_alt", AIPredictorAltStrategy),
        ("ai_regression", AIRegressionStrategy),
    ],
)
def test_set_strategy_learning_updates_instantiated_strategy_config_reference(
    tmp_path: Path, strategy_id: str, strategy_cls: Any
) -> None:
    config = _build_ml_config(tmp_path)
    if strategy_id == "ai_predictor":
        config.strategies.configs[strategy_id] = StrategyConfig(
            name=strategy_id,
            type="machine_learning",
            enabled=True,
            params={
                "pairs": ["BTC/USD"],
                "timeframe": "1h",
                "lookback_bars": 60,
                "continuous_learning": True,
            },
        )
    strategy = strategy_cls(config.strategies.configs[strategy_id])

    assert strategy._learning_enabled() is True  # noqa: SLF001

    _set_strategy_learning(config, strategy_id, False)

    assert strategy._learning_enabled() is False  # noqa: SLF001


def test_ml_walk_forward_scores_classifier_no_edge_without_fake_down_call() -> None:
    now = datetime.fromtimestamp(1_700_000_000, tz=UTC)
    current_bar = type("Bar", (), {"close": 100.0})()
    future_bar = type("Bar", (), {"close": 101.0})()
    market_data: Any = type(
        "FakeMarketData",
        (),
        {
            "get_bar_at_or_before": lambda self, pair, timeframe, ts: current_bar,
            "get_bar_at_or_after": lambda self, pair, timeframe, ts: future_bar,
            "get_display_pair": lambda self, pair: pair,
        },
    )()
    intent = StrategyIntent(
        strategy_id="ai_predictor",
        pair="BTC/USD",
        side="flat",
        intent_type="exit",
        desired_exposure_usd=0.0,
        confidence=0.7,
        timeframe="1h",
        generated_at=now,
        metadata={
            "prediction": "no_positive_edge",
            "prediction_target": "fee_adjusted_positive_edge",
            "predicted_positive_edge": False,
        },
    )

    prediction = _score_intent(
        fold_index=1,
        intent=intent,
        market_data=market_data,
        generated_at=now,
        fee_bps=25.0,
        slippage_bps=50.0,
    )

    assert prediction is not None
    assert prediction.predicted_direction is None
    assert prediction.predicted_positive_edge is False
    assert prediction.directional_correct is None
    assert prediction.evaluation_hurdle_correct is True
    assert prediction.fee_adjusted_correct is True
    assert prediction.to_dict()["evaluation_hurdle_correct"] is True
    assert prediction.to_dict()["fee_adjusted_correct"] is True
    metrics = _build_prediction_metrics([prediction])
    assert metrics["directional_accuracy"] is None
    assert metrics["edge_prediction_accuracy"] == pytest.approx(1.0)


def test_ml_walk_forward_scores_classifier_against_label_hurdle() -> None:
    now = datetime.fromtimestamp(1_700_000_000, tz=UTC)
    current_bar = type("Bar", (), {"close": 100.0})()
    future_bar = type("Bar", (), {"close": 102.0})()
    market_data: Any = type(
        "FakeMarketData",
        (),
        {
            "get_bar_at_or_before": lambda self, pair, timeframe, ts: current_bar,
            "get_bar_at_or_after": lambda self, pair, timeframe, ts: future_bar,
            "get_display_pair": lambda self, pair: pair,
        },
    )()
    intent = StrategyIntent(
        strategy_id="ai_predictor",
        pair="BTC/USD",
        side="long",
        intent_type="enter",
        desired_exposure_usd=100.0,
        confidence=0.8,
        timeframe="1h",
        generated_at=now,
        metadata={
            "prediction": "positive_edge",
            "prediction_target": "fee_adjusted_positive_edge",
            "predicted_positive_edge": True,
            "label": {"label_hurdle_bps": 300.0},
        },
    )

    prediction = _score_intent(
        fold_index=1,
        intent=intent,
        market_data=market_data,
        generated_at=now,
        fee_bps=25.0,
        slippage_bps=50.0,
    )

    assert prediction is not None
    assert prediction.realized_return == pytest.approx(0.02)
    assert prediction.round_trip_cost_pct == pytest.approx(0.015)
    assert prediction.evaluation_hurdle_pct == pytest.approx(0.03)
    assert prediction.evaluation_hurdle_source == "label_hurdle_bps"
    assert prediction.fee_adjusted_correct is False
    assert _build_prediction_metrics([prediction])["precision_long"] == pytest.approx(
        0.0
    )


def test_classifier_summary_reports_fee_adjusted_label_semantics() -> None:
    now = datetime.fromtimestamp(1_700_000_000, tz=UTC)
    prediction = MLWalkForwardPrediction(
        fold_index=1,
        generated_at=now,
        strategy_id="ai_predictor",
        pair="BTC/USD",
        timeframe="1h",
        side="long",
        intent_type="enter",
        confidence=0.8,
        prediction_target="fee_adjusted_positive_edge",
        predicted_positive_edge=True,
        predicted_direction=None,
        current_close=100.0,
        future_close=104.0,
        realized_return=0.04,
        round_trip_cost_pct=0.015,
        evaluation_hurdle_pct=0.03,
        evaluation_hurdle_source="label_hurdle_bps",
        directional_correct=None,
        evaluation_hurdle_correct=True,
        metadata={
            "feature_schema_version": ML_FEATURE_SCHEMA_VERSION,
            "label": {
                "label_cost_multiplier": 2.0,
                "label_hurdle_bps": 300.0,
            },
        },
    )
    summary = MLWalkForwardSummary(
        start=now,
        end=now,
        strategy_id="ai_predictor",
        strategy_type="machine_learning",
        timeframe="1h",
        train_bars=3,
        test_bars=3,
        folds=[
            MLWalkForwardFold(
                fold_index=1,
                train_start=now,
                train_end=now,
                test_start=now,
                test_end=now,
                train_cycles=3,
                test_cycles=3,
                predictions=[prediction],
            )
        ],
        fee_bps=25.0,
        slippage_bps=50.0,
        pairs=["BTC/USD"],
        coverage_status="ready",
        warnings=[],
    )

    payload = summary.to_dict()

    assert payload["model_semantics"]["model_family"] == "classifier"
    assert (
        payload["model_semantics"]["training_target"]
        == "fee_adjusted_classification"
    )
    assert (
        payload["model_semantics"]["prediction_target"]
        == "fee_adjusted_positive_edge"
    )
    assert payload["cost_semantics"]["label_cost_multipliers"] == [2.0]
    assert payload["cost_semantics"]["evaluation_hurdle_source"] == "label_hurdle_bps"
    assert payload["cost_semantics"]["evaluation_hurdle_pct"] == pytest.approx(0.03)


def test_ml_walk_forward_scores_regression_against_effective_min_edge() -> None:
    now = datetime.fromtimestamp(1_700_000_000, tz=UTC)
    current_bar = type("Bar", (), {"close": 100.0})()
    future_bar = type("Bar", (), {"close": 102.0})()
    market_data: Any = type(
        "FakeMarketData",
        (),
        {
            "get_bar_at_or_before": lambda self, pair, timeframe, ts: current_bar,
            "get_bar_at_or_after": lambda self, pair, timeframe, ts: future_bar,
            "get_display_pair": lambda self, pair: pair,
        },
    )()
    intent = StrategyIntent(
        strategy_id="ai_regression",
        pair="BTC/USD",
        side="long",
        intent_type="enter",
        desired_exposure_usd=100.0,
        confidence=0.8,
        timeframe="1h",
        generated_at=now,
        metadata={
            "predicted_delta": 0.04,
            "prediction_target": "signed_return_delta",
            "predicted_positive_edge": True,
            "effective_min_edge_pct": 0.025,
        },
    )

    prediction = _score_intent(
        fold_index=1,
        intent=intent,
        market_data=market_data,
        generated_at=now,
        fee_bps=25.0,
        slippage_bps=50.0,
    )

    assert prediction is not None
    assert prediction.evaluation_hurdle_pct == pytest.approx(0.025)
    assert prediction.evaluation_hurdle_source == "effective_min_edge_pct"
    assert prediction.fee_adjusted_correct is False


def test_ml_walk_forward_falls_back_to_round_trip_hurdle() -> None:
    now = datetime.fromtimestamp(1_700_000_000, tz=UTC)
    current_bar = type("Bar", (), {"close": 100.0})()
    future_bar = type("Bar", (), {"close": 102.0})()
    market_data: Any = type(
        "FakeMarketData",
        (),
        {
            "get_bar_at_or_before": lambda self, pair, timeframe, ts: current_bar,
            "get_bar_at_or_after": lambda self, pair, timeframe, ts: future_bar,
            "get_display_pair": lambda self, pair: pair,
        },
    )()
    intent = StrategyIntent(
        strategy_id="legacy_ml",
        pair="BTC/USD",
        side="long",
        intent_type="enter",
        desired_exposure_usd=100.0,
        confidence=0.8,
        timeframe="1h",
        generated_at=now,
        metadata={"prediction": "up"},
    )

    prediction = _score_intent(
        fold_index=1,
        intent=intent,
        market_data=market_data,
        generated_at=now,
        fee_bps=25.0,
        slippage_bps=50.0,
    )

    assert prediction is not None
    assert prediction.round_trip_cost_pct == pytest.approx(0.015)
    assert prediction.evaluation_hurdle_pct == pytest.approx(
        prediction.round_trip_cost_pct
    )
    assert prediction.evaluation_hurdle_source == "round_trip_cost_pct"
    assert prediction.fee_adjusted_correct is True


def _regression_prediction(
    *,
    predicted_delta: float,
    realized_return: float,
    evaluation_hurdle_pct: float = 0.005,
    fold_index: int = 1,
) -> MLWalkForwardPrediction:
    return MLWalkForwardPrediction(
        fold_index=fold_index,
        generated_at=datetime.fromtimestamp(1_700_000_000, tz=UTC),
        strategy_id="ai_regression",
        pair="BTC/USD",
        timeframe="1h",
        side="long" if predicted_delta > evaluation_hurdle_pct else "flat",
        intent_type="enter",
        confidence=0.5,
        prediction_target="signed_return_delta",
        predicted_positive_edge=predicted_delta > evaluation_hurdle_pct,
        predicted_direction="up" if predicted_delta > 0 else "down",
        current_close=100.0,
        future_close=100.0 * (1.0 + realized_return),
        realized_return=realized_return,
        round_trip_cost_pct=evaluation_hurdle_pct,
        evaluation_hurdle_pct=evaluation_hurdle_pct,
        evaluation_hurdle_source="effective_min_edge_pct",
        directional_correct=(predicted_delta > 0) == (realized_return > 0),
        evaluation_hurdle_correct=(
            (predicted_delta > evaluation_hurdle_pct)
            == (realized_return > evaluation_hurdle_pct)
        ),
        metadata={"predicted_delta": predicted_delta},
    )


def test_regression_calibration_reports_threshold_lift_and_deciles() -> None:
    predictions = [
        _regression_prediction(predicted_delta=-0.004, realized_return=-0.002),
        _regression_prediction(predicted_delta=0.001, realized_return=0.000),
        _regression_prediction(predicted_delta=0.004, realized_return=0.002),
        _regression_prediction(predicted_delta=0.006, realized_return=0.006),
        _regression_prediction(predicted_delta=0.010, realized_return=0.012),
        _regression_prediction(predicted_delta=0.020, realized_return=0.018),
    ]

    calibration = _build_regression_calibration(predictions)

    assert calibration["prediction_count"] == 6
    fixed_0p005 = next(
        row for row in calibration["threshold_sweeps"] if row["name"] == "fixed_0p005"
    )
    assert fixed_0p005["predicted_long_count"] == 3
    assert fixed_0p005["true_positive_count"] == 3
    assert fixed_0p005["precision"] == pytest.approx(1.0)
    assert fixed_0p005["recall"] == pytest.approx(1.0)
    assert fixed_0p005["lift_over_base_rate"] == pytest.approx(2.0)
    assert calibration["predicted_delta_deciles"]
    # Six predictions is below the monotonicity sample-size guard, so the
    # value should be the sentinel string instead of a boolean.
    assert calibration["monotonicity"]["upper_half_improves"] == "insufficient_data"
    assert "insufficient_data_reasons" in calibration["monotonicity"]


def test_regression_calibration_monotonicity_with_sufficient_samples() -> None:
    # Build 80 monotonic predictions so each decile half clears the row guard.
    predictions = []
    for index in range(80):
        predicted_delta = -0.02 + (0.06 * index / 79)
        realized_return = predicted_delta + (0.001 if index % 2 == 0 else -0.001)
        predictions.append(
            _regression_prediction(
                predicted_delta=predicted_delta,
                realized_return=realized_return,
            )
        )

    calibration = _build_regression_calibration(predictions)
    monotonicity = calibration["monotonicity"]

    assert monotonicity["upper_half_improves"] is True
    assert monotonicity["total_decile_rows"] == 80
    assert monotonicity["lower_half_decile_rows"] >= 30
    assert monotonicity["upper_half_decile_rows"] >= 30
    assert "insufficient_data_reasons" not in monotonicity


def test_feature_diagnostics_handles_unavailable_scaler() -> None:
    prediction = _regression_prediction(
        predicted_delta=0.01,
        realized_return=0.01,
    )
    prediction.metadata["feature_schema_version"] = ML_FEATURE_SCHEMA_VERSION
    prediction.metadata["features"] = {
        name: float(index) for index, name in enumerate(ML_FEATURE_NAMES, start=1)
    }

    diagnostics = _build_feature_diagnostics(
        [prediction],
        [
            (
                {
                    "source": "live_model",
                    "model_key": "global|1h|features_ohlc_v5|dummy",
                },
                object(),
            )
        ],
    )

    assert diagnostics["schema_version"] == ML_FEATURE_SCHEMA_VERSION
    assert diagnostics["prediction_count"] == 1
    assert set(diagnostics["raw_feature_quantiles"]) == set(ML_FEATURE_NAMES)
    assert diagnostics["scaled_available"] is False
    assert "scaled_feature_quantiles" not in diagnostics


class _PassthroughScaledModel:
    scaler_initialized = True

    def __init__(self, coefficients: list[float] | None = None) -> None:
        if coefficients is not None:
            self.coef_ = coefficients

    def _scaled(self, rows: list[list[float]]) -> list[list[float]]:
        return rows


def _feature_prediction_row(
    values: list[float], *, fold_index: int = 1
) -> MLWalkForwardPrediction:
    prediction = _regression_prediction(
        predicted_delta=0.01,
        realized_return=0.01,
        fold_index=fold_index,
    )
    prediction.metadata["feature_schema_version"] = ML_FEATURE_SCHEMA_VERSION
    prediction.metadata["features"] = dict(zip(ML_FEATURE_NAMES, values))
    return prediction


def test_feature_diagnostics_reports_no_health_warnings_for_sane_scaled_features() -> None:
    predictions = [
        _feature_prediction_row([value] * len(ML_FEATURE_NAMES))
        for value in (-1.0, 0.0, 0.0, 1.0)
    ]

    diagnostics = _build_feature_diagnostics(
        predictions,
        [
            (
                {
                    "source": "live_model",
                    "model_key": "global|1h|features_ohlc_v5|dummy",
                },
                _PassthroughScaledModel(),
            )
        ],
    )

    assert diagnostics["scaled_available"] is True
    assert diagnostics["health_warnings"] == []
    assert diagnostics["health_thresholds"]["scaled_tail_abs_warn"] == pytest.approx(
        3.0
    )


def test_feature_diagnostics_warns_for_tail_heavy_scaled_features() -> None:
    feature_count = len(ML_FEATURE_NAMES)
    volume_index = list(ML_FEATURE_NAMES).index("volume_log_ratio")
    upper_wick_index = list(ML_FEATURE_NAMES).index("upper_wick_atr")
    rows = [[0.0] * feature_count for _ in range(4)]
    rows[-1][volume_index] = 4.0
    rows[-1][upper_wick_index] = 4.5
    predictions = [_feature_prediction_row(row) for row in rows]

    diagnostics = _build_feature_diagnostics(
        predictions,
        [
            (
                {
                    "source": "live_model",
                    "model_key": "global|1h|features_ohlc_v5|dummy",
                },
                _PassthroughScaledModel(),
            )
        ],
    )

    warnings = diagnostics["health_warnings"]
    assert any(
        "High-risk scaled feature volume_log_ratio" in warning
        for warning in warnings
    )
    assert any(
        "High-risk scaled feature upper_wick_atr" in warning
        for warning in warnings
    )


def test_feature_diagnostics_reports_linear_feature_contributions() -> None:
    feature_count = len(ML_FEATURE_NAMES)
    first_feature = ML_FEATURE_NAMES[0]
    second_feature = ML_FEATURE_NAMES[1]
    coefficients = [0.0] * feature_count
    coefficients[0] = 2.0
    coefficients[1] = -1.0
    rows = [[1.0, 2.0] + [0.0] * (feature_count - 2)]
    rows.append([3.0, -2.0] + [0.0] * (feature_count - 2))
    predictions = [_feature_prediction_row(row) for row in rows]

    diagnostics = _build_feature_diagnostics(
        predictions,
        [
            (
                {
                    "source": "live_model",
                    "model_key": "global|1h|features_ohlc_v5|dummy",
                },
                _PassthroughScaledModel(coefficients),
            )
        ],
    )

    contributions = diagnostics["linear_contributions"]
    assert contributions[0]["feature"] == first_feature
    assert contributions[0]["coefficient"] == pytest.approx(2.0)
    assert contributions[0]["scaled_feature_std"] == pytest.approx(1.0)
    assert contributions[0]["coef_times_scaled_std"] == pytest.approx(2.0)
    assert contributions[0]["avg_abs_row_contribution"] == pytest.approx(4.0)
    assert contributions[0]["p95_abs_row_contribution"] == pytest.approx(5.8)
    second = next(row for row in contributions if row["feature"] == second_feature)
    assert second["avg_abs_row_contribution"] == pytest.approx(2.0)


def test_feature_diagnostics_uses_profile_feature_names() -> None:
    names = list(feature_names_for_profile("drop_weakest"))
    coefficients = [0.0] * len(names)
    coefficients[0] = 1.0
    prediction = _regression_prediction(
        predicted_delta=0.01,
        realized_return=0.01,
    )
    prediction.metadata["feature_schema_version"] = ML_FEATURE_SCHEMA_VERSION
    prediction.metadata["features"] = {
        "feature_schema_version": ML_FEATURE_SCHEMA_VERSION,
        "feature_profile": "drop_weakest",
        "feature_names": names,
        "feature_profile_excluded_features": [
            name for name in ML_FEATURE_NAMES if name not in names
        ],
        **{name: float(index) for index, name in enumerate(names, start=1)},
    }

    diagnostics = _build_feature_diagnostics(
        [prediction],
        [
            (
                {
                    "source": "live_model",
                    "model_key": (
                        "global|1h|features_ohlc_v5_profile_drop_weakest|dummy"
                    ),
                },
                _PassthroughScaledModel(coefficients),
            )
        ],
    )

    assert diagnostics["feature_profile"] == "drop_weakest"
    assert diagnostics["feature_names"] == names
    assert "pct_change" not in diagnostics["raw_feature_quantiles"]
    assert set(diagnostics["raw_feature_quantiles"]) == set(names)
    assert diagnostics["linear_contributions"][0]["feature"] == names[0]


def test_feature_diagnostics_reports_clipping_stats_and_warnings() -> None:
    rows = [[0.0] * len(ML_FEATURE_NAMES) for _ in range(10)]
    predictions = [_feature_prediction_row(row) for row in rows]
    for index, prediction in enumerate(predictions):
        was_clipped = index < 3
        raw_value = 0.25 if was_clipped else 0.01
        clipped_value = 0.15 if was_clipped else raw_value
        prediction.metadata["features"]["feature_clipping_version"] = (
            ML_FEATURE_CLIPPING_VERSION
        )
        prediction.metadata["features"]["feature_clipping"] = {
            "pct_change": {
                "cap_min": -0.15,
                "cap_max": 0.15,
                "raw_value": raw_value,
                "clipped_value": clipped_value,
                "was_clipped": was_clipped,
            }
        }

    diagnostics = _build_feature_diagnostics(
        predictions,
        [
            (
                {
                    "source": "live_model",
                    "model_key": "global|1h|features_ohlc_v5|dummy",
                },
                _PassthroughScaledModel(),
            )
        ],
    )

    clipping = diagnostics["clipping"]["features"]["pct_change"]
    assert diagnostics["clipping"]["version"] == ML_FEATURE_CLIPPING_VERSION
    assert clipping["observed_count"] == 10
    assert clipping["clipped_count"] == 3
    assert clipping["clipped_rate"] == pytest.approx(0.3)
    assert clipping["cap_min"] == pytest.approx(-0.15)
    assert clipping["cap_max"] == pytest.approx(0.15)
    assert clipping["raw_min"] == pytest.approx(0.01)
    assert clipping["raw_max"] == pytest.approx(0.25)
    assert clipping["research_gate_failed"] is True
    assert any("pct_change clipped on 30.0%" in warning for warning in diagnostics["health_warnings"])


def test_feature_diagnostics_omits_clipping_warning_at_two_percent() -> None:
    rows = [[0.0] * len(ML_FEATURE_NAMES) for _ in range(50)]
    predictions = [_feature_prediction_row(row) for row in rows]
    for index, prediction in enumerate(predictions):
        was_clipped = index == 0
        raw_value = 0.25 if was_clipped else 0.01
        clipped_value = 0.15 if was_clipped else raw_value
        prediction.metadata["features"]["feature_clipping_version"] = (
            ML_FEATURE_CLIPPING_VERSION
        )
        prediction.metadata["features"]["feature_clipping"] = {
            "pct_change": {
                "cap_min": -0.15,
                "cap_max": 0.15,
                "raw_value": raw_value,
                "clipped_value": clipped_value,
                "was_clipped": was_clipped,
            }
        }

    diagnostics = _build_feature_diagnostics(
        predictions,
        [
            (
                {
                    "source": "live_model",
                    "model_key": "global|1h|features_ohlc_v5|dummy",
                },
                _PassthroughScaledModel(),
            )
        ],
    )

    clipping = diagnostics["clipping"]["features"]["pct_change"]
    assert clipping["clipped_rate"] == pytest.approx(0.02)
    assert not any("pct_change clipped" in warning for warning in diagnostics["health_warnings"])


def test_diagnostic_warnings_surface_non_monotonic_regression_calibration() -> None:
    warnings = _build_diagnostic_warnings(
        [
            {
                "fold_index": 1,
                "diagnostics": {
                    "models": [],
                    "training": {},
                    "predictions": {"prediction_count": 4},
                    "outcomes": {
                        "above_evaluation_hurdle": {"count": 1, "rate": 0.25}
                    },
                },
                "regression_calibration": {
                    "monotonicity": {
                        "available": True,
                        "upper_half_improves": False,
                    }
                },
            }
        ]
    )

    assert any("predicted-delta buckets" in warning for warning in warnings)


def test_summary_warns_when_aggregate_regression_calibration_is_non_monotonic() -> None:
    # Need at least 60 rows split 30/30 across the deciles for monotonicity to
    # produce a real True/False signal, so build a clearly anti-monotonic set.
    predictions = []
    for index in range(80):
        predicted_delta = -0.02 + (0.06 * index / 79)
        realized_return = -predicted_delta  # anti-monotonic by construction
        predictions.append(
            _regression_prediction(
                predicted_delta=predicted_delta,
                realized_return=realized_return,
            )
        )
    now = datetime.fromtimestamp(1_700_000_000, tz=UTC)
    summary = MLWalkForwardSummary(
        start=now,
        end=now,
        strategy_id="ai_regression",
        strategy_type="machine_learning_regression",
        timeframe="1h",
        train_bars=3,
        test_bars=3,
        folds=[
            MLWalkForwardFold(
                fold_index=1,
                train_start=now,
                train_end=now,
                test_start=now,
                test_end=now,
                train_cycles=3,
                test_cycles=3,
                predictions=predictions,
            )
        ],
        fee_bps=10.0,
        slippage_bps=20.0,
        pairs=["BTC/USD"],
        coverage_status="ready",
        warnings=[],
    )

    payload = summary.to_dict()

    assert (
        "Higher predicted-delta buckets did not improve realized returns overall."
        in payload["diagnostic_warnings"]
    )


def test_summary_skips_monotonicity_warning_for_insufficient_data() -> None:
    # Six predictions is below the monotonicity sample-size guard, so we should
    # see the "skipped" warning rather than the "did not improve" warning.
    predictions = [
        _regression_prediction(predicted_delta=-0.004, realized_return=0.010),
        _regression_prediction(predicted_delta=0.001, realized_return=0.008),
        _regression_prediction(predicted_delta=0.004, realized_return=0.006),
        _regression_prediction(predicted_delta=0.006, realized_return=0.000),
        _regression_prediction(predicted_delta=0.010, realized_return=-0.002),
        _regression_prediction(predicted_delta=0.020, realized_return=-0.004),
    ]
    now = datetime.fromtimestamp(1_700_000_000, tz=UTC)
    summary = MLWalkForwardSummary(
        start=now,
        end=now,
        strategy_id="ai_regression",
        strategy_type="machine_learning_regression",
        timeframe="1h",
        train_bars=3,
        test_bars=3,
        folds=[
            MLWalkForwardFold(
                fold_index=1,
                train_start=now,
                train_end=now,
                test_start=now,
                test_end=now,
                train_cycles=3,
                test_cycles=3,
                predictions=predictions,
            )
        ],
        fee_bps=10.0,
        slippage_bps=20.0,
        pairs=["BTC/USD"],
        coverage_status="ready",
        warnings=[],
    )

    payload = summary.to_dict()

    assert not any(
        "did not improve realized returns" in warning
        for warning in payload["diagnostic_warnings"]
    )
    assert any(
        "monotonicity check skipped for insufficient data" in warning
        for warning in payload["diagnostic_warnings"]
    )


def test_diagnostic_warnings_surface_collapsed_model_and_constant_predictions() -> None:
    warnings = _build_diagnostic_warnings(
        [
            {
                "fold_index": 1,
                "diagnostics": {
                    "models": [
                        {
                            "initialized": True,
                            "coefficient_norm": 0.0,
                        }
                    ],
                    "training": {
                        "class_balance": {
                            "positive_label_count": 0,
                            "positive_label_rate": 0.0,
                        }
                    },
                    "predictions": {
                        "prediction_count": 2,
                        "positive_edge_prediction_count": 0,
                        "predicted_delta_quantiles": {"count": 2, "std": 0.0},
                    },
                    "outcomes": {
                        "above_evaluation_hurdle": {"count": 0, "rate": 0.0}
                    },
                    "features": {
                        "health_warnings": [
                            "High-risk scaled feature volume_log_ratio has tail values."
                        ]
                    },
                },
            }
        ]
    )

    assert any("coefficients" in warning for warning in warnings)
    assert any("near-constant" in warning for warning in warnings)
    assert any("No positive-edge predictions" in warning for warning in warnings)
    assert any("No positive labels" in warning for warning in warnings)
    assert any("evaluation hurdle" in warning for warning in warnings)
    assert any("feature health warnings" in warning for warning in warnings)


def _full_research_metrics(
    *,
    prediction_count: int = 30,
    positive_edge_prediction_count: int = 10,
    edge_prediction_accuracy: float = 0.7,
    directional_accuracy: float = 0.6,
    directional_prediction_count: int = 0,
    precision_long: float = 0.3,
) -> dict[str, Any]:
    return {
        "prediction_count": prediction_count,
        "positive_edge_prediction_count": positive_edge_prediction_count,
        "directional_prediction_count": directional_prediction_count,
        "directional_accuracy": directional_accuracy,
        "edge_prediction_accuracy": edge_prediction_accuracy,
        "precision_long": precision_long,
    }


def _calibration_with_lift(
    *,
    base_hit_rate: float = 0.18,
    p95_lift: float = 1.5,
    selected_avg: float = 0.001,
) -> dict[str, Any]:
    return {
        "threshold_sweeps": [
            {
                "name": "evaluation_hurdle",
                "realized_hit_rate": base_hit_rate,
                "avg_realized_return_selected": selected_avg,
            },
            {
                "name": "predicted_delta_p95",
                "lift_over_base_rate": p95_lift,
                "avg_realized_return_selected": selected_avg,
            },
        ],
        "monotonicity": {"upper_half_improves": True},
    }


def test_assess_promotability_blocks_when_research_fails() -> None:
    metrics = _full_research_metrics(positive_edge_prediction_count=0)

    assessment = _assess_promotability(
        metrics=metrics,
        regression_calibration={},
        fold_dicts=[],
        round_trip_cost_pct=0.015,
    )

    assert assessment.tier == PROMOTION_TIER_BLOCKED
    assert assessment.is_operational is False
    research = assessment.tier_results[0]
    assert research.tier == PROMOTION_TIER_RESEARCH
    assert research.clears is False
    assert any("positive-edge" in reason for reason in research.reasons)


def test_assess_promotability_research_only_when_risk_overlay_fails() -> None:
    # Clears research promising but precision lift is too low → risk_overlay blocks
    metrics = _full_research_metrics(precision_long=0.18)  # equal to base, lift = 1.0
    calibration = _calibration_with_lift(p95_lift=1.5)

    assessment = _assess_promotability(
        metrics=metrics,
        regression_calibration=calibration,
        fold_dicts=[],
        round_trip_cost_pct=0.015,
    )

    assert assessment.tier == PROMOTION_TIER_RESEARCH
    assert assessment.is_operational is False
    risk_overlay = assessment.tier_results[1]
    assert risk_overlay.clears is False
    assert any("Precision lift" in reason for reason in risk_overlay.reasons)


def test_assess_promotability_reaches_risk_overlay_with_lift_and_positive_return() -> None:
    metrics = _full_research_metrics(precision_long=0.30)  # 0.30 / 0.18 ≈ 1.67x lift
    calibration = _calibration_with_lift(
        base_hit_rate=0.18,
        p95_lift=1.5,
        selected_avg=0.001,
    )

    assessment = _assess_promotability(
        metrics=metrics,
        regression_calibration=calibration,
        fold_dicts=[],
        round_trip_cost_pct=0.015,
    )

    assert assessment.tier == PROMOTION_TIER_RISK_OVERLAY
    assert assessment.is_operational is True
    self_standing = assessment.tier_results[2]
    assert self_standing.clears is False
    assert any(
        "below 50% after estimated costs" in reason
        for reason in self_standing.reasons
    )


def test_assess_promotability_reaches_self_standing_with_strict_metrics() -> None:
    metrics = _full_research_metrics(precision_long=0.55)
    # selected_avg must exceed 2x round_trip_cost (0.015) = 0.030
    calibration = _calibration_with_lift(
        base_hit_rate=0.18,
        p95_lift=2.0,
        selected_avg=0.05,
    )
    fold_dicts = [
        {
            "fold_index": 1,
            "metrics": {
                "positive_edge_prediction_count": 5,
                "edge_prediction_accuracy": 0.6,
            },
            "regression_calibration": {
                "monotonicity": {"upper_half_improves": True}
            },
        },
    ]

    assessment = _assess_promotability(
        metrics=metrics,
        regression_calibration=calibration,
        fold_dicts=fold_dicts,
        round_trip_cost_pct=0.015,
    )

    assert assessment.tier == PROMOTION_TIER_SELF_STANDING
    assert all(result.clears for result in assessment.tier_results)


def test_summary_promotable_reasons_are_clean_for_operational_tier() -> None:
    # Build predictions whose realized returns mirror predicted deltas. The
    # resulting metrics clear an operational tier (precision_long is high and
    # the top decile beats the hurdle), so promotable_reasons must read as a
    # pass message rather than a list of failure bullets. The dedicated
    # `test_assess_promotability_reaches_risk_overlay_*` tests cover the
    # risk-overlay vs self-standing tier boundary directly.
    predictions = []
    for index in range(80):
        predicted_delta = -0.02 + (0.06 * index / 79)
        realized_return = predicted_delta + (0.001 if index % 2 == 0 else -0.001)
        predictions.append(
            _regression_prediction(
                predicted_delta=predicted_delta,
                realized_return=realized_return,
            )
        )
    now = datetime.fromtimestamp(1_700_000_000, tz=UTC)
    summary = MLWalkForwardSummary(
        start=now,
        end=now,
        strategy_id="ai_regression",
        strategy_type="machine_learning_regression",
        timeframe="4h",
        train_bars=40,
        test_bars=40,
        folds=[
            MLWalkForwardFold(
                fold_index=1,
                train_start=now,
                train_end=now,
                test_start=now,
                test_end=now,
                train_cycles=40,
                test_cycles=40,
                predictions=predictions,
            )
        ],
        fee_bps=10.0,
        slippage_bps=20.0,
        pairs=["BTC/USD"],
        coverage_status="ready",
        warnings=[],
    )

    payload = summary.to_dict()
    tier = payload["promotion_tier"]

    if payload["promotable"]:
        # Operational tier promotable_reasons must be a pass message, not the
        # failure list of the next-higher tier.
        for reason in payload["promotable_reasons"]:
            assert "below" not in reason.lower()
            assert "fail" not in reason.lower()
            assert "non-monotonic" not in reason.lower()
        # The next-tier blockers must still be accessible via promotion_tiers.
        next_tier = {
            "research_promising": "risk_overlay_candidate",
            "risk_overlay_candidate": "self_standing",
        }.get(tier)
        if next_tier is not None:
            next_block = payload["promotion_tiers"].get(next_tier) or {}
            assert next_block.get("clears") is False
            assert next_block.get("reasons")


def test_assess_promotability_holds_at_risk_overlay_on_per_fold_failure() -> None:
    metrics = _full_research_metrics(precision_long=0.55)
    calibration = _calibration_with_lift(p95_lift=2.0, selected_avg=0.05)
    fold_dicts = [
        {
            "fold_index": 2,
            "metrics": {
                "positive_edge_prediction_count": 0,  # per-fold strict failure
                "edge_prediction_accuracy": 0.6,
            },
            "regression_calibration": {
                "monotonicity": {"upper_half_improves": True}
            },
        },
    ]

    assessment = _assess_promotability(
        metrics=metrics,
        regression_calibration=calibration,
        fold_dicts=fold_dicts,
        round_trip_cost_pct=0.015,
    )

    assert assessment.tier == PROMOTION_TIER_RISK_OVERLAY
    self_standing = assessment.tier_results[2]
    assert any("Per-fold strict checks" in reason for reason in self_standing.reasons)


def test_run_ml_walk_forward_rejects_non_ml_strategy(tmp_path: Path) -> None:
    config = _build_ml_config(tmp_path)
    _seed_pair_metadata(config)
    timestamps = [1_700_000_000 + idx * 3600 for idx in range(20)]
    closes = [100.0 + idx for idx in range(20)]
    _write_ohlc_series(tmp_path, timestamps, closes)

    with pytest.raises(ValueError, match="not an ML strategy"):
        run_ml_walk_forward(
            config,
            start=datetime.fromtimestamp(timestamps[0], tz=UTC),
            end=datetime.fromtimestamp(timestamps[-1], tz=UTC),
            strategy_id="trend_core",
            timeframe="1h",
            train_bars=5,
            test_bars=5,
        )
