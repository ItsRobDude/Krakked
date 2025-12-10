
import pytest
from unittest.mock import MagicMock
from kraken_bot.config import AppConfig, StrategyConfig, RiskConfig
from kraken_bot.strategy.engine import StrategyEngine

@pytest.fixture
def mock_app_config():
    config = MagicMock(spec=AppConfig)
    config.strategies = MagicMock()
    config.strategies.configs = {}
    config.strategies.enabled = []
    config.risk = MagicMock(spec=RiskConfig)
    config.risk.max_open_positions = 5
    return config

@pytest.fixture
def strategy_engine(mock_app_config):
    market_data = MagicMock()
    portfolio = MagicMock()
    return StrategyEngine(mock_app_config, market_data, portfolio)

def test_reload_config_updates_risk_engine(strategy_engine):
    # Setup initial state
    strategy_engine.initialize()
    assert strategy_engine.risk_engine.config.max_open_positions == 5

    # Create new config with different risk params
    new_config = MagicMock(spec=AppConfig)
    new_config.risk = MagicMock(spec=RiskConfig)
    new_config.risk.max_open_positions = 10
    new_config.strategies = MagicMock()
    new_config.strategies.configs = {}
    new_config.strategies.enabled = []

    # Reload
    strategy_engine.reload_config(new_config)

    # Verify update
    assert strategy_engine.risk_engine.config.max_open_positions == 10

def test_reload_config_manages_strategies(strategy_engine):
    # Setup initial config with one strategy
    strat1 = StrategyConfig(name="strat1", type="trend_following", enabled=True, params={"timeframes": ["1h"]})
    strategy_engine.config.strategies.configs = {"strat1": strat1}
    strategy_engine.config.strategies.enabled = ["strat1"]

    strategy_engine.initialize()
    assert "strat1" in strategy_engine.strategies
    assert strategy_engine.strategy_states["strat1"].enabled

    # New config: Disable strat1, Enable strat2
    new_config = MagicMock(spec=AppConfig)
    new_config.risk = MagicMock(spec=RiskConfig)

    strat1_new = StrategyConfig(name="strat1", type="trend_following", enabled=True, params={})
    strat2 = StrategyConfig(name="strat2", type="mean_reversion", enabled=True, params={"timeframes": ["15m"]})

    new_config.strategies = MagicMock()
    new_config.strategies.configs = {"strat1": strat1_new, "strat2": strat2}
    new_config.strategies.enabled = ["strat2"] # strat1 removed from enabled list

    # Reload
    strategy_engine.reload_config(new_config)

    # Verify
    assert "strat1" not in strategy_engine.strategies
    assert "strat2" in strategy_engine.strategies

    assert not strategy_engine.strategy_states["strat1"].enabled
    assert strategy_engine.strategy_states["strat2"].enabled

def test_reload_config_updates_strategy_params(strategy_engine):
    # Setup strat1
    strat1 = StrategyConfig(name="strat1", type="trend_following", enabled=True, params={"ma_fast": 10})
    strategy_engine.config.strategies.configs = {"strat1": strat1}
    strategy_engine.config.strategies.enabled = ["strat1"]

    strategy_engine.initialize()
    initial_strategy = strategy_engine.strategies["strat1"]
    assert initial_strategy.config.params["ma_fast"] == 10

    # Update params
    new_config = MagicMock(spec=AppConfig)
    new_config.risk = MagicMock(spec=RiskConfig)
    strat1_update = StrategyConfig(name="strat1", type="trend_following", enabled=True, params={"ma_fast": 20})
    new_config.strategies = MagicMock()
    new_config.strategies.configs = {"strat1": strat1_update}
    new_config.strategies.enabled = ["strat1"]

    strategy_engine.reload_config(new_config)

    updated_strategy = strategy_engine.strategies["strat1"]
    # Should be a new instance or updated
    assert updated_strategy.config.params["ma_fast"] == 20
    assert updated_strategy is not initial_strategy # We re-instantiated
