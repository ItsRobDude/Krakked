from unittest.mock import MagicMock

from krakked.config import AppConfig
from krakked.market_data.api import MarketDataAPI
from krakked.portfolio.manager import PortfolioService
from krakked.portfolio.models import SpotPosition
from krakked.strategy.engine import StrategyEngine


def test_build_emergency_flatten_plan_skips_dust():
    """Ensure flatten plan skips dust and populates metadata correctly."""
    # Setup
    config = MagicMock(spec=AppConfig)
    config.risk = MagicMock()
    config.strategies = MagicMock()
    config.strategies.configs = {}
    market = MagicMock(spec=MarketDataAPI)
    portfolio = MagicMock(spec=PortfolioService)

    # Metadata Setup
    meta_a = MagicMock()
    meta_a.min_order_size = 1.0
    meta_a.volume_decimals = 1
    meta_a.canonical = "A"

    meta_b = MagicMock()
    meta_b.min_order_size = 1.0
    meta_b.volume_decimals = 1
    meta_b.canonical = "B"

    def get_meta(pair):
        if pair == "A":
            return meta_a
        if pair == "B":
            return meta_b
        raise Exception("Missing")

    market.get_pair_metadata.side_effect = get_meta

    engine = StrategyEngine(config, market, portfolio)

    positions = [
        SpotPosition(
            pair="A",
            base_asset="A",
            quote_asset="USD",
            base_size=10.0,
            avg_entry_price=10.0,
            realized_pnl_base=0.0,
            fees_paid_base=0.0,
            unrealized_pnl_base=0.0,
            current_value_base=100.0,
            strategy_tag="s1",
        ),
        SpotPosition(
            pair="B",
            base_asset="B",
            quote_asset="USD",
            base_size=0.5,
            avg_entry_price=10.0,
            realized_pnl_base=0.0,
            fees_paid_base=0.0,
            unrealized_pnl_base=0.0,
            current_value_base=5.0,
            strategy_tag="s2",
        ),
        SpotPosition(
            pair="C",
            base_asset="C",
            quote_asset="USD",
            base_size=10.0,
            avg_entry_price=10.0,
            realized_pnl_base=0.0,
            fees_paid_base=0.0,
            unrealized_pnl_base=0.0,
            current_value_base=100.0,
            strategy_tag="s3",
        ),
    ]

    plan = engine.build_emergency_flatten_plan(positions)

    # Verify Actions
    assert len(plan.actions) == 1
    assert plan.actions[0].pair == "A"
    assert plan.actions[0].action_type == "close"

    # Verify Metadata
    assert plan.metadata["order_type"] == "market"
    assert plan.metadata["dust_count_total"] == 1
    assert plan.metadata["untradeable_count_total"] == 1

    assert plan.metadata["dust_positions"][0]["pair"] == "B"
    assert "Dust:" in plan.metadata["dust_positions"][0]["reason"]

    assert plan.metadata["untradeable_positions"][0]["pair"] == "C"
    assert (
        "Missing pair metadata" in plan.metadata["untradeable_positions"][0]["reason"]
    )
