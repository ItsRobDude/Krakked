from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from krakked.backtest.pair_local_source_research import (
    PairLocalSourceResearchParams,
    aggregate_pair_local_source_research_reports,
    evaluate_pair_local_source_scenarios,
    pair_local_signal_active,
)
from krakked.market_data.models import OHLCBar


def _timeline(count: int) -> list[int]:
    start = datetime(2026, 5, 1, tzinfo=UTC)
    return [
        int((start + timedelta(hours=4 * index)).timestamp()) for index in range(count)
    ]


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


def _params(**overrides: Any) -> PairLocalSourceResearchParams:
    values = {
        "allocation_pct": 20.0,
        "long_lookback_bars": 5,
        "short_lookback_bars": 3,
        "pullback_lookback_bars": 2,
        "pullback_min_bps": 50.0,
        "pullback_max_bps": 400.0,
        "oversold_threshold_bps": 250.0,
        "breakout_min_bps": 150.0,
        "breakout_max_bps": 600.0,
    }
    values.update(overrides)
    return PairLocalSourceResearchParams(**values)


def test_pair_local_signal_rules_require_pair_local_setups() -> None:
    params = _params()
    active, reasons = pair_local_signal_active(
        "pair_dual_momentum",
        features={
            "long_momentum_bps": 500.0,
            "short_momentum_bps": 100.0,
            "pullback_momentum_bps": 25.0,
            "realized_volatility_bps": 100.0,
            "vol_adjusted_score": 5.0,
        },
        params=params,
    )
    assert active is True
    assert reasons == []

    inactive, reasons = pair_local_signal_active(
        "pair_trend_pullback",
        features={
            "long_momentum_bps": 500.0,
            "short_momentum_bps": 100.0,
            "pullback_momentum_bps": 150.0,
            "realized_volatility_bps": 100.0,
            "vol_adjusted_score": 5.0,
        },
        params=params,
    )
    assert inactive is False
    assert "pullback_not_in_entry_band" in reasons

    oversold, _ = pair_local_signal_active(
        "pair_oversold_reversion",
        features={
            "long_momentum_bps": -100.0,
            "short_momentum_bps": -200.0,
            "pullback_momentum_bps": -300.0,
            "realized_volatility_bps": 100.0,
            "vol_adjusted_score": -1.0,
        },
        params=params,
    )
    assert oversold is True


def test_pair_local_research_emits_pair_runs_traces_and_diagnostics() -> None:
    start = datetime.fromtimestamp(_timeline(10)[0], tz=UTC)
    end = datetime.fromtimestamp(_timeline(10)[-1], tz=UTC)

    result = evaluate_pair_local_source_scenarios(
        {
            "BTC/USD": _bars([100, 101, 102, 103, 104, 105, 106, 107, 108, 109]),
            "ETH/USD": _bars([100, 100, 100, 101, 101, 102, 102, 103, 103, 104]),
        },
        start=start,
        end=end,
        pairs=["BTC/USD", "ETH/USD"],
        scenarios=["pair_dual_momentum"],
        params=_params(rebalance_interval_bars=2),
        preflight={"missing_series": [], "partial_series": []},
    )

    assert len(result.runs) == 2
    run = next(item for item in result.runs if item["pair"] == "BTC/USD")
    assert run["research_only"] is True
    assert run["runtime_wiring_approved"] is False
    assert run["rebalance_trace"]
    assert "features" in run["rebalance_trace"][-1]
    assert "avg_active_forward_return_pct" in run["diagnostics"]


def test_pair_local_aggregate_requires_both_window_sets() -> None:
    reports: list[dict[str, Any]] = []
    paths: list[str] = []
    for index, (window_set, window_id) in enumerate(
        [
            ("recent_20d", "w1"),
            ("recent_20d", "w2"),
            ("recent_20d", "w3"),
            ("recent_20d", "w4"),
            ("recent_20d", "20260510-20260530"),
            ("long_4h", "l1"),
            ("long_4h", "l2"),
            ("long_4h", "l3"),
            ("long_4h", "l4"),
            ("long_4h", "l5"),
            ("long_4h", "l6"),
        ]
    ):
        reports.append(
            _report(
                window_set=window_set,
                window_id=window_id,
                return_pct=0.2,
                max_drawdown_pct=0.5,
            )
        )
        paths.append(f"{index}.json")

    aggregate = aggregate_pair_local_source_research_reports(
        reports,
        report_paths=paths,
        save_dir="pair-local",
    )

    assert aggregate["summary"]["promote_pair_local_source"] is True
    assert aggregate["summary"]["runtime_wiring_approved"] is False
    assert aggregate["summary"]["candidate_sources"][0]["pair"] == "BTC/USD"


def _report(
    *,
    window_set: str,
    window_id: str,
    return_pct: float,
    max_drawdown_pct: float,
) -> dict[str, Any]:
    return {
        "report_version": 1,
        "report_type": "pair_local_source_research",
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
                "scenario_id": "pair_dual_momentum",
                "pair": "BTC/USD",
                "research_only": True,
                "runtime_wiring_approved": False,
                "return_pct": return_pct,
                "gross_return_before_fees_pct": return_pct + 0.05,
                "max_drawdown_pct": max_drawdown_pct,
                "trades": 2,
                "fees_usd": 1.0,
                "fee_drag_pct_of_starting_cash": 0.01,
                "active_cycle_pct": 50.0,
                "active_rebalance_pct": 50.0,
                "avg_exposure_pct": 10.0,
                "strict_data_ready": True,
                "diagnostics": {"failure_reasons": []},
            }
        ],
    }
