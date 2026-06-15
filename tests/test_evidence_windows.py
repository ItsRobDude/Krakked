from __future__ import annotations

from krakked.backtest.evidence_windows import (
    EVIDENCE_WINDOW_SET_TUPLES,
    EVIDENCE_WINDOWS,
    evidence_window_tuples,
    parse_evidence_datetime,
    summarize_regime_coverage,
)


def test_evidence_windows_centralize_existing_and_regime_diverse_sets() -> None:
    assert "recent_20d" in EVIDENCE_WINDOWS
    assert "long_4h" in EVIDENCE_WINDOWS
    assert "regime_diverse_4h" in EVIDENCE_WINDOWS

    tuples = evidence_window_tuples(["regime_diverse_4h"])

    assert (
        tuples["regime_diverse_4h"] == EVIDENCE_WINDOW_SET_TUPLES["regime_diverse_4h"]
    )
    assert any(
        window_id == "20260510-20260530"
        for window_id, _start, _end in tuples["regime_diverse_4h"]
    )


def test_parse_evidence_datetime_normalizes_z_to_utc() -> None:
    parsed = parse_evidence_datetime("2026-05-10T00:00:00Z")

    assert parsed.tzinfo is not None
    assert parsed.isoformat() == "2026-05-10T00:00:00+00:00"


def test_summarize_regime_coverage_requires_all_three_regimes() -> None:
    counts, sufficient = summarize_regime_coverage(
        ["uptrend", "downtrend", "chop_or_transition", "current_rolling"]
    )

    assert counts == {
        "chop_or_transition": 1,
        "current_rolling": 1,
        "downtrend": 1,
        "uptrend": 1,
    }
    assert sufficient is True


def test_summarize_regime_coverage_flags_down_chop_only_as_insufficient() -> None:
    # A set missing any uptrend window must be flagged insufficient regardless of
    # how many down/chop windows it has; current_rolling/None never count.
    counts, sufficient = summarize_regime_coverage(
        ["downtrend", "downtrend", "chop_or_transition", None, "current_rolling"]
    )

    assert sufficient is False
    assert counts["downtrend"] == 2
