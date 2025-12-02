from datetime import datetime, timezone

from kraken_bot.config import ExecutionConfig
from kraken_bot.execution.router import build_order_from_plan_action
from kraken_bot.strategy.models import ExecutionPlan, RiskAdjustedAction


def test_local_order_preserves_strategy_id_and_userref():
    action = RiskAdjustedAction(
        pair="XBTUSD",
        strategy_id="trend_core",
        action_type="open",
        target_base_size=1.0,
        target_notional_usd=100.0,
        current_base_size=0.0,
        reason="test",
        blocked=False,
        blocked_reasons=[],
        risk_limits_snapshot={},
        strategy_tag="trend_core",
        userref=42,
    )

    plan = ExecutionPlan(
        plan_id="plan_1",
        generated_at=datetime.now(timezone.utc),
        actions=[action],
        metadata={"order_type": "market"},
    )

    order, warning = build_order_from_plan_action(
        action, plan, market_data=None, config=ExecutionConfig()
    )

    assert warning is None
    assert order.strategy_id == "trend_core"
    assert order.userref == 42
