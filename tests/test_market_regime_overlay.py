from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from krakked.backtest.market_regime_overlay import (
    MarketRegimeOverlayParams,
    MarketRegimeSnapshot,
    apply_market_regime_overlay_to_plan,
    classify_market_regime_snapshot,
    evaluate_market_regime_bars,
)
from krakked.market_data.models import OHLCBar
from krakked.strategy.models import ExecutionPlan, RiskAdjustedAction


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


def _trend(start_price: float, pct_per_bar: float, count: int = 20) -> list[float]:
    prices = [start_price]
    for _ in range(count - 1):
        prices.append(prices[-1] * (1.0 + pct_per_bar))
    return prices


def _params(**overrides: Any) -> MarketRegimeOverlayParams:
    values: dict[str, Any] = {
        "momentum_lookback_bars": 10,
        "basket_momentum_lookback_bars": 10,
        "volatility_lookback_bars": 10,
        "drawdown_lookback_bars": 10,
        "neutral_benchmark_momentum_bps": 100.0,
        "neutral_basket_momentum_bps": 100.0,
        "risk_off_benchmark_momentum_bps": 0.0,
        "risk_off_basket_momentum_bps": 0.0,
        "neutral_benchmark_drawdown_pct": 10.0,
        "risk_off_benchmark_drawdown_pct": 20.0,
        "neutral_volatility_pct": 10.0,
        "risk_off_volatility_pct": 20.0,
    }
    values.update(overrides)
    return MarketRegimeOverlayParams(**values)


def test_params_reject_unsupported_timeframe() -> None:
    with pytest.raises(ValueError, match="Unsupported market regime timeframe"):
        MarketRegimeOverlayParams(timeframe="2h")


def test_classifier_marks_risk_off_when_benchmark_and_basket_are_negative() -> None:
    bars = {
        "BTC/USD": _bars_from_prices(_trend(100.0, -0.01)),
        "ETH/USD": _bars_from_prices(_trend(50.0, -0.008)),
        "SOL/USD": _bars_from_prices(_trend(20.0, -0.006)),
    }
    snapshot = classify_market_regime_snapshot(
        bars,
        timestamp=bars["BTC/USD"][-1].timestamp,
        params=_params(),
    )

    assert snapshot.regime == "risk_off"
    assert snapshot.allocation_multiplier == 0.0
    assert "btc_momentum_negative" in snapshot.reason_codes
    assert "basket_momentum_negative" in snapshot.reason_codes


def test_classifier_marks_neutral_for_mixed_soft_market() -> None:
    bars = {
        "BTC/USD": _bars_from_prices(_trend(100.0, -0.002)),
        "ETH/USD": _bars_from_prices(_trend(50.0, 0.02)),
        "SOL/USD": _bars_from_prices(_trend(20.0, 0.02)),
    }
    snapshot = classify_market_regime_snapshot(
        bars,
        timestamp=bars["BTC/USD"][-1].timestamp,
        params=_params(),
    )

    assert snapshot.regime == "neutral"
    assert snapshot.allocation_multiplier == pytest.approx(0.5)
    assert "btc_momentum_soft" in snapshot.reason_codes


def test_classifier_marks_risk_on_when_benchmark_and_basket_are_strong() -> None:
    bars = {
        "BTC/USD": _bars_from_prices(_trend(100.0, 0.01)),
        "ETH/USD": _bars_from_prices(_trend(50.0, 0.012)),
        "SOL/USD": _bars_from_prices(_trend(20.0, 0.01)),
    }
    snapshot = classify_market_regime_snapshot(
        bars,
        timestamp=bars["BTC/USD"][-1].timestamp,
        params=_params(),
    )

    assert snapshot.regime == "risk_on"
    assert snapshot.allocation_multiplier == 1.0
    assert snapshot.reason_codes == ["risk_on_conditions_met"]


def test_classifier_surfaces_insufficient_data() -> None:
    bars = {
        "BTC/USD": _bars_from_prices([100.0, 101.0]),
        "ETH/USD": _bars_from_prices([50.0, 50.5]),
    }
    snapshot = classify_market_regime_snapshot(
        bars,
        timestamp=bars["BTC/USD"][-1].timestamp,
        params=_params(),
    )

    assert snapshot.regime == "neutral"
    assert snapshot.reason_codes == ["insufficient_data"]


def test_evaluator_reports_state_and_reason_counts() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = start + timedelta(hours=4 * 19)
    result = evaluate_market_regime_bars(
        {
            "BTC/USD": _bars_from_prices(_trend(100.0, -0.01)),
            "ETH/USD": _bars_from_prices(_trend(50.0, -0.008)),
        },
        start=start,
        end=end,
        params=_params(),
    )

    assert result.summary["total_cycles"] == 20
    assert result.summary["state_counts"]["risk_off"] > 0
    assert result.summary["reason_counts"]["btc_momentum_negative"] > 0


def _action(action_type: str, target: float, current: float) -> RiskAdjustedAction:
    return RiskAdjustedAction(
        pair="BTC/USD",
        strategy_id="trend_core",
        action_type=action_type,
        target_base_size=target,
        target_notional_usd=target * 100.0,
        current_base_size=current,
        reason="test action",
        blocked=False,
        blocked_reasons=[],
    )


def test_risk_off_overlay_blocks_new_risk_but_allows_exits() -> None:
    plan = ExecutionPlan(
        plan_id="plan-1",
        generated_at=datetime(2026, 1, 1, tzinfo=UTC),
        actions=[_action("open", 1.0, 0.0), _action("close", 0.0, 1.0)],
    )
    snapshot = MarketRegimeSnapshot(
        timestamp=int(plan.generated_at.timestamp()),
        regime="risk_off",
        allocation_multiplier=0.0,
        reason_codes=["btc_momentum_negative"],
        features={},
    )

    adjusted = apply_market_regime_overlay_to_plan(plan, snapshot)

    assert adjusted.actions[0].blocked is True
    assert "Market regime overlay risk_off" in adjusted.actions[0].reason
    assert adjusted.actions[1].blocked is False
    assert adjusted.actions[1].action_type == "close"
    overlay = adjusted.metadata["market_regime_overlay"]
    assert overlay["overlay_blocked_actions"] == 1
    assert overlay["overlay_clamped_actions"] == 0


def test_neutral_overlay_clamps_new_exposure() -> None:
    plan = ExecutionPlan(
        plan_id="plan-1",
        generated_at=datetime(2026, 1, 1, tzinfo=UTC),
        actions=[_action("open", 1.0, 0.0)],
    )
    snapshot = MarketRegimeSnapshot(
        timestamp=int(plan.generated_at.timestamp()),
        regime="neutral",
        allocation_multiplier=0.5,
        reason_codes=["btc_momentum_soft"],
        features={},
    )

    adjusted = apply_market_regime_overlay_to_plan(plan, snapshot)

    assert adjusted.actions[0].blocked is False
    assert adjusted.actions[0].clamped is True
    assert adjusted.actions[0].target_base_size == pytest.approx(0.5)
    assert adjusted.actions[0].target_notional_usd == pytest.approx(50.0)
    overlay = adjusted.metadata["market_regime_overlay"]
    assert overlay["overlay_blocked_actions"] == 0
    assert overlay["overlay_clamped_actions"] == 1
