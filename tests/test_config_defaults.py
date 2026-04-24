from pathlib import Path

import appdirs  # type: ignore[import-untyped]

from krakked.config import get_config_dir, load_config
from krakked.config_loader import (
    DEFAULT_STARTER_BACKFILL_TIMEFRAMES,
    DEFAULT_STARTER_PAIRS,
    DEFAULT_STARTER_STRATEGY_IDS,
    DEFAULT_STARTER_WS_TIMEFRAMES,
    cleanup_active_config_chain,
)


def test_load_config_sets_default_ohlc_store(monkeypatch, tmp_path: Path):
    """load_config should populate a default OHLC store when none is provided."""
    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"

    monkeypatch.setattr(appdirs, "user_config_dir", lambda appname: config_dir)
    monkeypatch.setattr(appdirs, "user_data_dir", lambda appname: data_dir)

    app_config = load_config()

    expected_root = str(data_dir / "ohlc")
    assert app_config.market_data.ohlc_store["root_dir"] == expected_root
    assert app_config.market_data.ohlc_store["backend"] == "parquet"


def test_load_config_region_profile_and_capabilities(monkeypatch, tmp_path: Path):
    """load_config should round-trip an explicit region and preserve defaults elsewhere."""
    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    config_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    config_path = config_dir / "config.yaml"
    config_path.write_text(
        """
region:
  code: "EU_TEST"
  default_quote: "EUR"
  capabilities:
    supports_margin: false
    supports_futures: true
    supports_staking: false
""".strip()
    )

    monkeypatch.setattr(appdirs, "user_config_dir", lambda appname: config_dir)
    monkeypatch.setattr(appdirs, "user_data_dir", lambda appname: data_dir)

    app_config = load_config()

    assert app_config.region.code == "EU_TEST"
    assert app_config.region.default_quote == "EUR"
    assert app_config.region.capabilities.supports_margin is False
    assert app_config.region.capabilities.supports_futures is True
    assert app_config.region.capabilities.supports_staking is False

    expected_root = str(data_dir / "ohlc")
    assert app_config.market_data.ohlc_store["root_dir"] == expected_root
    assert app_config.market_data.ohlc_store["backend"] == "parquet"


def test_get_config_dir_prefers_env_override(monkeypatch, tmp_path: Path):
    config_dir = tmp_path / "compose-config"
    monkeypatch.setenv("KRAKKED_CONFIG_DIR", str(config_dir))
    monkeypatch.setattr(appdirs, "user_config_dir", lambda appname: tmp_path / "ignored")

    assert get_config_dir() == config_dir


def test_load_config_uses_data_dir_env_override_for_default_ohlc_store(
    monkeypatch, tmp_path: Path
):
    config_dir = tmp_path / "config"
    data_dir = tmp_path / "compose-data"

    monkeypatch.setenv("KRAKKED_DATA_DIR", str(data_dir))
    monkeypatch.setattr(appdirs, "user_config_dir", lambda appname: config_dir)
    monkeypatch.setattr(appdirs, "user_data_dir", lambda appname: tmp_path / "ignored")

    app_config = load_config()

    assert app_config.market_data.ohlc_store["root_dir"] == str(data_dir / "ohlc")


def test_load_config_applies_default_starter_strategies_when_missing(
    monkeypatch, tmp_path: Path
):
    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    config_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    (config_dir / "config.yaml").write_text("execution:\n  mode: paper\n")

    monkeypatch.setattr(appdirs, "user_config_dir", lambda appname: config_dir)
    monkeypatch.setattr(appdirs, "user_data_dir", lambda appname: data_dir)

    app_config = load_config()

    assert app_config.strategies.enabled == DEFAULT_STARTER_STRATEGY_IDS
    assert set(app_config.strategies.configs) == set(DEFAULT_STARTER_STRATEGY_IDS)
    assert app_config.strategies.configs["trend_core"].enabled is True
    assert app_config.ml.enabled is False
    assert app_config.universe.include_pairs == DEFAULT_STARTER_PAIRS
    assert app_config.universe.min_24h_volume_usd == 100000.0
    assert app_config.market_data.backfill_timeframes == DEFAULT_STARTER_BACKFILL_TIMEFRAMES
    assert app_config.market_data.ws_timeframes == DEFAULT_STARTER_WS_TIMEFRAMES
    assert app_config.risk.max_open_positions == 4
    assert app_config.risk.max_per_strategy_pct["trend_core"] == 5.0


def test_load_config_unwraps_strategy_params(monkeypatch, tmp_path: Path):
    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    config_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    (config_dir / "config.yaml").write_text(
        """
strategies:
  enabled:
    - dca_overlay
  configs:
    dca_overlay:
      name: dca_overlay
      type: dca_rebalance
      enabled: true
      params:
        dca_interval_minutes: 240
        dca_notional_usd: 100.0
        pairs:
          - BTC/USD
          - ETH/USD
        target_weights:
          BTC/USD: 0.6
          ETH/USD: 0.4
""".strip()
    )

    monkeypatch.setattr(appdirs, "user_config_dir", lambda appname: config_dir)
    monkeypatch.setattr(appdirs, "user_data_dir", lambda appname: data_dir)

    app_config = load_config()
    params = app_config.strategies.configs["dca_overlay"].params

    assert "params" not in params
    assert params["pairs"] == ["BTC/USD", "ETH/USD"]
    assert params["target_weights"] == {"BTC/USD": 0.6, "ETH/USD": 0.4}


def test_load_config_ignores_empty_runtime_strategy_override_when_bootstrapping(
    monkeypatch, tmp_path: Path
):
    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    config_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    (config_dir / "config.yaml").write_text(
        "session:\n  profile_name: Rob\nprofiles:\n  Rob:\n    config_path: profiles\\Rob.yaml\n"
    )
    profile_dir = config_dir / "profiles" / "Rob"
    profile_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "profiles" / "Rob.yaml").write_text(
        "execution:\n  mode: paper\nml:\n  enabled: true\n"
    )
    (profile_dir / "config.runtime.yaml").write_text(
        "strategies:\n  enabled: []\n  configs: {}\n"
    )

    monkeypatch.setattr(appdirs, "user_config_dir", lambda appname: config_dir)
    monkeypatch.setattr(appdirs, "user_data_dir", lambda appname: data_dir)

    app_config = load_config()

    assert app_config.strategies.enabled == DEFAULT_STARTER_STRATEGY_IDS
    assert "trend_core" in app_config.strategies.configs


def test_cleanup_active_config_chain_normalizes_bootstrap_residue(
    monkeypatch, tmp_path: Path
):
    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    config_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    (config_dir / "config.yaml").write_text(
        """
execution:
  mode: paper
market_data:
  ws_timeframes:
  - 1m
  - 5m
ml:
  enabled: true
profiles:
  Rob:
    config_path: profiles\\Rob.yaml
session:
  profile_name: Rob
""".strip()
    )
    (config_dir / "profiles").mkdir(parents=True, exist_ok=True)
    (config_dir / "profiles" / "Rob.yaml").write_text(
        """
execution:
  mode: paper
ml:
  enabled: true
""".strip()
    )
    profile_runtime_dir = config_dir / "profiles" / "Rob"
    profile_runtime_dir.mkdir(parents=True, exist_ok=True)
    (profile_runtime_dir / "config.runtime.yaml").write_text(
        """
risk:
  dynamic_allocation_enabled: false
  dynamic_allocation_lookback_hours: 72
  include_manual_positions: true
  kill_switch_on_drift: true
  max_daily_drawdown_pct: 10.0
  max_open_positions: 10
  max_per_asset_pct: 5.0
  max_per_strategy_pct: {}
  max_portfolio_risk_pct: 10.0
  max_risk_per_trade_pct: 1.0
  max_strategy_weight_pct: 50.0
  min_liquidity_24h_usd: 100000.0
  min_strategy_weight_pct: 0.0
  volatility_lookback_bars: 20
session:
  profile_name: Rob
strategies:
  configs: {}
  enabled: []
""".strip()
    )

    monkeypatch.setattr(appdirs, "user_config_dir", lambda appname: config_dir)
    monkeypatch.setattr(appdirs, "user_data_dir", lambda appname: data_dir)

    first = cleanup_active_config_chain(config_dir)
    second = cleanup_active_config_chain(config_dir)
    app_config = load_config()

    assert first == {"changed": True, "main": True, "profile": True, "runtime": True}
    assert second == {
        "changed": False,
        "main": False,
        "profile": False,
        "runtime": False,
    }

    cleaned_main = load_config()
    cleaned_profile = (config_dir / "profiles" / "Rob.yaml").read_text()
    assert cleaned_main.ml.enabled is False
    assert cleaned_main.market_data.ws_timeframes == DEFAULT_STARTER_WS_TIMEFRAMES
    assert "enabled: true" not in cleaned_profile

    runtime_path = profile_runtime_dir / "config.runtime.yaml"
    runtime_text = runtime_path.read_text()
    assert "strategies:" not in runtime_text
    assert "risk:" not in runtime_text
    assert "profile_name: Rob" in runtime_text

    assert app_config.ml.enabled is False
    assert app_config.market_data.ws_timeframes == DEFAULT_STARTER_WS_TIMEFRAMES
    assert app_config.strategies.enabled == DEFAULT_STARTER_STRATEGY_IDS
    assert app_config.risk.max_open_positions == 4
    assert app_config.risk.max_per_strategy_pct["trend_core"] == 5.0
