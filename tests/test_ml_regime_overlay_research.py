from __future__ import annotations

import pytest

from krakked.backtest.ml_regime_overlay_research import (
    MLRegimeOverlayResearchParams,
    _best_scale_label,
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
        ],
        params=MLRegimeOverlayResearchParams(),
        timeframe="4h",
    )

    assert summary["regime_coverage_sufficient"] is True
    assert summary["promotion_gate"]["regime_coverage_sufficient"] is True
    assert summary["promotion_gate"]["passed"] is True


def test_ml_regime_overlay_gate_blocks_when_no_uptrend_window() -> None:
    # Even with healthy return/drawdown/exposure metrics, a window set missing an
    # uptrend regime must fail the gate: the verdict is inconclusive without it.
    summary = _summary(  # noqa: SLF001
        [
            _healthy_window("downtrend"),
            _healthy_window("downtrend"),
            _healthy_window("chop_or_transition"),
        ],
        params=MLRegimeOverlayResearchParams(),
        timeframe="4h",
    )

    assert summary["insufficient_regime_coverage"] is True
    assert summary["promotion_gate"]["regime_coverage_sufficient"] is False
    assert summary["promotion_gate"]["not_cash_only"] is True
    assert summary["promotion_gate"]["passed"] is False
