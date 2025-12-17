from datetime import UTC, datetime
from unittest.mock import MagicMock

from kraken_bot.config import ExecutionConfig
from kraken_bot.execution.oms import ExecutionService
from kraken_bot.market_data.models import PairMetadata
from kraken_bot.portfolio.models import AssetValuation, PortfolioSnapshot
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


def _pair_metadata(pair: str = "XBTUSD") -> PairMetadata:
    base, quote = pair[:3], pair[3:]
    rest_symbol = f"{base}/{quote}"
    return PairMetadata(
        canonical=pair,
        base=base,
        quote=quote,
        rest_symbol=rest_symbol,
        ws_symbol=rest_symbol,
        raw_name=pair,
        price_decimals=1,
        volume_decimals=8,
        lot_size=0.00000001,
        min_order_size=0.0001,
        status="online",
    )


def test_portfolio_snapshot_selected_near_plan_time() -> None:
    """Guardrail passive-exposure math should use the snapshot closest to plan time."""

    adapter = MagicMock()
    adapter.config = ExecutionConfig(max_total_notional_usd=1500.0, validate_only=True)
    adapter.submit_order.side_effect = AssertionError(
        "submit_order should not be called when total notional guardrail trips"
    )

    plan = ExecutionPlan(
        plan_id="plan_snapshot_choice",
        generated_at=datetime.now(UTC),
        actions=[_build_action("XBTUSD", 600.0)],
        metadata={"risk_status": {"total_exposure_pct": 10.0}},
    )

    plan_ts = int(plan.generated_at.timestamp())

    # A far-away snapshot that would *not* trip the guardrail (passive exposure ~0).
    snapshot_far = PortfolioSnapshot(
        timestamp=plan_ts - 1000,
        equity_base=0.0,
        cash_base=0.0,
        asset_valuations=[],
        realized_pnl_base_total=0.0,
        unrealized_pnl_base_total=0.0,
        realized_pnl_base_by_pair={},
        unrealized_pnl_base_by_pair={},
    )

    # A near snapshot that *does* trip the guardrail (passive exposure ~1000).
    snapshot_close = PortfolioSnapshot(
        timestamp=plan_ts,
        equity_base=0.0,
        cash_base=0.0,
        asset_valuations=[
            AssetValuation(
                asset="ETH",
                amount=0.5,
                value_base=1000.0,
                source_pair="ETHUSD",
            )
        ],
        realized_pnl_base_total=0.0,
        unrealized_pnl_base_total=0.0,
        realized_pnl_base_by_pair={},
        unrealized_pnl_base_by_pair={},
    )

    store = MagicMock()
    # Intentionally unsorted to ensure we pick by timestamp proximity, not list order.
    store.get_snapshots.return_value = [snapshot_far, snapshot_close]

    market_data = MagicMock()
    market_data.get_best_bid_ask.return_value = {"bid": 10.0, "ask": 11.0}
    market_data.get_pair_metadata_or_raise.side_effect = lambda pair: _pair_metadata(
        pair
    )

    risk_provider = MagicMock()
    risk_provider.return_value.kill_switch_active = False

    service = ExecutionService(
        adapter=adapter,
        store=store,
        market_data=market_data,
        risk_status_provider=risk_provider,
    )
    result = service.execute_plan(plan)

    assert len(result.orders) == 1
    order = result.orders[0]
    assert order.status == "rejected"
    assert "max_total_notional_usd" in (order.last_error or "")
