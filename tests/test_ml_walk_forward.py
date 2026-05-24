from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import sqlite3

import pandas as pd
import pytest

from krakked.backtest.ml_walk_forward import (
    _build_walk_forward_folds,
    run_ml_walk_forward,
)
from krakked.config import AppConfig, load_config
from krakked.market_data.metadata_store import PairMetadataStore
from krakked.market_data.models import PairMetadata


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

    assert report["report_version"] == 2
    assert report["provenance"]["generated_by"] == "krakked ml-walk-forward"
    assert summary["strategy_id"] == "ai_regression"
    assert summary["timeframe"] == "1h"
    assert summary["evaluation_mode"] == "rolling_window_isolated"
    assert summary["model_state_reused_across_folds"] is False
    assert summary["fold_count"] >= 1
    assert summary["metrics"]["prediction_count"] > 0
    assert summary["metrics"]["directional_accuracy"] is not None
    assert summary["round_trip_cost_bps"] == pytest.approx(150.0)
    assert summary["confidence_buckets"]
    assert summary["folds"][0]["prediction_count"] > 0
    assert summary["folds"][0]["confidence_buckets"]


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
            (count,) = conn.execute(
                "SELECT COUNT(*) FROM ml_training_examples"
            ).fetchone()
        assert 0 < count <= 12


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
