from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from krakked.backtest.ml_walk_forward import (
    _build_diagnostic_warnings,
    _build_prediction_metrics,
    _build_walk_forward_folds,
    _score_intent,
    _set_strategy_learning,
    run_ml_walk_forward,
)
from krakked.config import AppConfig, StrategyConfig, load_config
from krakked.market_data.metadata_store import PairMetadataStore
from krakked.market_data.models import PairMetadata
from krakked.strategy.models import StrategyIntent
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

    assert report["report_version"] == 4
    assert report["provenance"]["generated_by"] == "krakked ml-walk-forward"
    assert summary["strategy_id"] == "ai_regression"
    assert summary["timeframe"] == "1h"
    assert summary["evaluation_mode"] == "rolling_window_isolated"
    assert summary["edge_scoring_mode"] == "intent_hurdle_aligned"
    assert summary["model_state_reused_across_folds"] is False
    assert summary["fold_count"] >= 1
    assert summary["metrics"]["prediction_count"] > 0
    assert "directional_accuracy" in summary["metrics"]
    assert summary["metrics"]["edge_prediction_accuracy"] is not None
    assert "positive_edge_prediction_count" in summary["metrics"]
    assert isinstance(summary["diagnostic_warnings"], list)
    assert summary["round_trip_cost_bps"] == pytest.approx(150.0)
    assert summary["confidence_buckets"]
    assert summary["folds"][0]["prediction_count"] > 0
    assert summary["folds"][0]["confidence_buckets"]
    fold_diagnostics = summary["folds"][0]["diagnostics"]
    assert fold_diagnostics["models"]
    assert "coef" in fold_diagnostics["models"][0]
    assert "intercept" in fold_diagnostics["models"][0]
    assert "n_iter" in fold_diagnostics["models"][0]
    assert "predicted_delta_quantiles" in fold_diagnostics["predictions"]
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
                },
            }
        ]
    )

    assert any("coefficients" in warning for warning in warnings)
    assert any("near-constant" in warning for warning in warnings)
    assert any("No positive-edge predictions" in warning for warning in warnings)
    assert any("No positive labels" in warning for warning in warnings)
    assert any("evaluation hurdle" in warning for warning in warnings)


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
