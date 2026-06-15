from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

import krakked.backtest.ml_regime_overlay_research as overlay_research
from krakked.backtest.ml_regime_overlay_research import (
    MLRegimeOverlayResearchParams,
    _best_scale_label,
    _chronological_window_specs,
    _summary,
)


def test_ml_regime_overlay_params_reject_invalid_values() -> None:
    with pytest.raises(ValueError, match="allocation_pct"):
        MLRegimeOverlayResearchParams(allocation_pct=0.0)

    with pytest.raises(ValueError, match="min_training_examples"):
        MLRegimeOverlayResearchParams(min_training_examples=0)


def test_best_scale_label_prefers_higher_scale_when_forward_return_is_positive() -> (
    None
):
    label, scale = _best_scale_label(  # noqa: SLF001
        {"BTC/USD": 0.2},
        price_maps={
            "BTC/USD": {
                1: 100.0,
                2: 110.0,
            }
        },
        timeline=[1, 2],
        index=0,
        next_index=1,
        fee_bps=0.0,
    )

    assert scale == pytest.approx(1.0)
    assert label == 2


def test_ml_regime_overlay_gate_rejects_cash_like_ml_exposure() -> None:
    summary = _summary(  # noqa: SLF001
        [
            {
                "status": "ready",
                "strict_data_ready": True,
                "evidence_bucket": "current_rolling",
                "rows": {
                    "handcoded_top2_soft_target_scale": {
                        "avg_exposure_pct": 20.0,
                    },
                    "ml_scale_overlay": {
                        "avg_exposure_pct": 1.0,
                    },
                },
                "comparisons": {
                    "ml_vs_handcoded": {
                        "delta_return_pct": 0.2,
                        "delta_max_drawdown_pct": -0.1,
                    }
                },
            }
        ],
        params=MLRegimeOverlayResearchParams(),
        timeframe="4h",
    )

    assert summary["min_required_ml_exposure_pct"] == pytest.approx(7.0)
    assert summary["promotion_gate"]["not_cash_only"] is False
    assert summary["promotion_gate"]["passed"] is False


def _healthy_window(bucket: str) -> dict:
    """A window where ML cleanly beats the handcoded baseline on every metric."""
    return {
        "status": "ready",
        "strict_data_ready": True,
        "evidence_bucket": bucket,
        "rows": {
            "handcoded_top2_soft_target_scale": {"avg_exposure_pct": 15.0},
            "ml_scale_overlay": {"avg_exposure_pct": 15.0},
        },
        "comparisons": {
            "ml_vs_handcoded": {
                "delta_return_pct": 0.2,
                "delta_max_drawdown_pct": -0.1,
            }
        },
    }


def test_ml_regime_overlay_gate_passes_with_full_regime_coverage() -> None:
    summary = _summary(  # noqa: SLF001
        [
            _healthy_window("uptrend"),
            _healthy_window("downtrend"),
            _healthy_window("chop_or_transition"),
            _healthy_window("current_rolling"),
        ],
        params=MLRegimeOverlayResearchParams(),
        timeframe="4h",
    )

    assert summary["regime_coverage_sufficient"] is True
    assert summary["promotion_gate"]["regime_coverage_sufficient"] is True
    assert summary["promotion_gate"]["current_rolling_not_worse"] is True
    assert summary["promotion_gate"]["regime_bucket_return_drawdown"] is True
    assert summary["promotion_gate"]["passed"] is True


def test_ml_regime_overlay_gate_blocks_when_no_uptrend_window() -> None:
    # Even with healthy return/drawdown/exposure metrics, a window set missing an
    # uptrend regime must fail the gate: the verdict is inconclusive without it.
    summary = _summary(  # noqa: SLF001
        [
            _healthy_window("downtrend"),
            _healthy_window("downtrend"),
            _healthy_window("chop_or_transition"),
            _healthy_window("current_rolling"),
        ],
        params=MLRegimeOverlayResearchParams(),
        timeframe="4h",
    )

    assert summary["insufficient_regime_coverage"] is True
    assert summary["promotion_gate"]["regime_coverage_sufficient"] is False
    assert summary["promotion_gate"]["not_cash_only"] is True
    assert summary["promotion_gate"]["passed"] is False


def test_ml_regime_overlay_gate_blocks_current_rolling_failure() -> None:
    current = _healthy_window("current_rolling")
    current["comparisons"]["ml_vs_handcoded"]["delta_return_pct"] = -0.01

    summary = _summary(  # noqa: SLF001
        [
            _healthy_window("uptrend"),
            _healthy_window("downtrend"),
            _healthy_window("chop_or_transition"),
            current,
        ],
        params=MLRegimeOverlayResearchParams(),
        timeframe="4h",
    )

    assert summary["promotion_gate"]["current_rolling_not_worse"] is False
    assert summary["promotion_gate"]["passed"] is False


def test_ml_regime_overlay_sorts_windows_chronologically() -> None:
    specs = _chronological_window_specs(  # noqa: SLF001
        {
            "shuffled": [
                ("late", "2026-02-01T00:00:00Z", "2026-02-02T00:00:00Z"),
                ("early", "2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z"),
            ]
        }
    )

    assert [spec.window_id for spec in specs] == ["early", "late"]


def test_ml_regime_overlay_run_uses_only_earlier_windows_for_training(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fit_example_counts: list[int] = []

    def _fake_evaluate(*args, **kwargs):  # noqa: ANN002, ANN003
        return SimpleNamespace(
            runs=[
                {
                    "overlay_mode": "none",
                    "return_pct": 0.0,
                    "max_drawdown_pct": 1.0,
                    "trades": 0,
                    "fees_usd": 0.0,
                    "active_cycle_pct": 100.0,
                    "avg_exposure_pct": 10.0,
                },
                {
                    "overlay_mode": "target_scale",
                    "return_pct": 0.0,
                    "max_drawdown_pct": 1.0,
                    "trades": 0,
                    "fees_usd": 0.0,
                    "active_cycle_pct": 100.0,
                    "avg_exposure_pct": 10.0,
                },
            ]
        )

    def _fake_fit(examples, *, params):  # noqa: ANN001
        fit_example_counts.append(len(examples))
        return None

    def _fake_examples(*args, **kwargs):  # noqa: ANN002, ANN003
        start = kwargs["start"].isoformat()
        return [
            {
                "features": [1.0],
                "label": 0,
                "start": start,
                "label_end_timestamp": int(kwargs["end"].timestamp()),
            }
        ]

    monkeypatch.setattr(
        overlay_research,
        "build_evidence_window_context",
        lambda *args, **kwargs: {"windows": []},
    )
    monkeypatch.setattr(
        overlay_research,
        "_load_window_bars",
        lambda *args, **kwargs: ({}, {"missing_series": [], "partial_series": []}),
    )
    monkeypatch.setattr(
        overlay_research,
        "evaluate_market_regime_exposure_scenarios",
        _fake_evaluate,
    )
    monkeypatch.setattr(overlay_research, "_fit_model", _fake_fit)
    monkeypatch.setattr(overlay_research, "_build_training_examples", _fake_examples)

    result = overlay_research.run_ml_regime_overlay_research(
        cast(Any, SimpleNamespace(universe=SimpleNamespace(include_pairs=["BTC/USD"]))),
        window_sets={
            "shuffled": [
                ("late", "2026-02-01T00:00:00Z", "2026-02-02T00:00:00Z"),
                ("early", "2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z"),
            ]
        },
        params=MLRegimeOverlayResearchParams(min_training_examples=99),
    )

    assert [window["window_id"] for window in result.windows] == ["early", "late"]
    assert [window["training_examples_before"] for window in result.windows] == [0, 1]
    assert [window["training_examples_used"] for window in result.windows] == [0, 1]
    assert fit_example_counts == [0, 1]


def test_ml_regime_overlay_excludes_overlapping_future_labels_from_training(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fit_example_counts: list[int] = []

    def _fake_evaluate(*args, **kwargs):  # noqa: ANN002, ANN003
        return SimpleNamespace(
            runs=[
                {
                    "overlay_mode": "none",
                    "return_pct": 0.0,
                    "max_drawdown_pct": 1.0,
                    "trades": 0,
                    "fees_usd": 0.0,
                    "active_cycle_pct": 100.0,
                    "avg_exposure_pct": 10.0,
                },
                {
                    "overlay_mode": "target_scale",
                    "return_pct": 0.0,
                    "max_drawdown_pct": 1.0,
                    "trades": 0,
                    "fees_usd": 0.0,
                    "active_cycle_pct": 100.0,
                    "avg_exposure_pct": 10.0,
                },
            ]
        )

    def _fake_fit(examples, *, params):  # noqa: ANN001
        fit_example_counts.append(len(examples))
        return None

    def _fake_examples(*args, **kwargs):  # noqa: ANN002, ANN003
        # The first window starts earlier but has labels ending after the second
        # window starts, so the second window must not train on it.
        return [
            {
                "features": [1.0],
                "label": 0,
                "label_end_timestamp": int(kwargs["end"].timestamp()),
            }
        ]

    monkeypatch.setattr(
        overlay_research,
        "build_evidence_window_context",
        lambda *args, **kwargs: {"windows": []},
    )
    monkeypatch.setattr(
        overlay_research,
        "_load_window_bars",
        lambda *args, **kwargs: ({}, {"missing_series": [], "partial_series": []}),
    )
    monkeypatch.setattr(
        overlay_research,
        "evaluate_market_regime_exposure_scenarios",
        _fake_evaluate,
    )
    monkeypatch.setattr(overlay_research, "_fit_model", _fake_fit)
    monkeypatch.setattr(overlay_research, "_build_training_examples", _fake_examples)

    result = overlay_research.run_ml_regime_overlay_research(
        cast(Any, SimpleNamespace(universe=SimpleNamespace(include_pairs=["BTC/USD"]))),
        window_sets={
            "overlap": [
                ("earlier", "2026-01-01T00:00:00Z", "2026-01-20T00:00:00Z"),
                ("later", "2026-01-10T00:00:00Z", "2026-01-30T00:00:00Z"),
            ]
        },
        params=MLRegimeOverlayResearchParams(min_training_examples=99),
    )

    assert [window["training_examples_before"] for window in result.windows] == [0, 1]
    assert [window["training_examples_used"] for window in result.windows] == [0, 0]
    assert [
        window["training_examples_excluded_overlap"] for window in result.windows
    ] == [
        0,
        1,
    ]
    assert fit_example_counts == [0, 0]
