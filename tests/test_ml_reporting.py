from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from krakked.backtest.ml_reporting import (
    get_latest_ml_walk_forward_report_path,
    load_ml_walk_forward_report,
    publish_latest_ml_walk_forward_report,
    summarize_latest_ml_walk_forward_report,
    validate_ml_walk_forward_report_payload,
    write_ml_walk_forward_report,
)


def _sample_report() -> dict[str, Any]:
    generated_at = datetime(2026, 5, 23, tzinfo=UTC).isoformat()
    return {
        "report_version": 8,
        "generated_at": generated_at,
        "summary": {
            "start": generated_at,
            "end": generated_at,
            "strategy_id": "ai_predictor",
            "strategy_type": "machine_learning",
            "timeframe": "1h",
            "train_bars": 12,
            "test_bars": 6,
            "evaluation_mode": "rolling_window_isolated",
            "edge_scoring_mode": "intent_hurdle_aligned",
            "model_state_reused_across_folds": False,
            "model_semantics": {
                "model_family": "classifier",
                "strategy_type": "machine_learning",
                "training_target": "fee_adjusted_classification",
                "prediction_target": "fee_adjusted_positive_edge",
                "prediction_targets": ["fee_adjusted_positive_edge"],
                "feature_schema": "ohlc_v5",
                "feature_profile": "all",
                "feature_schemas": ["ohlc_v5"],
                "feature_profiles": ["all"],
            },
            "cost_semantics": {
                "fee_bps": 25.0,
                "slippage_bps": 50.0,
                "round_trip_cost_bps": 150.0,
                "round_trip_cost_pct": 0.015,
                "label_cost_multipliers": [2.0],
                "edge_cost_multipliers": [],
                "evaluation_hurdle_source": "label_hurdle_bps",
                "evaluation_hurdle_sources": {"label_hurdle_bps": 3},
                "evaluation_hurdle_pct": 0.03,
                "evaluation_hurdle_pct_quantiles": {"count": 3},
            },
            "fold_count": 1,
            "pairs": ["BTC/USD"],
            "fee_bps": 25.0,
            "slippage_bps": 50.0,
            "round_trip_cost_bps": 150.0,
            "coverage_status": "ready",
            "warnings": [],
            "metrics": {
                "prediction_count": 3,
                "positive_edge_prediction_count": 1,
                "edge_prediction_accuracy": 2 / 3,
                "directional_accuracy": 1.0,
                "precision_long": 1.0,
            },
            "confidence_buckets": [
                {
                    "bucket": "0.70-0.80",
                    "prediction_count": 3,
                    "edge_prediction_accuracy": 2 / 3,
                }
            ],
            "regression_calibration": {
                "prediction_count": 0,
                "threshold_sweeps": [],
                "predicted_delta_deciles": [],
                "monotonicity": {"available": False},
            },
            "baselines": {
                "cash": {
                    "fold_count": 1,
                    "avg_return_pct": 0.0,
                    "positive_folds": 0,
                    "avg_max_drawdown_pct": 0.0,
                    "warnings": [],
                },
                "buy_hold_by_pair": {},
                "buy_hold_equal_weight": {
                    "fold_count": 1,
                    "avg_return_pct": 0.1,
                    "positive_folds": 1,
                    "avg_max_drawdown_pct": 0.2,
                    "warnings": [],
                },
                "warnings": [],
            },
            "diagnostic_warnings": [],
            "promotion_tier": "blocked",
            "promotion_tiers": {
                "research_promising": {
                    "tier": "research_promising",
                    "clears": False,
                    "reasons": ["Fewer than 20 scored out-of-sample predictions."],
                },
                "risk_overlay_candidate": {
                    "tier": "risk_overlay_candidate",
                    "clears": False,
                    "reasons": ["Earlier tier research promising did not clear."],
                },
                "self_standing": {
                    "tier": "self_standing",
                    "clears": False,
                    "reasons": ["Earlier tier research promising did not clear."],
                },
            },
            "promotable": False,
            "promotable_reasons": ["Fewer than 20 scored out-of-sample predictions."],
            "folds": [],
        },
        "provenance": {"generated_by": "krakked ml-walk-forward"},
    }


def test_ml_report_write_load_and_summarize(tmp_path: Path) -> None:
    report_path = tmp_path / "ml-report.json"
    payload = _sample_report()

    written = write_ml_walk_forward_report(payload, report_path)
    loaded = load_ml_walk_forward_report(report_path)
    summary = summarize_latest_ml_walk_forward_report(loaded, resolved_path=report_path)

    assert written == report_path.resolve()
    assert loaded == payload
    assert summary["strategy_id"] == "ai_predictor"
    assert summary["strategy_type"] == "machine_learning"
    assert summary["evaluation_mode"] == "rolling_window_isolated"
    assert summary["edge_scoring_mode"] == "intent_hurdle_aligned"
    assert summary["edge_prediction_accuracy"] == pytest.approx(2 / 3)
    assert summary["diagnostic_warnings"] == []
    assert summary["model_semantics"]["model_family"] == "classifier"
    assert summary["cost_semantics"]["evaluation_hurdle_source"] == "label_hurdle_bps"
    assert summary["baselines"]["cash"]["avg_return_pct"] == 0.0
    assert summary["confidence_buckets"][0]["bucket"] == "0.70-0.80"
    assert summary["regression_calibration"]["prediction_count"] == 0
    assert summary["promotion_tier"] == "blocked"
    assert summary["promotion_tiers"]["research_promising"]["clears"] is False


def test_ml_report_publish_latest_uses_ml_specific_path(tmp_path: Path) -> None:
    payload = _sample_report()

    published = publish_latest_ml_walk_forward_report(payload, config_dir=tmp_path)

    assert published == get_latest_ml_walk_forward_report_path(tmp_path)
    assert published.parts[-3:] == ("reports", "ml", "latest.json")
    assert json.loads(published.read_text(encoding="utf-8")) == payload


def test_ml_report_validation_rejects_old_report_version_with_regenerate_hint(
    tmp_path: Path,
) -> None:
    payload = _sample_report()
    payload["report_version"] = 2

    with pytest.raises(ValueError, match="regenerate with `krakked ml-walk-forward`"):
        validate_ml_walk_forward_report_payload(
            payload, resolved_path=tmp_path / "bad.json"
        )
