from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

from kraken_bot.config import RiskConfig
from kraken_bot.market_data.exceptions import DataStaleError
from kraken_bot.portfolio.manager import PortfolioService
from kraken_bot.portfolio.models import EquityView, SpotPosition
from kraken_bot.strategy.models import StrategyIntent
from kraken_bot.strategy.risk import RiskContext, RiskEngine


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
    portfolio = MagicMock(spec=PortfolioService)
    portfolio.get_equity.return_value = EquityView(
        equity_base=1000.0,
        cash_base=1000.0,
        realized_pnl_base_total=0.0,
        unrealized_pnl_base_total=0.0,
        drift_flag=False,
    )
    portfolio.get_positions.return_value = []
    portfolio.get_asset_exposure.return_value = []
    portfolio.store = SimpleNamespace(get_snapshots=MagicMock(return_value=[]))
    return portfolio


def _intent(strategy_id: str, pair: str, intent_type: str, desired_usd: float = 0.0):
    return StrategyIntent(
        strategy_id=strategy_id,
        pair=pair,
        side="long",
        intent_type=intent_type,
        desired_exposure_usd=desired_usd,
        confidence=1.0,
        timeframe="1h",
        generated_at=datetime.utcnow(),
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

    actions = engine._block_all_opens([_intent("s1", "ETHUSD", "enter")], ctx, "Kill Switch Active")

    assert len(actions) == 1
    assert actions[0].target_notional_usd == 0.0
    assert actions[0].blocked is True
