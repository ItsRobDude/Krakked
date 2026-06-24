from __future__ import annotations

from datetime import UTC, datetime

import pytest

from krakked.backtest.runner import BacktestPreflight, backtest_strict_data_details
from krakked.backtest.trend_core_signal_quality import (
    _collect_baseline_rows,
    _forward_metrics,
    _window_summary_row,
    build_trend_core_signal_quality_report,
    build_trend_core_signal_quality_window_set_report,
)
from krakked.market_data.ohlc_fetcher import TIMEFRAME_MAP


class _Bar:
    def __init__(
        self,
        timestamp: int,
        open_: float,
        high: float,
        low: float,
        close: float,
    ) -> None:
        self.timestamp = timestamp
        self.open = open_
        self.high = high
        self.low = low
        self.close = close


class _FakeMarketData:
    """Minimal market data exposing only ``get_bar_at_or_after``.

    The signal-quality forward-metric helpers read bars exclusively through this
    method, so a sorted-by-timestamp list per series is enough to exercise the
    next-bar-open entry, exact-horizon exit, and baseline enumeration.
    """

    def __init__(self, bars_by_key: dict[tuple[str, str], list[_Bar]]) -> None:
        self._bars = bars_by_key

    def get_bar_at_or_after(
        self, pair: str, timeframe: str, timestamp: int
    ) -> _Bar | None:
        for bar in self._bars.get((pair, timeframe), []):
            if int(bar.timestamp) >= int(timestamp):
                return bar
        return None


def _baseline_row(return_pct: float) -> dict[str, object]:
    return {
        "pair": "BTC/USD",
        "timeframe": "4h",
        "forward_returns_pct": {"6": return_pct},
        "adverse_excursions_pct": {"6": None},
    }


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
    cleared = status == "candidate_signal"
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
        "promotion_ready": False,
        "baseline_controlled": cleared,
        "gate_reasons": [] if cleared else ["weak forward edge"],
        "primary_horizon_bars": 6,
        "primary_horizon_stats": {
            "sample_count": 12,
            "mean_return_pct": 0.8 if cleared else -0.2,
            "hit_rate": 0.75 if cleared else 0.25,
        },
        "missing_series": [] if strict_data_ready else ["BTC/USD@4h"],
        "partial_series": [],
        "summary": {"status": status, "baseline_controlled": cleared},
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


def test_signal_quality_heuristic_pass_is_unverified_not_promotable() -> None:
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

    summary = report["summary"]
    # The signal clears every heuristic gate, but no unconditional baseline was
    # supplied, so drift cannot be controlled: it must surface as an unverified
    # diagnostic, never a promotable candidate.
    assert summary["status"] == "diagnostic_candidate_unverified"
    assert summary["promotion_ready"] is False
    assert summary["baseline_controlled"] is False
    assert summary["promotion_blocked_reason"] == "baseline_control_unavailable"
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
    assert summary["slippage_bps"] == pytest.approx(0.0)
    assert summary["round_trip_all_in_cost_bps"] == pytest.approx(50.0)
    assert summary["round_trip_all_in_cost_pct"] == pytest.approx(0.5)
    assert summary["round_trip_fee_hurdle_pct"] == pytest.approx(0.5)
    assert "next-bar-open entry" in summary["cost_model_note"]
    assert stats["mean_adverse_excursion_pct"] == pytest.approx(0.25)
    assert stats["median_adverse_excursion_pct"] == pytest.approx(0.25)
    assert stats["max_adverse_excursion_pct"] == pytest.approx(0.25)
    # Adverse excursion is reported but not gated.
    assert stats["adverse_excursion_gated"] is False


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
    # Every evaluable window is a baseline-controlled candidate, so the aggregate
    # is a candidate_signal across the regime mix. promotion_ready stays False:
    # out-of-sample validation is the next gate, not this tool.
    assert summary["status"] == "candidate_signal"
    assert summary["promotion_ready"] is False
    assert summary["baseline_controlled"] is True
    assert summary["promotion_blocked_reason"] == "needs_out_of_sample_validation"
    assert summary["consistency_rule"] == "n_of_n"
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


def test_forward_metrics_enters_next_bar_open_not_signal_close() -> None:
    frame = int(TIMEFRAME_MAP["4h"]) * 60
    t0 = 1_700_000_000
    bars = [
        _Bar(t0, 100.0, 101.0, 99.0, 100.0),  # signal bar (close 100)
        _Bar(t0 + frame, 110.0, 130.0, 105.0, 120.0),  # entry bar (open 110)
    ]
    market_data = _FakeMarketData({("BTC/USD", "4h"): bars})

    entry_price, forward_returns, adverse = _forward_metrics(
        market_data,  # type: ignore[arg-type]
        pair="BTC/USD",
        timeframe="4h",
        signal_bar_ts=t0,
        horizons=[1],
        frame_seconds=frame,
    )

    # Entry is the next bar's open (110), never the signal bar's own close (100).
    assert entry_price == pytest.approx(110.0)
    # horizon=1 exits at the entry bar's close: (120 - 110) / 110.
    assert forward_returns["1"] == pytest.approx((120.0 - 110.0) / 110.0 * 100.0)
    # Adverse excursion from the entry open down to the held low (105).
    assert adverse["1"] == pytest.approx((110.0 - 105.0) / 110.0 * 100.0)


def test_forward_metrics_rejects_missing_exact_horizon_bar() -> None:
    frame = int(TIMEFRAME_MAP["4h"]) * 60
    t0 = 1_700_000_000
    bars = [
        _Bar(t0, 100.0, 101.0, 99.0, 100.0),  # signal bar
        _Bar(t0 + frame, 110.0, 130.0, 105.0, 120.0),  # entry bar (t1)
        # Gap: no bar at t0 + 2*frame.
        _Bar(t0 + 3 * frame, 200.0, 210.0, 190.0, 205.0),  # later bar (t3)
    ]
    market_data = _FakeMarketData({("BTC/USD", "4h"): bars})

    _entry, forward_returns, adverse = _forward_metrics(
        market_data,  # type: ignore[arg-type]
        pair="BTC/USD",
        timeframe="4h",
        signal_bar_ts=t0,
        horizons=[1, 2],
        frame_seconds=frame,
    )

    # horizon=1 lands on the entry bar and is fine.
    assert forward_returns["1"] is not None
    # horizon=2's exact exit bar is missing: the sample is dropped, never
    # stretched to the later t3 bar.
    assert forward_returns["2"] is None
    assert adverse["2"] is None


def test_collect_baseline_rows_enumerates_every_bar_next_open() -> None:
    frame = int(TIMEFRAME_MAP["4h"]) * 60
    t0 = 1_700_000_000
    # Five bars; each opens flat and closes +1% so the unconditional drift is +1%.
    bars = [_Bar(t0 + index * frame, 100.0, 101.5, 99.5, 101.0) for index in range(5)]
    market_data = _FakeMarketData({("BTC/USD", "4h"): bars})
    start = datetime.fromtimestamp(t0, tz=UTC)
    end = datetime.fromtimestamp(t0 + 4 * frame, tz=UTC)

    rows = _collect_baseline_rows(
        market_data,  # type: ignore[arg-type]
        start=start,
        end=end,
        pairs=["BTC/USD"],
        timeframes=["4h"],
        horizons=[1],
    )

    # One baseline row per bar in [start, end].
    assert len(rows) == 5
    # Entry = next bar open (100), exit = that bar's close (101): +1%.
    realized = [
        row["forward_returns_pct"]["1"]
        for row in rows
        if row["forward_returns_pct"]["1"] is not None
    ]
    # The last bar has no next bar, so its forward return is None (dropped).
    assert len(realized) == 4
    assert all(value == pytest.approx(1.0) for value in realized)


def test_signal_must_beat_baseline_to_be_candidate() -> None:
    # Heuristics all pass and the signal is net-positive, but it only edges the
    # unconditional baseline by 0.2pct (< the 0.5pct round-trip margin). A signal
    # that merely rides drift must NOT be promoted to candidate_signal.
    signals = [
        _signal(strength=float(index), return_pct=1.0 if index < 20 else 1.4)
        for index in range(40)
    ]
    baseline_rows = [_baseline_row(1.0) for _ in range(40)]

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
        baseline_rows=baseline_rows,
    ).to_report_dict()

    summary = report["summary"]
    assert summary["status"] == "edge_not_proven"
    assert summary["baseline_controlled"] is True
    assert summary["promotion_ready"] is False
    assert summary["baseline_mean_return_pct"] == pytest.approx(1.0)
    assert summary["signal_minus_baseline_mean_pct"] == pytest.approx(0.2)
    assert any(
        "does not beat the unconditional baseline" in reason
        for reason in summary["gate_reasons"]
    )


def test_baseline_controlled_candidate_when_signal_beats_baseline() -> None:
    # Same signal, but now the unconditional baseline is flat (0%), so the signal
    # beats it by 1.2pct (> the 0.5pct round-trip margin): a genuine
    # baseline-controlled candidate. promotion_ready still stays False.
    signals = [
        _signal(strength=float(index), return_pct=1.0 if index < 20 else 1.4)
        for index in range(40)
    ]
    baseline_rows = [_baseline_row(0.0) for _ in range(40)]

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
        baseline_rows=baseline_rows,
    ).to_report_dict()

    summary = report["summary"]
    assert summary["status"] == "candidate_signal"
    assert summary["baseline_controlled"] is True
    assert summary["promotion_ready"] is False
    assert summary["promotion_blocked_reason"] == "needs_out_of_sample_validation"
    assert summary["signal_minus_baseline_mean_pct"] == pytest.approx(1.2)
    assert summary["gate_reasons"] == []


def test_strict_data_details_flags_warmup_gaps_from_dict() -> None:
    # A serialized preflight dict with only a warmup gap must still surface a
    # strict-data detail; the window-set wrapper depends on this.
    details = backtest_strict_data_details({"warmup_partial_series": ["BTC/USD@1h"]})
    assert details == ["warmup partial: BTC/USD@1h"]


def test_strict_data_details_accepts_dataclass_unchanged() -> None:
    preflight = BacktestPreflight(warmup_missing_series=["ETH/USD@4h"])
    assert backtest_strict_data_details(preflight) == ["warmup missing: ETH/USD@4h"]


def _window_payload(
    *,
    status: str = "candidate_signal",
    warmup_partial_series: list[str] | None = None,
) -> dict[str, object]:
    return {
        "summary": {
            "total_signals": 40,
            "status": status,
            "promotion_ready": False,
            "baseline_controlled": status == "candidate_signal",
            "gate_reasons": [],
        },
        "overall": {"6": {"mean_return_pct": 0.8, "sample_count": 40}},
        "preflight": {
            "missing_series": [],
            "partial_series": [],
            "warmup_missing_series": [],
            "warmup_partial_series": list(warmup_partial_series or []),
        },
    }


def _summary_row(
    window_id: str, bucket: str, **payload_kwargs: object
) -> dict[str, object]:
    return _window_summary_row(
        window_set="regime_diverse_4h",
        window_id=window_id,
        start=datetime(2026, 1, 1, tzinfo=UTC),
        end=datetime(2026, 1, 31, tzinfo=UTC),
        payload=_window_payload(**payload_kwargs),  # type: ignore[arg-type]
        window_context={"evidence_bucket": bucket, "market_bucket": bucket},
        primary_horizon=6,
    )


def test_window_summary_row_warmup_gap_is_not_evaluable() -> None:
    # Full evaluation-window coverage but a warmup gap must mark the window
    # non-evaluable so it cannot become a baseline-controlled candidate.
    row = _summary_row("w1", "uptrend", warmup_partial_series=["BTC/USD@1h"])

    assert row["strict_data_ready"] is False
    assert row["evaluable"] is False
    assert row["warmup_partial_series"] == ["BTC/USD@1h"]
    # The per-window status is preserved verbatim; the aggregate gate handles it.
    assert row["status"] == "candidate_signal"


def test_window_summary_row_clean_window_is_evaluable() -> None:
    row = _summary_row("w1", "uptrend")

    assert row["strict_data_ready"] is True
    assert row["evaluable"] is True
    assert row["warmup_partial_series"] == []


def test_window_set_aggregate_fails_on_warmup_only_gap() -> None:
    # End-to-end: a non-current window with only a warmup gap must drop the
    # aggregate to edge_not_proven, even though every evaluable window passes.
    result = build_trend_core_signal_quality_window_set_report(
        [
            _summary_row("up", "uptrend", warmup_partial_series=["BTC/USD@1h"]),
            _summary_row("down", "downtrend"),
            _summary_row("chop", "chop_or_transition"),
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
    assert any("strict data coverage" in reason for reason in summary["gate_reasons"])
    assert "up" not in summary["passing_window_ids"]
