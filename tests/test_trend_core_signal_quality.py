from __future__ import annotations

from datetime import UTC, datetime

import pytest

from krakked.backtest.trend_core_signal_quality import (
    build_trend_core_signal_quality_report,
)


def _signal(
    *,
    strength: float,
    return_pct: float,
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
        "metadata": {"trend_strength_bps": strength},
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
