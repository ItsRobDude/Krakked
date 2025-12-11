
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from kraken_bot.config import AppConfig, RiskConfig, StrategiesConfig, StrategyConfig
from kraken_bot.market_data.api import MarketDataAPI
from kraken_bot.portfolio.manager import PortfolioService
from kraken_bot.strategy.base import Strategy
from kraken_bot.strategy.engine import StrategyRiskEngine
from kraken_bot.strategy.models import (
    RiskAdjustedAction,
    RiskStatus,
    StrategyIntent,
    StrategyState,
)


def test_strategy_engine_composite_id_resolution():
    """
    Test that StrategyEngine correctly resolves userref for composite strategy IDs
    (e.g. 'stratB,stratA') by sorting them and picking the first valid config.
    """
    # Setup Configs
    # stratA has userref 100
    config_a = StrategyConfig(
        name="stratA",
        type="trend_following",
        enabled=True,
        userref=100,
        params={"timeframes": ["1h"]},
    )
    # stratB has userref 200
    config_b = StrategyConfig(
        name="stratB",
        type="mean_reversion",
        enabled=True,
        userref=200,
        params={"timeframes": ["1h"]},
    )
    # stratC has NO userref
    config_c = StrategyConfig(
        name="stratC",
        type="vol_breakout",
        enabled=True,
        userref=None,
        params={"timeframes": ["1h"]},
    )

    strategies_cfg = StrategiesConfig(
        enabled=["stratA", "stratB", "stratC"],
        configs={"stratA": config_a, "stratB": config_b, "stratC": config_c},
    )

    app_config = MagicMock(spec=AppConfig)
    app_config.strategies = strategies_cfg
    app_config.risk = RiskConfig()
    app_config.universe = MagicMock()
    app_config.universe.include_pairs = ["XBTUSD"]

    # Mock Services
    market = MagicMock(spec=MarketDataAPI)
    market.get_data_status.return_value = MagicMock(
        rest_api_reachable=True,
        websocket_connected=True,
        stale_pairs=0,
    )

    # Ensure get_universe returns something so infer_regime doesn't crash if called
    market.get_universe.return_value = ["XBTUSD"]

    portfolio = MagicMock(spec=PortfolioService)
    portfolio.record_execution_plan = MagicMock()
    portfolio.record_decision = MagicMock()
    portfolio.get_realized_pnl_by_strategy.return_value = {}

    engine = StrategyRiskEngine(app_config, market, portfolio)

    # Bypass data checks and portfolio sync
    engine._data_ready = MagicMock(return_value=True)
    engine._compute_strategy_weights = MagicMock(return_value=None)

    # Mock RiskEngine
    engine.risk_engine = MagicMock()
    engine.risk_engine.get_status.return_value = RiskStatus(
        kill_switch_active=False,
        daily_drawdown_pct=0.0,
        drift_flag=False,
        total_exposure_pct=0.0,
        manual_exposure_pct=0.0,
        per_asset_exposure_pct={},
        per_strategy_exposure_pct={},
    )
    engine.risk_engine.build_risk_context.return_value = SimpleNamespace(
        per_strategy_exposure_pct={}
    )

    # Mock Strategies
    class FakeStrategy(Strategy):
        def warmup(self, market_data, portfolio): pass
        def generate_intents(self, ctx): return [] # We'll bypass intent generation

    engine.strategies = {
        "stratA": FakeStrategy(config_a),
        "stratB": FakeStrategy(config_b),
        "stratC": FakeStrategy(config_c),
    }
    engine.strategy_states = {
        sid: StrategyState(
            strategy_id=sid,
            enabled=True,
            last_intents_at=None,
            last_actions_at=None,
            current_positions=[],
            pnl_summary={},
        )
        for sid in ["stratA", "stratB", "stratC"]
    }

    # Case 1: "stratB,stratA" -> Should resolve to "stratA" (userref 100) because "stratA" < "stratB"
    action_1 = RiskAdjustedAction(
        pair="XBTUSD",
        strategy_id="stratB,stratA", # Composite ID
        action_type="open",
        target_base_size=1.0,
        target_notional_usd=100.0,
        current_base_size=0.0,
        reason="merged",
        blocked=False,
        blocked_reasons=[],
        risk_limits_snapshot={},
        userref=None # Currently None
    )

    # Case 2: "stratC,stratB" -> "stratC" has no userref. "stratB" has 200.
    # Sorted: "stratB", "stratC". "stratB" is first and has userref -> 200.
    action_2 = RiskAdjustedAction(
        pair="ETHUSD",
        strategy_id="stratC,stratB",
        action_type="open",
        target_base_size=1.0,
        target_notional_usd=100.0,
        current_base_size=0.0,
        reason="merged",
        blocked=False,
        blocked_reasons=[],
        risk_limits_snapshot={},
        userref=None
    )

    # Case 3: "stratC,stratD" -> "stratD" doesn't exist. "stratC" has no userref.
    # Result: userref remains None.
    action_3 = RiskAdjustedAction(
        pair="SOLUSD",
        strategy_id="stratC,stratD",
        action_type="open",
        target_base_size=1.0,
        target_notional_usd=100.0,
        current_base_size=0.0,
        reason="merged",
        blocked=False,
        blocked_reasons=[],
        risk_limits_snapshot={},
        userref=None
    )

    engine.risk_engine.process_intents.return_value = [action_1, action_2, action_3]

    # Run Cycle
    plan = engine.run_cycle(datetime.now(timezone.utc))

    # Assertions
    # Action 1: stratB,stratA -> sorted stratA,stratB -> pick stratA -> userref 100
    assert plan.actions[0].strategy_id == "stratB,stratA"
    assert plan.actions[0].userref == "100"

    # Action 2: stratC,stratB -> sorted stratB,stratC -> pick stratB -> userref 200
    assert plan.actions[1].strategy_id == "stratC,stratB"
    assert plan.actions[1].userref == "200"

    # Action 3: stratC,stratD -> sorted stratC,stratD -> stratC(no userref), stratD(no config) -> None
    assert plan.actions[2].strategy_id == "stratC,stratD"
    assert plan.actions[2].userref is None
