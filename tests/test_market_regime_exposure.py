from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from krakked.backtest.market_regime_exposure import (
    MarketRegimeExposureScenarioParams,
    evaluate_market_regime_exposure_scenarios,
)
from krakked.backtest.market_regime_overlay import MarketRegimeOverlayParams
from krakked.market_data.models import OHLCBar


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


def _risk_off_prices() -> list[float]:
    prices = [100.0]
    for _ in range(9):
        prices.append(prices[-1] * 1.002)
    for _ in range(30):
        prices.append(prices[-1] * 0.99)
    return prices


def _compound_prices(start_price: float, pct_per_bar: float, count: int) -> list[float]:
    prices = [start_price]
    for _ in range(count - 1):
        prices.append(prices[-1] * (1.0 + pct_per_bar))
    return prices


def _regime_params() -> MarketRegimeOverlayParams:
    return MarketRegimeOverlayParams(
        momentum_lookback_bars=5,
        basket_momentum_lookback_bars=5,
        volatility_lookback_bars=5,
        drawdown_lookback_bars=5,
        neutral_benchmark_momentum_bps=50.0,
        neutral_basket_momentum_bps=50.0,
        risk_off_benchmark_momentum_bps=0.0,
        risk_off_basket_momentum_bps=0.0,
        neutral_benchmark_drawdown_pct=10.0,
        risk_off_benchmark_drawdown_pct=20.0,
        neutral_volatility_pct=10.0,
        risk_off_volatility_pct=20.0,
    )


def test_target_scale_exposure_scenario_reduces_drawdown_in_risk_off() -> None:
    prices = _risk_off_prices()
    bars = {
        "BTC/USD": _bars_from_prices(prices),
        "ETH/USD": _bars_from_prices([price * 0.5 for price in prices]),
    }
    result = evaluate_market_regime_exposure_scenarios(
        bars,
        start=datetime.fromtimestamp(bars["BTC/USD"][0].timestamp, tz=UTC),
        end=datetime.fromtimestamp(bars["BTC/USD"][-1].timestamp, tz=UTC),
        pairs=["BTC/USD", "ETH/USD"],
        regime_params=_regime_params(),
        scenario_params=MarketRegimeExposureScenarioParams(
            allocation_pct=100.0,
            rebalance_interval_bars=1,
            fee_bps=0.0,
        ),
        scenarios=["starter_equal_weight"],
        overlay_modes=["target_scale"],
    )

    comparison = result.comparisons[0]
    assert comparison["delta"]["return_pct"] > 0.0
    assert comparison["delta"]["max_drawdown_pct"] < 0.0
    assert comparison["overlay_interventions"]["overlay_target_reductions"] > 0
    assert comparison["promotion_checks"]["baseline_had_exposure"]["passed"] is True


def test_entry_guard_exposure_scenario_does_not_force_target_reductions() -> None:
    prices = _risk_off_prices()
    bars = {
        "BTC/USD": _bars_from_prices(prices),
        "ETH/USD": _bars_from_prices([price * 0.5 for price in prices]),
    }
    result = evaluate_market_regime_exposure_scenarios(
        bars,
        start=datetime.fromtimestamp(bars["BTC/USD"][0].timestamp, tz=UTC),
        end=datetime.fromtimestamp(bars["BTC/USD"][-1].timestamp, tz=UTC),
        pairs=["BTC/USD", "ETH/USD"],
        regime_params=_regime_params(),
        scenario_params=MarketRegimeExposureScenarioParams(
            allocation_pct=100.0,
            rebalance_interval_bars=1,
            fee_bps=0.0,
        ),
        scenarios=["starter_equal_weight"],
        overlay_modes=["entry_guard"],
    )

    comparison = result.comparisons[0]
    assert comparison["overlay_interventions"]["overlay_target_reductions"] == 0
    assert comparison["overlay"]["active_cycles"] > 0


def test_exposure_research_rejects_unsupported_scenario() -> None:
    bars = {"BTC/USD": _bars_from_prices([100.0, 101.0, 102.0, 103.0, 104.0])}
    with pytest.raises(ValueError, match="Unsupported scenario"):
        evaluate_market_regime_exposure_scenarios(
            bars,
            start=datetime.fromtimestamp(bars["BTC/USD"][0].timestamp, tz=UTC),
            end=datetime.fromtimestamp(bars["BTC/USD"][-1].timestamp, tz=UTC),
            pairs=["BTC/USD"],
            regime_params=_regime_params(),
            scenario_params=MarketRegimeExposureScenarioParams(),
            scenarios=["bad_scenario"],
        )


def test_exposure_scenario_params_reject_invalid_target_inputs() -> None:
    with pytest.raises(ValueError, match="target_lookback_bars"):
        MarketRegimeExposureScenarioParams(target_lookback_bars=1)

    with pytest.raises(ValueError, match="max_target_pairs"):
        MarketRegimeExposureScenarioParams(max_target_pairs=0)


def test_trend_proxy_selects_ranked_pairs_above_momentum_threshold() -> None:
    bars = {
        "BTC/USD": _bars_from_prices(_compound_prices(100.0, 0.01, 12)),
        "ETH/USD": _bars_from_prices(_compound_prices(100.0, 0.03, 12)),
        "SOL/USD": _bars_from_prices(_compound_prices(100.0, 0.02, 12)),
        "ADA/USD": _bars_from_prices(_compound_prices(100.0, 0.001, 12)),
    }
    result = evaluate_market_regime_exposure_scenarios(
        bars,
        start=datetime.fromtimestamp(bars["BTC/USD"][0].timestamp, tz=UTC),
        end=datetime.fromtimestamp(bars["BTC/USD"][-1].timestamp, tz=UTC),
        pairs=["BTC/USD", "ETH/USD", "SOL/USD", "ADA/USD"],
        regime_params=_regime_params(),
        scenario_params=MarketRegimeExposureScenarioParams(
            allocation_pct=20.0,
            rebalance_interval_bars=1,
            fee_bps=0.0,
            target_lookback_bars=4,
            min_momentum_bps=150.0,
            max_target_pairs=2,
        ),
        scenarios=["trend_proxy"],
        overlay_modes=["target_scale"],
    )

    baseline = next(run for run in result.runs if run["overlay_mode"] == "none")
    assert set(baseline["target_selection_counts"]) == {"ETH/USD", "SOL/USD"}
    assert baseline["target_selection_counts"]["ETH/USD"] > 0
    assert baseline["target_selection_counts"]["SOL/USD"] > 0


def test_trend_proxy_targets_cash_when_no_pair_qualifies() -> None:
    bars = {
        "BTC/USD": _bars_from_prices(_compound_prices(100.0, -0.001, 12)),
        "ETH/USD": _bars_from_prices(_compound_prices(100.0, 0.0, 12)),
    }
    result = evaluate_market_regime_exposure_scenarios(
        bars,
        start=datetime.fromtimestamp(bars["BTC/USD"][0].timestamp, tz=UTC),
        end=datetime.fromtimestamp(bars["BTC/USD"][-1].timestamp, tz=UTC),
        pairs=["BTC/USD", "ETH/USD"],
        regime_params=_regime_params(),
        scenario_params=MarketRegimeExposureScenarioParams(
            allocation_pct=20.0,
            rebalance_interval_bars=1,
            fee_bps=0.0,
            target_lookback_bars=4,
            min_momentum_bps=150.0,
            max_target_pairs=4,
        ),
        scenarios=["trend_proxy"],
        overlay_modes=["target_scale"],
    )

    baseline = next(run for run in result.runs if run["overlay_mode"] == "none")
    assert baseline["target_selection_counts"] == {}
    assert baseline["active_cycles"] == 0
    assert baseline["trades"] == 0
    assert baseline["cash_target_rebalances"] > 0


def test_trend_rank_proxy_selects_ranked_pairs_without_positive_threshold() -> None:
    bars = {
        "BTC/USD": _bars_from_prices(_compound_prices(100.0, -0.010, 12)),
        "ETH/USD": _bars_from_prices(_compound_prices(100.0, -0.003, 12)),
        "SOL/USD": _bars_from_prices(_compound_prices(100.0, -0.005, 12)),
        "ADA/USD": _bars_from_prices(_compound_prices(100.0, -0.020, 12)),
    }
    result = evaluate_market_regime_exposure_scenarios(
        bars,
        start=datetime.fromtimestamp(bars["BTC/USD"][0].timestamp, tz=UTC),
        end=datetime.fromtimestamp(bars["BTC/USD"][-1].timestamp, tz=UTC),
        pairs=["BTC/USD", "ETH/USD", "SOL/USD", "ADA/USD"],
        regime_params=_regime_params(),
        scenario_params=MarketRegimeExposureScenarioParams(
            allocation_pct=20.0,
            rebalance_interval_bars=1,
            fee_bps=0.0,
            target_lookback_bars=4,
            min_momentum_bps=10_000.0,
            max_target_pairs=2,
        ),
        scenarios=["trend_rank_proxy"],
        overlay_modes=["target_scale"],
    )

    baseline = next(run for run in result.runs if run["overlay_mode"] == "none")
    assert set(baseline["target_selection_counts"]) == {"ETH/USD", "SOL/USD"}
    assert baseline["active_cycles"] > 0


def test_trend_rank_proxy_uses_partial_lookback_after_two_bars() -> None:
    bars = {
        "BTC/USD": _bars_from_prices([100.0, 101.0, 102.0]),
        "ETH/USD": _bars_from_prices([100.0, 100.5, 101.0]),
    }
    result = evaluate_market_regime_exposure_scenarios(
        bars,
        start=datetime.fromtimestamp(bars["BTC/USD"][0].timestamp, tz=UTC),
        end=datetime.fromtimestamp(bars["BTC/USD"][-1].timestamp, tz=UTC),
        pairs=["BTC/USD", "ETH/USD"],
        regime_params=_regime_params(),
        scenario_params=MarketRegimeExposureScenarioParams(
            allocation_pct=20.0,
            rebalance_interval_bars=1,
            fee_bps=0.0,
            target_lookback_bars=63,
            max_target_pairs=1,
        ),
        scenarios=["trend_rank_proxy"],
        overlay_modes=["target_scale"],
    )

    baseline = next(run for run in result.runs if run["overlay_mode"] == "none")
    assert baseline["target_selection_counts"] == {"BTC/USD": 2}
    assert baseline["cash_target_rebalances"] == 1
