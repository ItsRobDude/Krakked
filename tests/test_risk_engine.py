from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from krakked.config import RiskConfig
from krakked.market_data.exceptions import DataStaleError
from krakked.portfolio.models import SpotPosition
from krakked.strategy.models import StrategyIntent
from krakked.strategy.risk import RiskContext, RiskEngine
from tests.runtime_mocks import make_portfolio_service_mock


def _build_risk_context(open_positions=None):
    open_positions = open_positions or []
    return RiskContext(
        equity_usd=1000.0,
        realized_pnl_usd=0.0,
        unrealized_pnl_usd=0.0,
        total_exposure_usd=0.0,
        total_exposure_pct=0.0,
        manual_exposure_usd=0.0,
        manual_exposure_pct=0.0,
        per_strategy_exposure_usd={},
        per_strategy_exposure_pct={},
        open_positions=open_positions,
        asset_exposures=[],
        manual_positions=[],
        manual_positions_included=True,
        drift_flag=False,
        daily_drawdown_pct=0.0,
    )


def _build_portfolio_mock():
    return make_portfolio_service_mock(equity_base=1000.0, cash_base=1000.0)


def _intent(strategy_id: str, pair: str, intent_type: str, desired_usd: float = 0.0):
    return StrategyIntent(
        strategy_id=strategy_id,
        pair=pair,
        side="long",
        intent_type=intent_type,
        desired_exposure_usd=desired_usd,
        confidence=1.0,
        timeframe="1h",
        generated_at=datetime.now(timezone.utc),
    )


def test_kill_switch_uses_zero_price_on_stale_data():
    market_data = MagicMock()
    market_data.get_latest_price.side_effect = [
        DataStaleError("XBTUSD", 120.0, 60.0),
        DataStaleError("XBTUSD", 120.0, 60.0),
    ]

    portfolio = _build_portfolio_mock()

    engine = RiskEngine(RiskConfig(), market_data, portfolio)
    engine.set_manual_kill_switch(True)

    intents = [
        _intent("s1", "XBTUSD", "enter"),
        _intent("s1", "XBTUSD", "reduce", desired_usd=50.0),
    ]

    actions = engine.process_intents(intents)

    assert len(actions) == 2
    assert actions[0].blocked is True
    assert actions[0].target_notional_usd == 0.0
    assert actions[1].blocked is False
    assert actions[1].target_base_size == 0.0


def test_kill_switch_handles_unexpected_price_errors():
    market_data = MagicMock()
    market_data.get_latest_price.side_effect = Exception("boom")

    portfolio = _build_portfolio_mock()

    engine = RiskEngine(RiskConfig(), market_data, portfolio)
    ctx = _build_risk_context(
        open_positions=[
            SpotPosition(
                pair="ETHUSD",
                base_asset="ETH",
                quote_asset="USD",
                base_size=2.0,
                avg_entry_price=1500.0,
                realized_pnl_base=0.0,
                fees_paid_base=0.0,
            )
        ]
    )

    actions = engine._block_all_opens(
        [_intent("s1", "ETHUSD", "enter")], ctx, "Kill Switch Active"
    )

    assert len(actions) == 1
    assert actions[0].target_notional_usd == 0.0
    assert actions[0].blocked is True


def test_manual_positions_excluded_from_limits():
    market_data = MagicMock()
    market_data.get_latest_price.side_effect = lambda pair: {
        "MANUSD": 50.0,
        "STRATUSD": 200.0,
    }[pair]

    portfolio = _build_portfolio_mock()

    manual_position = SpotPosition(
        pair="MANUSD",
        base_asset="MAN",
        quote_asset="USD",
        base_size=2.0,
        avg_entry_price=40.0,
        realized_pnl_base=0.0,
        fees_paid_base=0.0,
    )

    strategy_position = SpotPosition(
        pair="STRATUSD",
        base_asset="STRA",
        quote_asset="USD",
        base_size=1.0,
        avg_entry_price=150.0,
        realized_pnl_base=0.0,
        fees_paid_base=0.0,
        strategy_tag="trend",
    )

    portfolio.get_positions.return_value = [manual_position, strategy_position]

    # Pre-calculate current value since RiskEngine expects it populated (usually by Portfolio.get_equity)
    manual_position.current_value_base = 2.0 * 50.0  # 100
    strategy_position.current_value_base = 1.0 * 200.0  # 200

    include_config = RiskConfig(include_manual_positions=True)
    include_engine = RiskEngine(include_config, market_data, portfolio)
    include_ctx = include_engine.build_risk_context()

    assert include_ctx.manual_positions_included is True
    assert include_ctx.total_exposure_usd == pytest.approx(300.0)
    assert include_ctx.total_exposure_pct == pytest.approx(30.0)
    assert include_ctx.manual_exposure_usd == pytest.approx(100.0)
    assert include_ctx.manual_exposure_pct == pytest.approx(10.0)
    assert include_ctx.per_strategy_exposure_usd["manual"] == pytest.approx(100.0)
    assert include_ctx.per_strategy_exposure_usd["trend"] == pytest.approx(200.0)

    exclude_config = RiskConfig(include_manual_positions=False)
    exclude_engine = RiskEngine(exclude_config, market_data, portfolio)
    exclude_ctx = exclude_engine.build_risk_context()

    assert exclude_ctx.manual_positions_included is False
    assert exclude_ctx.total_exposure_usd == pytest.approx(200.0)
    assert exclude_ctx.total_exposure_pct == pytest.approx(20.0)
    assert exclude_ctx.manual_exposure_usd == pytest.approx(100.0)
    assert exclude_ctx.manual_exposure_pct == pytest.approx(10.0)
    assert "manual" not in exclude_ctx.per_strategy_exposure_usd
    assert exclude_ctx.per_strategy_exposure_usd["trend"] == pytest.approx(200.0)


def test_clamped_flag_set_when_limits_reduce_target():
    market_data = MagicMock()
    market_data.get_latest_price.return_value = 100.0

    portfolio = _build_portfolio_mock()

    # Make the per-asset limit very small so we force a clamp, but not a full block.
    config = RiskConfig(max_per_asset_pct=10.0, max_portfolio_risk_pct=100.0)
    engine = RiskEngine(config, market_data, portfolio)

    # Ensure get_pair_metadata returns something with liquidity
    meta_mock = MagicMock()
    meta_mock.liquidity_24h_usd = 1_000_000.0
    market_data.get_pair_metadata.return_value = meta_mock

    intents = [_intent("s1", "XBTUSD", "enter", desired_usd=200.0)]

    actions = engine.process_intents(intents)

    assert len(actions) == 1
    action = actions[0]

    assert action.blocked is False
    assert action.clamped is True
    assert action.target_notional_usd == pytest.approx(100.0)
    assert "Clamped:" in action.reason
