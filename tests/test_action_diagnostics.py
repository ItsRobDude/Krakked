from __future__ import annotations

from datetime import UTC, datetime

import pytest

from krakked.backtest.action_diagnostics import (
    build_strategy_action_diagnostics,
    normalize_action_reason,
)
from krakked.backtest.runner import BacktestResult, BacktestSummary
from krakked.execution.models import ExecutionResult, LocalOrder
from krakked.strategy.models import ExecutionPlan, RiskAdjustedAction


def _action(
    *,
    action_type: str = "open",
    blocked: bool = False,
    clamped: bool = False,
    blocked_reasons: list[str] | None = None,
    reason: str = "Aggregated Intent",
) -> RiskAdjustedAction:
    return RiskAdjustedAction(
        pair="BTC/USD",
        strategy_id="trend_core",
        action_type=action_type,
        target_base_size=1.0,
        target_notional_usd=100.0,
        current_base_size=0.0,
        reason=reason,
        blocked=blocked,
        blocked_reasons=blocked_reasons or [],
        clamped=clamped,
    )


def _filled_order(
    *,
    side: str,
    price: float,
    plan_id: str,
) -> LocalOrder:
    return LocalOrder(
        local_id=f"{plan_id}-{side}",
        plan_id=plan_id,
        strategy_id="trend_core",
        pair="BTC/USD",
        side=side,
        order_type="market",
        requested_base_size=1.0,
        status="filled",
        cumulative_base_filled=1.0,
        avg_fill_price=price,
    )


def test_normalize_action_reason_buckets_amount_specific_budget_reasons() -> None:
    assert (
        normalize_action_reason("Strategy trend_core budget exceeded (748.78 > 501.62)")
        == "Strategy budget exceeded"
    )
    assert (
        normalize_action_reason("Strategy trend_core budget exceeded (999.90 > 500.02)")
        == "Strategy budget exceeded"
    )
    assert normalize_action_reason("Max open positions reached (3)") == (
        "Max open positions reached"
    )


def test_build_strategy_action_diagnostics_summarizes_actions_and_fills() -> None:
    start = datetime(2026, 5, 10, tzinfo=UTC)
    end = datetime(2026, 5, 11, tzinfo=UTC)
    plans = [
        ExecutionPlan(
            plan_id="p1",
            generated_at=start,
            actions=[_action()],
            metadata={
                "strategy_evaluation": {
                    "trend_core": {
                        "cycles_evaluated": 1,
                        "intents_emitted": 2,
                        "actions_after_scoring": 1,
                        "filtered_by_score": 1,
                    }
                }
            },
        ),
        ExecutionPlan(
            plan_id="p2",
            generated_at=end,
            actions=[
                _action(
                    blocked=True,
                    blocked_reasons=[
                        "Strategy trend_core budget exceeded (748.78 > 501.62)"
                    ],
                ),
                _action(
                    action_type="none",
                    reason="Dust: rounded sell volume 0.0 < min_order_size 0.1",
                ),
            ],
        ),
    ]
    executions = [
        ExecutionResult(
            plan_id="p1",
            started_at=start,
            orders=[_filled_order(side="buy", price=100.0, plan_id="p1")],
        ),
        ExecutionResult(
            plan_id="p2",
            started_at=end,
            orders=[_filled_order(side="sell", price=110.0, plan_id="p2")],
        ),
    ]
    summary = BacktestSummary(
        start=start,
        end=end,
        starting_cash_usd=10_000.0,
        ending_equity_usd=10_010.0,
        pairs=["BTC/USD"],
        timeframes=["1h"],
        total_cycles=2,
        total_actions=3,
        blocked_actions=1,
        clamped_actions=0,
        total_orders=2,
        filled_orders=2,
        return_pct=0.1,
        max_drawdown_pct=0.0,
        per_strategy={
            "trend_core": {
                "intents_emitted": 2,
                "actions_after_scoring": 1,
                "filtered_by_score": 1,
            }
        },
        trust_level="decision_helpful",
        trust_note="test",
    )

    report = build_strategy_action_diagnostics(
        BacktestResult(plans=plans, executions=executions, summary=summary),
        fee_bps=0.0,
    ).to_report_dict()

    assert report["summary"]["stage_assessment"] == "filled"
    assert report["summary"]["blocked_reason_buckets"] == {
        "Strategy budget exceeded": 1
    }
    assert report["summary"]["none_reason_buckets"] == {
        "Dust below exchange minimum": 1
    }
    assert report["summary"]["approx_fill_realized_pnl_usd"] == pytest.approx(10.0)
    assert report["pair_diagnostics"][0]["filled_orders"] == 2
    assert report["pair_diagnostics"][0]["approx_realized_pnl_usd"] == pytest.approx(
        10.0
    )
    assert report["strategy_diagnostics"][0]["engine_summary"]["intents_emitted"] == 2
