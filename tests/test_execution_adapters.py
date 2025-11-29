import pytest
from datetime import datetime
from unittest.mock import MagicMock

from kraken_bot.config import ExecutionConfig
from kraken_bot.execution.adapter import KrakenExecutionAdapter, PaperExecutionAdapter
from kraken_bot.execution.oms import ExecutionService
from kraken_bot.strategy.models import ExecutionPlan, RiskAdjustedAction


def _action(pair: str, base_size: float = 1.0, price: float = 30.0) -> RiskAdjustedAction:
    return RiskAdjustedAction(
        pair=pair,
        strategy_id="test_strategy",
        action_type="open",
        target_base_size=base_size,
        target_notional_usd=base_size * price,
        current_base_size=0.0,
        reason="",
        blocked=False,
        blocked_reasons=[],
        strategy_tag="test_strategy",
        risk_limits_snapshot={},
    )


def _plan(action: RiskAdjustedAction) -> ExecutionPlan:
    return ExecutionPlan(
        plan_id="plan",
        generated_at=datetime.utcnow(),
        actions=[action],
        metadata={"order_type": "limit", "requested_price": 30.0},
    )


def test_execution_service_uses_paper_adapter_for_paper_mode():
    config = ExecutionConfig(mode="paper", validate_only=False)
    client = MagicMock()

    service = ExecutionService(config=config, client=client)

    assert isinstance(service.adapter, PaperExecutionAdapter)

    plan = _plan(_action("XBTUSD"))
    result = service.execute_plan(plan)

    assert result.success
    assert result.orders[0].status == "filled"
    assert result.orders[0].cumulative_base_filled == pytest.approx(1.0)
    client.add_order.assert_not_called()


def test_execution_service_uses_kraken_adapter_for_live_mode():
    config = ExecutionConfig(mode="live", validate_only=False, allow_live_trading=True)
    client = MagicMock()
    client.add_order.return_value = {"txid": ["ABC123"], "error": []}

    service = ExecutionService(config=config, client=client)

    assert isinstance(service.adapter, KrakenExecutionAdapter)

    plan = _plan(_action("XBTUSD", price=25.0))
    result = service.execute_plan(plan)

    assert result.orders[0].status in {"open", "validated"}
    client.add_order.assert_called_once()
