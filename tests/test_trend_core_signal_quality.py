from __future__ import annotations

from datetime import UTC, datetime

import pytest

from krakked.backtest.trend_core_signal_quality import (
    build_trend_core_signal_quality_report,
    build_trend_core_signal_quality_window_set_report,
)


def _signal(
    *,
    strength: float,
    return_pct: float,
    adverse_pct: float | None = None,
    timeframe: str = "4h",
    pair: str = "BTC/USD",
) -> dict[str, object]:
    return {
        "timestamp": 1_700_000_000,
        "time": "2023-11-14T22:13:20+00:00",
        "signal_bar_timestamp": 1_700_000_000,
        "signal_bar_time": "2023-11-14T22:13:20+00:00",
        "pair": pair,
        "timeframe": timeframe,
        "intent_type": "enter",
        "confidence": min(1.0, max(0.1, strength / 100.0)),
        "trend_strength_bps": strength,
        "regime": "trending",
        "current_close": 100.0,
        "forward_returns_pct": {"6": return_pct},
        "adverse_excursions_pct": {"6": adverse_pct},
        "metadata": {"trend_strength_bps": strength},
    }


def _window(
    window_id: str,
    *,
    evidence_bucket: str,
    status: str = "candidate_signal",
    strict_data_ready: bool = True,
    evaluable: bool = True,
) -> dict[str, object]:
    return {
        "window_set": "regime_diverse_4h",
        "window_id": window_id,
        "start": "2026-01-01T00:00:00+00:00",
        "end": "2026-01-31T00:00:00+00:00",
        "market_bucket": evidence_bucket,
        "evidence_bucket": evidence_bucket,
        "strict_data_ready": strict_data_ready,
        "evaluable": evaluable,
        "total_signals": 12,
        "status": status,
        "promotion_ready": status == "candidate_signal",
        "gate_reasons": [] if status == "candidate_signal" else ["weak forward edge"],
        "primary_horizon_bars": 6,
        "primary_horizon_stats": {
            "sample_count": 12,
            "mean_return_pct": 0.8 if status == "candidate_signal" else -0.2,
            "hit_rate": 0.75 if status == "candidate_signal" else 0.25,
        },
        "missing_series": [] if strict_data_ready else ["BTC/USD@4h"],
        "partial_series": [],
        "summary": {"status": status},
    }


def test_signal_quality_fails_when_strongest_bucket_underperforms() -> None:
    signals = [
        _signal(strength=float(index), return_pct=0.8 if index < 30 else 0.7)
        for index in range(40)
    ]

    report = build_trend_core_signal_quality_report(
        signals,
        start=datetime(2026, 5, 1, tzinfo=UTC),
        end=datetime(2026, 5, 2, tzinfo=UTC),
        pairs=["BTC/USD"],
        timeframes=["4h"],
        horizons=[6],
        fee_bps=25.0,
        fresh_bars_only=True,
        strict_data=True,
        warmup_days=30.0,
    ).to_report_dict()

    assert report["summary"]["status"] == "edge_not_proven"
    assert report["summary"]["promotion_ready"] is False
    assert any(
        "stronger trend-strength bucket" in reason
        for reason in report["summary"]["gate_reasons"]
    )
    assert report["strongest_vs_weakest"][
        "strongest_minus_weakest_mean_return_pct"
    ] == pytest.approx(-0.1)


def test_signal_quality_can_identify_candidate_signal() -> None:
    signals = [
        _signal(strength=float(index), return_pct=0.7 if index < 10 else 0.9)
        for index in range(40)
    ]

    report = build_trend_core_signal_quality_report(
        signals,
        start=datetime(2026, 5, 1, tzinfo=UTC),
        end=datetime(2026, 5, 2, tzinfo=UTC),
        pairs=["BTC/USD"],
        timeframes=["4h"],
        horizons=[6],
        fee_bps=25.0,
        fresh_bars_only=True,
        strict_data=True,
        warmup_days=30.0,
    ).to_report_dict()

    assert report["summary"]["status"] == "candidate_signal"
    assert report["summary"]["promotion_ready"] is True
    assert report["overall"]["6"]["mean_after_fee_pct"] == pytest.approx(0.35)


def test_signal_quality_reports_all_in_cost_and_adverse_excursion() -> None:
    signals = [
        _signal(
            strength=float(index),
            return_pct=0.7 if index < 10 else 0.9,
            adverse_pct=0.25,
        )
        for index in range(40)
    ]

    report = build_trend_core_signal_quality_report(
        signals,
        start=datetime(2026, 5, 1, tzinfo=UTC),
        end=datetime(2026, 5, 2, tzinfo=UTC),
        pairs=["BTC/USD"],
        timeframes=["4h"],
        horizons=[6],
        fee_bps=25.0,
        fresh_bars_only=True,
        strict_data=True,
        warmup_days=30.0,
    ).to_report_dict()

    summary = report["summary"]
    stats = report["overall"]["6"]
    assert summary["one_way_all_in_cost_bps"] == pytest.approx(25.0)
    assert summary["round_trip_all_in_cost_bps"] == pytest.approx(50.0)
    assert summary["round_trip_all_in_cost_pct"] == pytest.approx(0.5)
    assert summary["round_trip_fee_hurdle_pct"] == pytest.approx(0.5)
    assert "one-way all-in cost proxy" in summary["cost_model_note"]
    assert stats["mean_adverse_excursion_pct"] == pytest.approx(0.25)
    assert stats["median_adverse_excursion_pct"] == pytest.approx(0.25)
    assert stats["max_adverse_excursion_pct"] == pytest.approx(0.25)


def test_window_set_aggregate_requires_all_evaluable_windows_to_pass() -> None:
    result = build_trend_core_signal_quality_window_set_report(
        [
            _window("up", evidence_bucket="uptrend"),
            _window("down", evidence_bucket="downtrend"),
            _window("chop", evidence_bucket="chop_or_transition"),
            _window(
                "weak",
                evidence_bucket="uptrend",
                status="edge_not_proven",
            ),
        ],
        window_context=None,
        window_sets=["regime_diverse_4h"],
        pairs=["BTC/USD", "ETH/USD"],
        timeframes=["4h"],
        horizons=[6],
        fee_bps=25.0,
        fresh_bars_only=True,
        strict_data=True,
        warmup_days=30.0,
    ).to_report_dict()

    summary = result["summary"]
    assert summary["status"] == "edge_not_proven"
    assert summary["passing_window_count"] == 3
    assert summary["evaluable_window_count"] == 4
    assert summary["passing_window_ids"] == ["up", "down", "chop"]
    assert summary["failing_window_ids"] == ["weak"]
    assert summary["regime_coverage_sufficient"] is True
    assert summary["directional_ohlc_lane_verdict"] == (
        "retire_directional_ohlc_on_majors_for_now"
    )


def test_window_set_excludes_current_rolling_windows_from_gate() -> None:
    result = build_trend_core_signal_quality_window_set_report(
        [
            _window("up", evidence_bucket="uptrend"),
            _window("down", evidence_bucket="downtrend"),
            _window("chop", evidence_bucket="chop_or_transition"),
            _window(
                "current",
                evidence_bucket="current_rolling",
                status="edge_not_proven",
                evaluable=False,
            ),
        ],
        window_context=None,
        window_sets=["regime_diverse_4h"],
        pairs=["BTC/USD", "ETH/USD"],
        timeframes=["4h"],
        horizons=[6],
        fee_bps=25.0,
        fresh_bars_only=True,
        strict_data=True,
        warmup_days=30.0,
    ).to_report_dict()

    summary = result["summary"]
    assert summary["status"] == "candidate_signal"
    assert summary["evaluable_window_count"] == 3
    assert summary["passing_window_count"] == 3
    assert summary["failing_window_ids"] == []
    assert "current" not in summary["passing_window_ids"]


def test_window_set_coverage_gap_fails_even_when_not_strict() -> None:
    result = build_trend_core_signal_quality_window_set_report(
        [
            _window("up", evidence_bucket="uptrend"),
            _window("down", evidence_bucket="downtrend"),
            _window("chop", evidence_bucket="chop_or_transition"),
            _window(
                "missing",
                evidence_bucket="uptrend",
                strict_data_ready=False,
                evaluable=False,
            ),
        ],
        window_context=None,
        window_sets=["regime_diverse_4h"],
        pairs=["BTC/USD", "ETH/USD"],
        timeframes=["4h"],
        horizons=[6],
        fee_bps=25.0,
        fresh_bars_only=True,
        strict_data=False,
        warmup_days=30.0,
    ).to_report_dict()

    assert result["summary"]["status"] == "edge_not_proven"
    assert any(
        "strict data coverage" in reason for reason in result["summary"]["gate_reasons"]
    )
