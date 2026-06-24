from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from krakked.backtest.market_regime_exposure import (
    DefensiveBaselineReportResult,
    MarketRegimeExposureScenarioParams,
    evaluate_defensive_baseline_report,
)
from krakked.market_data.models import OHLCBar
from krakked.market_regime import MarketRegimeOverlayParams


def _bars_from_prices(prices: list[float]) -> list[OHLCBar]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    bars: list[OHLCBar] = []
    previous = prices[0]
    for index, close in enumerate(prices):
        ts = int((start + timedelta(hours=4 * index)).timestamp())
        bars.append(
            OHLCBar(
                timestamp=ts,
                open=previous,
                high=max(previous, close),
                low=min(previous, close),
                close=close,
                volume=1_000.0,
            )
        )
        previous = close
    return bars


def _wave(start: float, count: int) -> list[float]:
    prices = [start]
    for index in range(1, count):
        direction = 1.0 if index % 2 == 0 else -1.0
        prices.append(max(prices[-1] * (1.0 + (direction * 0.025)), 1.0))
    return prices


def _report() -> DefensiveBaselineReportResult:
    count = 96
    bars = {
        "BTC/USD": _bars_from_prices(_wave(100.0, count)),
        "ETH/USD": _bars_from_prices(_wave(50.0, count)),
        "SOL/USD": _bars_from_prices(_wave(20.0, count)),
        "ADA/USD": _bars_from_prices(_wave(1.0, count)),
    }
    start = datetime.fromtimestamp(bars["BTC/USD"][0].timestamp, tz=UTC)
    end = datetime.fromtimestamp(bars["BTC/USD"][-1].timestamp, tz=UTC)
    return evaluate_defensive_baseline_report(
        bars,
        start=start,
        end=end,
        pairs=list(bars),
        regime_params=MarketRegimeOverlayParams(
            timeframe="4h",
            momentum_lookback_bars=6,
            basket_momentum_lookback_bars=6,
            volatility_lookback_bars=6,
            drawdown_lookback_bars=6,
        ),
        scenario_params=MarketRegimeExposureScenarioParams(
            allocation_pct=100.0,
            rebalance_interval_bars=6,
            fee_bps=25.0,
            target_lookback_bars=6,
            max_target_pairs=2,
        ),
        window_sets={
            "synthetic": [
                (
                    "full",
                    start.isoformat(),
                    end.isoformat(),
                )
            ]
        },
        rebalance_delta_pct=2.5,
    )


def test_defensive_baseline_reports_matched_static_comparisons() -> None:
    payload = _report().to_report_dict()

    comparisons = payload["matched_exposure_comparisons"]
    assert {item["run_id"] for item in comparisons} >= {
        "equal_weight_ewma_vol_target",
        "inverse_vol_ewma_vol_target",
        "equal_weight_momentum_risk_off",
        "trend_rank_target_scale",
    }
    ewma = next(
        item for item in comparisons if item["run_id"] == "equal_weight_ewma_vol_target"
    )
    assert ewma["dynamic"]["avg_exposure_pct"] == pytest.approx(
        ewma["matched_static"]["allocation_pct"],
        abs=1e-6,
    )
    assert "matched_exposure_gate" in ewma


def test_defensive_baseline_uses_prior_bar_before_first_rebalance() -> None:
    payload = _report().to_report_dict()
    runs = {run["run_id"]: run for run in payload["primary_continuous_span"]["runs"]}

    equal_weight = runs["equal_weight_basket"]
    assert equal_weight["skipped_rebalances"] >= 1
    assert equal_weight["trades"] > 0


def test_defensive_baseline_exposes_static_frontier_and_window_results() -> None:
    payload = _report().to_report_dict()

    frontier = payload["static_exposure_frontier"]
    assert frontier[0]["run_id"] == "static_equal_weight_0"
    assert frontier[-1]["run_id"] == "static_equal_weight_100"
    assert len(frontier) == 11
    assert payload["regime_window_results"][0]["window_set"] == "synthetic"
    assert payload["summary"]["verdict"]["status"] in {
        "baseline_useful",
        "risk_control_tradeoff",
        "not_useful",
        "insufficient_data",
    }
