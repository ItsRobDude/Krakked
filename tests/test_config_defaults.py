# tests/test_config_defaults.py

import appdirs  # type: ignore[import-untyped]
from pathlib import Path
from kraken_bot.config import load_config


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


def test_load_config_region_and_capabilities_roundtrip(monkeypatch, tmp_path: Path):
    """load_config should round-trip an explicit region and preserve defaults elsewhere."""
    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    config_dir.mkdir(parents=True, exist_ok=True)

    config_path = config_dir / "config.yaml"
    config_path.write_text(
        """
region:
  code: "EU_TEST"
  default_quote: "EUR"
  capabilities:
    supports_margin: true
    supports_futures: false
    supports_staking: true
""".strip()
    )

    monkeypatch.setattr(appdirs, "user_config_dir", lambda appname: config_dir)
    monkeypatch.setattr(appdirs, "user_data_dir", lambda appname: data_dir)

    app_config = load_config()

    assert app_config.region.code == "EU_TEST"
    assert app_config.region.default_quote == "EUR"
    assert app_config.region.capabilities.supports_margin is True
    assert app_config.region.capabilities.supports_futures is False
    assert app_config.region.capabilities.supports_staking is True

    expected_root = str(data_dir / "ohlc")
    assert app_config.market_data.ohlc_store["root_dir"] == expected_root
    assert app_config.market_data.ohlc_store["backend"] == "parquet"
