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
