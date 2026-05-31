from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from krakked.backtest.target_source_research import (
    TargetSourceResearchParams,
    aggregate_target_source_research_reports,
    evaluate_target_source_scenarios,
    select_target_source_weights,
)
from krakked.market_data.models import OHLCBar


def _timeline(count: int) -> list[int]:
    start = datetime(2026, 5, 1, tzinfo=UTC)
    return [
        int((start + timedelta(hours=4 * index)).timestamp()) for index in range(count)
    ]


def _price_map(prices: list[float]) -> dict[int, float]:
    return {ts: price for ts, price in zip(_timeline(len(prices)), prices)}


def _bars(prices: list[float]) -> list[OHLCBar]:
    return [
        OHLCBar(
            timestamp=ts,
            open=price,
            high=price,
            low=price,
            close=price,
            volume=1.0,
        )
        for ts, price in zip(_timeline(len(prices)), prices)
    ]


def _selector_params(**overrides: Any) -> TargetSourceResearchParams:
    values = {
        "allocation_pct": 20.0,
        "long_lookback_bars": 5,
        "short_lookback_bars": 3,
        "pullback_lookback_bars": 2,
        "max_target_pairs": 2,
        "pullback_overextension_bps": 600.0,
        "oversold_threshold_bps": 250.0,
    }
    values.update(overrides)
    return TargetSourceResearchParams(**values)


def test_rank_top2_selects_highest_momentum_and_caps_pairs() -> None:
    timeline = _timeline(6)
    price_maps = {
        "BTC/USD": _price_map([100, 100, 100, 100, 100, 120]),
        "ETH/USD": _price_map([100, 100, 100, 100, 100, 130]),
        "SOL/USD": _price_map([100, 100, 100, 100, 100, 110]),
    }

    weights = select_target_source_weights(
        "rank_top2",
        pairs=list(price_maps),
        price_maps=price_maps,
        timeline=timeline,
        index=5,
        params=_selector_params(),
    )

    assert list(weights) == ["ETH/USD", "BTC/USD"]
    assert weights == {"ETH/USD": pytest.approx(0.10), "BTC/USD": pytest.approx(0.10)}


def test_dual_momentum_requires_positive_long_and_short_momentum() -> None:
    timeline = _timeline(6)
    price_maps = {
        "BTC/USD": _price_map([100, 100, 100, 120, 119, 118]),
        "ETH/USD": _price_map([100, 100, 100, 105, 110, 115]),
        "SOL/USD": _price_map([100, 100, 100, 95, 94, 93]),
    }

    weights = select_target_source_weights(
        "dual_momentum_top2",
        pairs=list(price_maps),
        price_maps=price_maps,
        timeline=timeline,
        index=5,
        params=_selector_params(),
    )

    assert list(weights) == ["ETH/USD"]


def test_vol_adjusted_dual_momentum_ranks_by_return_over_volatility() -> None:
    timeline = _timeline(6)
    price_maps = {
        "BTC/USD": _price_map([100, 100, 180, 110, 170, 220]),
        "ETH/USD": _price_map([100, 100, 110, 120, 130, 140]),
    }

    weights = select_target_source_weights(
        "vol_adj_dual_momentum_top2",
        pairs=list(price_maps),
        price_maps=price_maps,
        timeline=timeline,
        index=5,
        params=_selector_params(max_target_pairs=1),
    )

    assert list(weights) == ["ETH/USD"]


def test_pullback_guard_rejects_overextended_short_term_moves() -> None:
    timeline = _timeline(6)
    price_maps = {
        "BTC/USD": _price_map([100, 100, 100, 110, 115, 130]),
        "ETH/USD": _price_map([100, 100, 100, 104, 108, 110]),
    }

    weights = select_target_source_weights(
        "pullback_vol_adj_top2",
        pairs=list(price_maps),
        price_maps=price_maps,
        timeline=timeline,
        index=5,
        params=_selector_params(max_target_pairs=1, pullback_overextension_bps=800.0),
    )

    assert list(weights) == ["ETH/USD"]


def test_oversold_reversion_targets_cash_until_threshold_clears() -> None:
    timeline = _timeline(6)
    price_maps = {
        "BTC/USD": _price_map([100, 100, 100, 100, 100, 98]),
        "ETH/USD": _price_map([100, 100, 100, 100, 100, 97]),
    }
    params = _selector_params(oversold_threshold_bps=250.0)

    weights = select_target_source_weights(
        "oversold_reversion_top1",
        pairs=list(price_maps),
        price_maps=price_maps,
        timeline=timeline,
        index=5,
        params=params,
    )

    assert list(weights) == ["ETH/USD"]

    cash_weights = select_target_source_weights(
        "oversold_reversion_top1",
        pairs=list(price_maps),
        price_maps=price_maps,
        timeline=timeline,
        index=4,
        params=params,
    )

    assert cash_weights == {}


@pytest.mark.parametrize(
    "overrides, expected_message",
    [
        ({"allocation_pct": 0.0}, "allocation_pct"),
        ({"timeframe": "1h"}, "timeframe"),
        ({"rebalance_interval_bars": 0}, "rebalance_interval_bars"),
        ({"max_target_pairs": 0}, "max_target_pairs"),
    ],
)
def test_target_source_params_reject_invalid_values(
    overrides: dict[str, Any],
    expected_message: str,
) -> None:
    with pytest.raises(ValueError, match=expected_message):
        TargetSourceResearchParams(**overrides)


def test_unknown_target_source_scenario_fails_clearly() -> None:
    timeline = _timeline(3)
    price_maps = {"BTC/USD": _price_map([100, 101, 102])}

    with pytest.raises(ValueError, match="Unsupported scenario"):
        select_target_source_weights(
            "unknown",
            pairs=list(price_maps),
            price_maps=price_maps,
            timeline=timeline,
            index=2,
            params=_selector_params(),
        )


def test_target_source_run_emits_rebalance_trace_and_diagnostics() -> None:
    start = datetime.fromtimestamp(_timeline(8)[0], tz=UTC)
    end = datetime.fromtimestamp(_timeline(8)[-1], tz=UTC)

    result = evaluate_target_source_scenarios(
        {
            "BTC/USD": _bars([100, 101, 102, 103, 104, 105, 106, 107]),
            "ETH/USD": _bars([100, 100, 101, 101, 102, 102, 103, 103]),
        },
        start=start,
        end=end,
        pairs=["BTC/USD", "ETH/USD"],
        scenarios=["rank_top2"],
        params=_selector_params(rebalance_interval_bars=2),
        preflight={"missing_series": [], "partial_series": []},
    )

    run = result.runs[0]
    trace = run["rebalance_trace"]
    active_trace = next(row for row in trace if row["selected_pairs"])
    assert active_trace["candidate_scores"]["BTC/USD"]["eligible"] is True
    assert active_trace["target_weights"]["BTC/USD"] == pytest.approx(0.10)
    assert active_trace["equity_before_usd"] > 0.0
    assert active_trace["fees_usd"] >= 0.0
    assert active_trace["selected_forward_return_pct"] is not None
    assert run["diagnostics"]["active_rebalance_count"] > 0
    assert "pair_edge_summary" in run["diagnostics"]


def test_target_source_diagnostics_marks_sparse_cash_source() -> None:
    start = datetime.fromtimestamp(_timeline(8)[0], tz=UTC)
    end = datetime.fromtimestamp(_timeline(8)[-1], tz=UTC)

    result = evaluate_target_source_scenarios(
        {
            "BTC/USD": _bars([100, 101, 102, 103, 104, 105, 106, 107]),
            "ETH/USD": _bars([100, 100, 101, 101, 102, 102, 103, 103]),
        },
        start=start,
        end=end,
        pairs=["BTC/USD", "ETH/USD"],
        scenarios=["oversold_reversion_top1"],
        params=_selector_params(rebalance_interval_bars=2),
        preflight={"missing_series": [], "partial_series": []},
    )

    diagnostics = result.runs[0]["diagnostics"]
    assert diagnostics["cash_target_rebalance_pct"] == pytest.approx(100.0)
    assert diagnostics["sparse_exposure"] is True


def test_target_source_diagnostics_reports_hidden_pair_edge() -> None:
    start = datetime.fromtimestamp(_timeline(10)[0], tz=UTC)
    end = datetime.fromtimestamp(_timeline(10)[-1], tz=UTC)

    result = evaluate_target_source_scenarios(
        {
            "BTC/USD": _bars([100, 101, 102, 103, 104, 105, 106, 107, 108, 109]),
            "ETH/USD": _bars([100, 98, 96, 94, 92, 90, 88, 86, 84, 82]),
        },
        start=start,
        end=end,
        pairs=["BTC/USD", "ETH/USD"],
        scenarios=["rank_top2"],
        params=_selector_params(rebalance_interval_bars=2),
        preflight={"missing_series": [], "partial_series": []},
    )

    diagnostics = result.runs[0]["diagnostics"]
    assert diagnostics["pair_level_edge_hidden"] is True
    assert "pair_edge_hidden_inside_bad_allocation" in diagnostics["failure_reasons"]
    assert any(row["pair"] == "BTC/USD" for row in diagnostics["pair_edge_candidates"])


def test_aggregate_gate_compares_against_rank_top2() -> None:
    windows = [
        ("recent_20d", "w1"),
        ("recent_20d", "w2"),
        ("recent_20d", "w3"),
        ("recent_20d", "w4"),
        ("recent_20d", "20260510-20260530"),
    ]
    reports: list[dict[str, Any]] = []
    paths: list[str] = []
    for index, (window_set, window_id) in enumerate(windows):
        reports.append(
            _report(
                window_set=window_set,
                window_id=window_id,
                scenario_id="rank_top2",
                return_pct=0.0,
                max_drawdown_pct=1.0,
            )
        )
        paths.append(f"rank-{index}.json")
        reports.append(
            _report(
                window_set=window_set,
                window_id=window_id,
                scenario_id="dual_momentum_top2",
                return_pct=0.2,
                max_drawdown_pct=0.5,
            )
        )
        paths.append(f"dual-{index}.json")

    aggregate = aggregate_target_source_research_reports(
        reports,
        report_paths=paths,
        save_dir="research",
    )

    groups = aggregate["summary"]["groups"]
    dual = next(
        group for group in groups if group["scenario_id"] == "dual_momentum_top2"
    )
    rank = next(group for group in groups if group["scenario_id"] == "rank_top2")
    assert dual["avg_delta_return_pct_vs_rank_top2"] == pytest.approx(0.2)
    assert dual["avg_delta_max_drawdown_pct_vs_rank_top2"] == pytest.approx(-0.5)
    assert dual["promotion_gate"]["passed"] is True
    assert rank["promotion_gate"]["passed"] is False
    assert aggregate["summary"]["candidate_scenarios"][0]["scenario_id"] == (
        "dual_momentum_top2"
    )


def _report(
    *,
    window_set: str,
    window_id: str,
    scenario_id: str,
    return_pct: float,
    max_drawdown_pct: float,
) -> dict[str, Any]:
    return {
        "report_version": 1,
        "report_type": "target_source_research",
        "generated_at": "2026-05-30T00:00:00+00:00",
        "summary": {
            "research_only": True,
            "runtime_wiring_approved": False,
            "window_set": window_set,
            "window_id": window_id,
            "allocation_pct": 20.0,
        },
        "preflight": {"missing_series": [], "partial_series": []},
        "runs": [
            {
                "scenario_id": scenario_id,
                "research_only": True,
                "runtime_wiring_approved": False,
                "defensive_only": False,
                "return_pct": return_pct,
                "max_drawdown_pct": max_drawdown_pct,
                "trades": 4,
                "fees_usd": 1.0,
                "cash_target_rebalances": 0,
                "active_cycle_pct": 100.0,
                "avg_exposure_pct": 10.0,
                "target_selection_counts": {"BTC/USD": 1},
                "strict_data_ready": True,
            }
        ],
    }
