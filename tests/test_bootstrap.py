import pytest
from unittest.mock import patch

from kraken_bot.bootstrap import bootstrap, CredentialBootstrapError
from kraken_bot.config import (
    AppConfig,
    MarketDataConfig,
    PortfolioConfig,
    RegionCapabilities,
    RegionProfile,
    StrategiesConfig,
    UniverseConfig,
)
from kraken_bot.secrets import CredentialResult, CredentialStatus


def _sample_config() -> AppConfig:
    return AppConfig(
        region=RegionProfile(code="US", capabilities=RegionCapabilities(False, False, False)),
        universe=UniverseConfig(include_pairs=[], exclude_pairs=[], min_24h_volume_usd=0.0),
        market_data=MarketDataConfig(ws={}, ohlc_store={}, backfill_timeframes=[], ws_timeframes=[]),
        portfolio=PortfolioConfig(),
        strategies=StrategiesConfig(),
    )


def test_bootstrap_returns_client_and_config():
    with patch("kraken_bot.bootstrap.load_config", return_value=_sample_config()), patch(
        "kraken_bot.bootstrap.load_api_keys",
        return_value=CredentialResult("k", "s", CredentialStatus.LOADED, validated=True),
    ), patch("kraken_bot.bootstrap.KrakenRESTClient") as mock_client:
        client_instance = object()
        mock_client.return_value = client_instance

        client, config = bootstrap()

    mock_client.assert_called_once_with(api_key="k", api_secret="s")
    assert client is client_instance
    assert config.region.code == "US"


def test_bootstrap_raises_on_missing_credentials():
    with patch("kraken_bot.bootstrap.load_config", return_value=_sample_config()), patch(
        "kraken_bot.bootstrap.load_api_keys",
        return_value=CredentialResult(None, None, CredentialStatus.NOT_FOUND, validation_error="missing"),
    ):
        with pytest.raises(CredentialBootstrapError) as excinfo:
            bootstrap()

    assert "missing" in str(excinfo.value)


def test_bootstrap_raises_when_keys_absent_despite_loaded_status():
    with patch("kraken_bot.bootstrap.load_config", return_value=_sample_config()), patch(
        "kraken_bot.bootstrap.load_api_keys",
        return_value=CredentialResult("k", None, CredentialStatus.LOADED),
    ):
        with pytest.raises(CredentialBootstrapError) as excinfo:
            bootstrap()

    assert "API key/secret are missing" in str(excinfo.value)
