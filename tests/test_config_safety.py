"""Tests for configuration persistence safety, specifically handling null values."""

from unittest.mock import patch

import yaml

from kraken_bot.config_loader import dump_runtime_overrides, load_config
from kraken_bot.config_models import (
    AppConfig,
    ExecutionConfig,
    MarketDataConfig,
    PortfolioConfig,
    RegionCapabilities,
    RegionProfile,
    RiskConfig,
    SessionConfig,
    StrategiesConfig,
    UIConfig,
    UniverseConfig,
)


def _minimal_app_config(session_config=None):
    return AppConfig(
        region=RegionProfile(
            code="US", capabilities=RegionCapabilities(False, False, False)
        ),
        universe=UniverseConfig([], [], 0.0),
        market_data=MarketDataConfig({}, {}, [], []),
        portfolio=PortfolioConfig(),
        execution=ExecutionConfig(),
        risk=RiskConfig(),
        strategies=StrategiesConfig(),
        ui=UIConfig(),
        profiles={},
        session=session_config or SessionConfig(),
    )


def test_load_config_handles_null_account_id(tmp_path):
    config_path = tmp_path / "config.yaml"

    # Create config with explicit null account_id
    config_data = {"session": {"account_id": None, "mode": "paper"}}

    with open(config_path, "w") as f:
        yaml.safe_dump(config_data, f)

    with patch("kraken_bot.config_loader.get_config_dir", return_value=tmp_path):
        config = load_config(config_path=config_path)

    assert config.session.account_id == "default"


def test_dump_runtime_overrides_handles_null_account_id(tmp_path):
    # Setup AppConfig with None account_id in session
    session = SessionConfig(account_id=None)
    config = _minimal_app_config(session)

    with patch("kraken_bot.config_loader.get_config_dir", return_value=tmp_path):
        dump_runtime_overrides(config, config_dir=tmp_path, session=session)

    runtime_path = tmp_path / "config.runtime.yaml"
    assert runtime_path.exists()

    with open(runtime_path, "r") as f:
        data = yaml.safe_load(f)

    assert data["session"]["account_id"] == "default"
