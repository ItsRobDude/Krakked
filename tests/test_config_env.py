"""Tests for environment handling and configuration overlays."""

from pathlib import Path

import appdirs  # type: ignore[import-untyped]
import pytest

from krakked.config import load_config


@pytest.mark.parametrize("env_value", [None, "prod"])
def test_invalid_or_missing_env_defaults_to_paper(
    monkeypatch, tmp_path: Path, env_value
):
    """Invalid or missing env should fall back to paper config overlay and defaults."""

    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    config_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    base_config = config_dir / "config.yaml"
    env_config = config_dir / "config.paper.yaml"

    base_config.write_text(
        """
execution:
  mode: "live"
  validate_only: false
  allow_live_trading: true
""".strip()
    )

    env_config.write_text(
        """
execution:
  mode: "paper"
  validate_only: true
  allow_live_trading: false
""".strip()
    )

    monkeypatch.setattr(appdirs, "user_config_dir", lambda appname: config_dir)
    monkeypatch.setattr(appdirs, "user_data_dir", lambda appname: data_dir)

    if env_value is None:
        monkeypatch.delenv("KRAKKED_ENV", raising=False)
    else:
        monkeypatch.setenv("KRAKKED_ENV", env_value)

    app_config = load_config()

    assert app_config.execution.mode == "paper"
    assert app_config.execution.validate_only is False
    assert app_config.execution.allow_live_trading is False


def test_deep_merge_applies_env_overlay(monkeypatch, tmp_path: Path):
    """Environment overlays should deep-merge config values over the base file."""

    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    config_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    base_config = config_dir / "config.yaml"
    env_config = config_dir / "config.paper.yaml"

    base_config.write_text(
        """
market_data:
  ohlc_store:
    root_dir: "/base/root"
    backend: "parquet"
  backfill_timeframes: ["1h"]
execution:
  mode: "paper"
  max_slippage_bps: 25
risk:
  max_open_positions: 5
""".strip()
    )

    env_config.write_text(
        """
market_data:
  ohlc_store:
    backend: "csv"
execution:
  post_only: true
""".strip()
    )

    monkeypatch.setattr(appdirs, "user_config_dir", lambda appname: config_dir)
    monkeypatch.setattr(appdirs, "user_data_dir", lambda appname: data_dir)
    monkeypatch.setenv("KRAKKED_ENV", "paper")

    app_config = load_config()

    assert app_config.market_data.ohlc_store["root_dir"] == "/base/root"
    assert app_config.market_data.ohlc_store["backend"] == "csv"
    assert app_config.market_data.backfill_timeframes == ["1h"]
    assert app_config.execution.post_only is True
    assert app_config.execution.max_slippage_bps == 25
    assert app_config.risk.max_open_positions == 5


def test_live_mode_defaults_to_validate_only(monkeypatch, tmp_path: Path):
    """Live execution without explicit flags should remain validate-only and disallow trading."""

    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    config_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    config_path = config_dir / "config.yaml"
    config_path.write_text(
        """
execution:
  mode: "live"
""".strip()
    )

    monkeypatch.setattr(appdirs, "user_config_dir", lambda appname: config_dir)
    monkeypatch.setattr(appdirs, "user_data_dir", lambda appname: data_dir)
    monkeypatch.setenv("KRAKKED_ENV", "live")

    app_config = load_config()

    assert app_config.execution.mode == "live"
    assert app_config.execution.validate_only is True
    assert app_config.execution.allow_live_trading is False


def test_live_mode_without_allow_trading_is_forced_validate(
    monkeypatch, tmp_path: Path
):
    """Live mode without allow_live_trading should coerce validate_only to True."""

    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    config_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    config_path = config_dir / "config.yaml"
    config_path.write_text(
        """
execution:
  mode: "live"
  validate_only: false
  allow_live_trading: false
""".strip()
    )

    monkeypatch.setattr(appdirs, "user_config_dir", lambda appname: config_dir)
    monkeypatch.setattr(appdirs, "user_data_dir", lambda appname: data_dir)
    monkeypatch.setenv("KRAKKED_ENV", "live")

    app_config = load_config()

    assert app_config.execution.mode == "live"
    assert app_config.execution.allow_live_trading is False
    assert app_config.execution.validate_only is True


def test_portfolio_auto_migrate_defaults_follow_env(monkeypatch, tmp_path: Path):
    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    config_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(appdirs, "user_config_dir", lambda appname: config_dir)
    monkeypatch.setattr(appdirs, "user_data_dir", lambda appname: data_dir)

    monkeypatch.setenv("KRAKKED_ENV", "live")
    live_config = load_config()
    assert live_config.portfolio.auto_migrate_schema is False

    monkeypatch.setenv("KRAKKED_ENV", "paper")
    paper_config = load_config()
    assert paper_config.portfolio.auto_migrate_schema is False


def test_live_env_disables_ui_without_auth(monkeypatch, tmp_path: Path, caplog):
    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    config_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    config_path = config_dir / "config.yaml"
    config_path.write_text(
        """
ui:
  enabled: true
  auth:
    enabled: false
""".strip()
    )

    monkeypatch.setattr(appdirs, "user_config_dir", lambda appname: config_dir)
    monkeypatch.setattr(appdirs, "user_data_dir", lambda appname: data_dir)
    monkeypatch.setenv("KRAKKED_ENV", "live")

    with caplog.at_level("WARNING"):
        app_config = load_config()

    assert app_config.ui.enabled is False
    assert any(
        rec.__dict__.get("event") == "live_ui_disabled_no_auth"
        for rec in caplog.records
    )


def test_ui_auth_empty_token_disables_auth_and_logs(
    monkeypatch, tmp_path: Path, caplog
):
    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    config_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    config_path = config_dir / "config.yaml"
    config_path.write_text(
        """
ui:
  enabled: true
  auth:
    enabled: true
    token: "   "
""".strip()
    )

    monkeypatch.setattr(appdirs, "user_config_dir", lambda appname: config_dir)
    monkeypatch.setattr(appdirs, "user_data_dir", lambda appname: data_dir)
    monkeypatch.setenv("KRAKKED_ENV", "paper")

    with caplog.at_level("WARNING"):
        app_config = load_config()

    assert app_config.ui.enabled is True
    assert app_config.ui.auth.enabled is False
    assert app_config.ui.auth.token == ""
    assert any(
        getattr(rec, "event", None) == "ui_auth_empty_token" for rec in caplog.records
    )


def test_max_slippage_bps_clamped(monkeypatch, tmp_path: Path):
    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    config_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    config_path = config_dir / "config.yaml"
    config_path.write_text(
        """
execution:
  mode: "paper"
  max_slippage_bps: 10000
""".strip()
    )

    monkeypatch.setattr(appdirs, "user_config_dir", lambda appname: config_dir)
    monkeypatch.setattr(appdirs, "user_data_dir", lambda appname: data_dir)
    monkeypatch.setenv("KRAKKED_ENV", "paper")

    app_config = load_config()

    assert app_config.execution.max_slippage_bps == 5000


def test_live_mode_requires_per_strategy_limits(monkeypatch, tmp_path: Path):
    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    config_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    config_path = config_dir / "config.yaml"
    config_path.write_text(
        """
strategies:
  enabled: ["alpha"]
  configs:
    alpha:
      type: momentum
      enabled: true
execution:
  mode: "live"
  allow_live_trading: true
""".strip()
    )

    monkeypatch.setattr(appdirs, "user_config_dir", lambda appname: config_dir)
    monkeypatch.setattr(appdirs, "user_data_dir", lambda appname: data_dir)
    monkeypatch.setenv("KRAKKED_ENV", "live")

    with pytest.raises(ValueError):
        load_config()


def test_missing_strategy_limit_defaults_in_paper(monkeypatch, tmp_path: Path):
    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    config_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    config_path = config_dir / "config.yaml"
    config_path.write_text(
        """
strategies:
  enabled: ["alpha"]
  configs:
    alpha:
      type: momentum
      enabled: true
risk:
  max_risk_per_trade_pct: 0.5
""".strip()
    )

    monkeypatch.setattr(appdirs, "user_config_dir", lambda appname: config_dir)
    monkeypatch.setattr(appdirs, "user_data_dir", lambda appname: data_dir)
    monkeypatch.setenv("KRAKKED_ENV", "paper")

    app_config = load_config()

    assert app_config.risk.max_per_strategy_pct["alpha"] == pytest.approx(0.5)


def test_manual_strategy_limit_is_ignored_without_warning(
    monkeypatch, tmp_path: Path, caplog
):
    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    config_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    config_path = config_dir / "config.yaml"
    config_path.write_text(
        """
strategies:
  enabled: ["alpha"]
  configs:
    alpha:
      type: momentum
      enabled: true
risk:
  include_manual_positions: true
  max_per_strategy_pct:
    alpha: 10.0
    manual: 20.0
""".strip()
    )

    monkeypatch.setattr(appdirs, "user_config_dir", lambda appname: config_dir)
    monkeypatch.setattr(appdirs, "user_data_dir", lambda appname: data_dir)
    monkeypatch.setenv("KRAKKED_ENV", "paper")

    with caplog.at_level("INFO"):
        app_config = load_config()

    assert app_config.risk.include_manual_positions is True
    assert app_config.risk.max_per_strategy_pct == {"alpha": 10.0}
    assert not any(
        getattr(record, "event", None) == "config_unknown_strategy_limit"
        and getattr(record, "strategy_id", None) == "manual"
        for record in caplog.records
    )
    assert any(
        getattr(record, "event", None) == "config_reserved_strategy_limit_ignored"
        and getattr(record, "strategy_id", None) == "manual"
        for record in caplog.records
    )


def test_unknown_strategy_limit_still_warns(monkeypatch, tmp_path: Path, caplog):
    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    config_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    config_path = config_dir / "config.yaml"
    config_path.write_text(
        """
strategies:
  enabled: ["alpha"]
  configs:
    alpha:
      type: momentum
      enabled: true
risk:
  max_per_strategy_pct:
    alpha: 10.0
    typo_strategy: 20.0
""".strip()
    )

    monkeypatch.setattr(appdirs, "user_config_dir", lambda appname: config_dir)
    monkeypatch.setattr(appdirs, "user_data_dir", lambda appname: data_dir)
    monkeypatch.setenv("KRAKKED_ENV", "paper")

    with caplog.at_level("WARNING"):
        app_config = load_config()

    assert app_config.risk.max_per_strategy_pct == {"alpha": 10.0}
    assert any(
        getattr(record, "event", None) == "config_unknown_strategy_limit"
        and getattr(record, "strategy_id", None) == "typo_strategy"
        for record in caplog.records
    )
