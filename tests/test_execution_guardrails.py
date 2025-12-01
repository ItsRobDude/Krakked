from datetime import UTC, datetime
from unittest.mock import MagicMock

from kraken_bot.config import ExecutionConfig
from kraken_bot.execution.oms import ExecutionService
from kraken_bot.strategy.models import ExecutionPlan, RiskAdjustedAction


def _build_action(pair: str, target_notional: float) -> RiskAdjustedAction:
    return RiskAdjustedAction(
        pair=pair,
        strategy_id="test_strategy",
        action_type="open",
        target_base_size=1.0,
        target_notional_usd=target_notional,
        current_base_size=0.0,
        reason="",
        blocked=False,
        blocked_reasons=[],
        strategy_tag="test_strategy",
        risk_limits_snapshot={},
    )


def test_pair_notional_guardrail_blocks_submission():
    adapter = MagicMock()
    adapter.config = ExecutionConfig(max_pair_notional_usd=500.0)
    adapter.submit_order.side_effect = AssertionError("submit_order should not be called")

    plan = ExecutionPlan(
        plan_id="plan_pair_limit",
        generated_at=datetime.now(UTC),
        actions=[_build_action("XBTUSD", 1000.0)],
        metadata={"risk_status": {"total_exposure_pct": 10.0}},
    )

    market_data = MagicMock()
    market_data.get_best_bid_ask.return_value = {"bid": 10.0, "ask": 11.0}

    service = ExecutionService(adapter, market_data=market_data)
    result = service.execute_plan(plan)

    assert len(result.orders) == 1
    order = result.orders[0]
    assert order.status == "rejected"
    assert "max_pair_notional_usd" in (order.last_error or "")
    assert result.errors


def test_total_notional_guardrail_blocks_submission():
    adapter = MagicMock()
    adapter.config = ExecutionConfig(max_total_notional_usd=1000.0)
    adapter.submit_order.side_effect = AssertionError("submit_order should not be called")

    actions = [_build_action("XBTUSD", 600.0), _build_action("ETHUSD", 700.0)]
    plan = ExecutionPlan(
        plan_id="plan_total_limit",
        generated_at=datetime.now(UTC),
        actions=actions,
        metadata={"risk_status": {"total_exposure_pct": 15.0}},
    )

    market_data = MagicMock()
    market_data.get_best_bid_ask.return_value = {"bid": 10.0, "ask": 11.0}

    service = ExecutionService(adapter, market_data=market_data)
    result = service.execute_plan(plan)

    assert len(result.orders) == 2
    assert all(order.status == "rejected" for order in result.orders)
    assert all("max_total_notional_usd" in (order.last_error or "") for order in result.orders)
    assert len(result.errors) == 2
    assert adapter.submit_order.call_count == 0
