from unittest.mock import patch

import pytest

from kraken_bot.bootstrap import CredentialBootstrapError, bootstrap
from kraken_bot.config import (
    AppConfig,
    MarketDataConfig,
    PortfolioConfig,
    RegionCapabilities,
    RegionProfile,
    SessionConfig,
    StrategiesConfig,
    UniverseConfig,
)
from kraken_bot.credentials import CredentialResult, CredentialStatus


def _sample_config() -> AppConfig:
    return AppConfig(
        region=RegionProfile(
            code="US", capabilities=RegionCapabilities(False, False, False)
        ),
        universe=UniverseConfig(
            include_pairs=[], exclude_pairs=[], min_24h_volume_usd=0.0
        ),
        market_data=MarketDataConfig(
            ws={}, ohlc_store={}, backfill_timeframes=[], ws_timeframes=[]
        ),
        portfolio=PortfolioConfig(),
        strategies=StrategiesConfig(),
        session=SessionConfig(account_id="default"),
    )


def test_bootstrap_returns_client_and_config():
    with (
        patch("kraken_bot.bootstrap.load_config", return_value=_sample_config()),
        patch(
            "kraken_bot.bootstrap.load_api_keys",
            return_value=CredentialResult(
                "k", "s", CredentialStatus.LOADED, validated=True
            ),
        ) as mock_load_keys,
        patch("kraken_bot.bootstrap.KrakenRESTClient") as mock_client,
        patch("kraken_bot.bootstrap.ensure_default_account"),
        patch(
            "kraken_bot.bootstrap.resolve_secrets_path", return_value="path/to/secrets"
        ),
    ):
        client_instance = object()
        mock_client.return_value = client_instance

        client, config, rate_limiter = bootstrap()

    mock_load_keys.assert_called_once()
    # verify args
    call_kwargs = mock_load_keys.call_args.kwargs
    assert call_kwargs.get("account_id") == "default"
    assert call_kwargs.get("secrets_path") == "path/to/secrets"

    mock_client.assert_called_once_with(
        api_key="k", api_secret="s", rate_limiter=rate_limiter
    )
    assert client is client_instance
    assert config.region.code == "US"


def test_bootstrap_raises_on_missing_credentials():
    with (
        patch("kraken_bot.bootstrap.load_config", return_value=_sample_config()),
        patch(
            "kraken_bot.bootstrap.load_api_keys",
            return_value=CredentialResult(
                None, None, CredentialStatus.NOT_FOUND, validation_error="missing"
            ),
        ),
        patch("kraken_bot.bootstrap.ensure_default_account"),
        patch("kraken_bot.bootstrap.resolve_secrets_path"),
    ):
        with pytest.raises(CredentialBootstrapError) as excinfo:
            bootstrap()

    assert "missing" in str(excinfo.value)


def test_bootstrap_raises_when_keys_absent_despite_loaded_status():
    with (
        patch("kraken_bot.bootstrap.load_config", return_value=_sample_config()),
        patch(
            "kraken_bot.bootstrap.load_api_keys",
            return_value=CredentialResult("k", None, CredentialStatus.LOADED),
        ),
        patch("kraken_bot.bootstrap.ensure_default_account"),
        patch("kraken_bot.bootstrap.resolve_secrets_path"),
    ):
        with pytest.raises(CredentialBootstrapError) as excinfo:
            bootstrap()

    assert "API key/secret are missing" in str(excinfo.value)
