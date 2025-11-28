# tests/test_strategy_engine.py

import pytest
from unittest.mock import MagicMock
from datetime import datetime, timezone

from kraken_bot.config import AppConfig, StrategiesConfig, StrategyConfig, RiskConfig
from kraken_bot.strategy.engine import StrategyRiskEngine
from kraken_bot.market_data.api import MarketDataAPI
from kraken_bot.market_data.exceptions import DataStaleError
from kraken_bot.portfolio.manager import PortfolioService
from kraken_bot.portfolio.models import EquityView
from kraken_bot.strategy.models import DecisionRecord, StrategyIntent, StrategyState, RiskStatus
from kraken_bot.strategy.base import Strategy

def test_engine_cycle():
    # Setup Config
    strat_config = StrategyConfig(
        name="trend_v1", type="trend_following", enabled=True,
        params={"timeframes": ["1h"], "ma_fast": 10, "ma_slow": 20}
    )
    strategies_cfg = StrategiesConfig(enabled=["trend_v1"], configs={"trend_v1": strat_config})

    app_config = MagicMock(spec=AppConfig)
    app_config.strategies = strategies_cfg
    app_config.risk = RiskConfig()
    app_config.universe = MagicMock()
    app_config.universe.include_pairs = ["XBTUSD"]

    # Mock Services
    market = MagicMock(spec=MarketDataAPI)
    portfolio = MagicMock(spec=PortfolioService)

    market.get_data_status.return_value = MagicMock(
        rest_api_reachable=True,
        websocket_connected=True,
        stale_pairs=0,
    )

    # IMPORTANT: Mock drift_flag=False explicitly or use real object
    portfolio.get_equity.return_value = EquityView(
        equity_base=10000.0,
        cash_base=10000.0,
        realized_pnl_base_total=0.0,
        unrealized_pnl_base_total=0.0,
        drift_flag=False
    )

    portfolio.store = MagicMock()
    portfolio.store.get_snapshots.return_value = []
    portfolio.get_positions.return_value = []
    portfolio.get_asset_exposure.return_value = []

    # Mock OHLC for strategy
    from dataclasses import dataclass
    @dataclass
    class MockBar:
        close: float
        high: float = 0
        low: float = 0

    # Provide enough data for MA(20)
    # Slow MA = 20. Need > 20 bars.
    # To trigger LONG: Fast > Slow.
    # Increasing price pattern.
    prices = [100 + i for i in range(30)]
    market.get_ohlc.return_value = [MockBar(close=p, high=p+1, low=p-1) for p in prices]
    market.get_latest_price.return_value = 130.0

    engine = StrategyRiskEngine(app_config, market, portfolio)
    engine.initialize()

    assert len(engine.strategies) == 1

    # Run Cycle
    plan = engine.run_cycle(datetime.now(timezone.utc))

    assert plan is not None
    assert len(plan.actions) == 1
    assert plan.actions[0].pair == "XBTUSD"
    # Logic: Prices increasing -> Fast > Slow -> Long -> Enter
    assert plan.actions[0].action_type in ["open", "increase"]

    # Verify persistence call
    assert portfolio.record_decision.called
    args = portfolio.record_decision.call_args[0][0]
    assert isinstance(args, DecisionRecord)
    assert args.pair == "XBTUSD"

    # Execution plan should be persisted for downstream services
    assert portfolio.record_execution_plan.called

def test_engine_stale_data():
    app_config = MagicMock(spec=AppConfig)
    app_config.strategies = StrategiesConfig()
    app_config.risk = RiskConfig()

    market = MagicMock(spec=MarketDataAPI)
    portfolio = MagicMock(spec=PortfolioService)

    market.get_data_status.return_value = MagicMock(rest_api_reachable=False)

    engine = StrategyRiskEngine(app_config, market, portfolio)
    plan = engine.run_cycle()

    assert len(plan.actions) == 0
    assert "error" in plan.metadata


def test_data_stale_error_skips_timeframe():
    class FakeStrategy(Strategy):
        def warmup(self, market_data, portfolio):
            return None

        def generate_intents(self, ctx):
            if ctx.timeframe == "1h":
                raise DataStaleError("XBTUSD", 120.0, 60.0)
            return [
                StrategyIntent(
                    strategy_id=self.id,
                    pair="XBTUSD",
                    side="long",
                    intent_type="enter",
                    desired_exposure_usd=1000.0,
                    confidence=0.8,
                    timeframe=ctx.timeframe,
                    generated_at=ctx.now,
                )
            ]

    strat_config = StrategyConfig(
        name="fake", type="fake", enabled=True, params={"timeframes": ["1h", "4h"]}
    )
    strategies_cfg = StrategiesConfig(enabled=["fake"], configs={"fake": strat_config})

    app_config = MagicMock(spec=AppConfig)
    app_config.strategies = strategies_cfg
    app_config.risk = RiskConfig()
    app_config.universe = MagicMock()
    app_config.universe.include_pairs = ["XBTUSD"]

    market = MagicMock(spec=MarketDataAPI)
    market.get_data_status.return_value = MagicMock(
        rest_api_reachable=True,
        websocket_connected=True,
        stale_pairs=0,
    )

    portfolio = MagicMock(spec=PortfolioService)
    portfolio.record_execution_plan = MagicMock()

    engine = StrategyRiskEngine(app_config, market, portfolio)
    engine._data_ready = MagicMock(return_value=True)
    engine.risk_engine = MagicMock()
    engine.risk_engine.process_intents.return_value = []
    engine.risk_engine.get_status.return_value = RiskStatus(
        kill_switch_active=False,
        daily_drawdown_pct=0.0,
        drift_flag=False,
        total_exposure_pct=0.0,
        per_asset_exposure_pct={},
        per_strategy_exposure_pct={},
    )

    fake_strategy = FakeStrategy(strat_config)
    engine.strategies = {"fake": fake_strategy}
    engine.strategy_states = {
        "fake": StrategyState(
            strategy_id="fake",
            enabled=True,
            last_intents_at=None,
            last_actions_at=None,
            current_positions=[],
            pnl_summary={},
        )
    }

    plan = engine.run_cycle(datetime.now(timezone.utc))

    engine.risk_engine.process_intents.assert_called_once()
    processed_intents = engine.risk_engine.process_intents.call_args[0][0]
    assert len(processed_intents) == 1
    assert processed_intents[0].timeframe == "4h"

    assert plan.actions == []
    assert "risk_status" in plan.metadata
