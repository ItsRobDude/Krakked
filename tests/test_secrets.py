# tests/test_secrets.py

import os
import stat
import pytest
from unittest.mock import patch
from kraken_bot.connection.exceptions import AuthError, ServiceUnavailableError
from kraken_bot.secrets import (
    load_api_keys,
    encrypt_secrets,
    _decrypt_secrets,
    SecretsDecryptionError,
    CredentialStatus,
    CredentialResult,
    persist_api_keys,
    _interactive_setup,
)

# Mock config dir to avoid writing to real system
@pytest.fixture
def mock_config_dir(tmp_path):
    with patch("kraken_bot.secrets.get_config_dir", return_value=tmp_path):
        yield tmp_path

def test_load_from_env_vars(mock_config_dir):
    with patch.dict(os.environ, {"KRAKEN_API_KEY": "env_key", "KRAKEN_API_SECRET": "env_secret"}):
        result = load_api_keys()
        assert result.api_key == "env_key"
        assert result.api_secret == "env_secret"
        assert result.status == CredentialStatus.LOADED
        assert result.source == "environment"


def test_partial_env_vars_return_auth_error(mock_config_dir):
    with patch.dict(os.environ, {"KRAKEN_API_KEY": "env_key"}, clear=True), patch(
        "getpass.getpass"
    ) as mock_getpass:
        result = load_api_keys()

    mock_getpass.assert_not_called()
    assert result.api_key == "env_key"
    assert result.api_secret is None
    assert result.status == CredentialStatus.AUTH_ERROR
    assert result.source == "environment"
    assert "both" in result.validation_error.lower()

def test_encrypt_and_decrypt_flow(mock_config_dir):
    api_key = "test_key"
    api_secret = "test_secret"
    password = "secure_password"

    encrypt_secrets(api_key, api_secret, password)

    # Verify file exists
    secrets_file = mock_config_dir / "secrets.enc"
    assert secrets_file.exists()
    assert stat.S_IMODE(secrets_file.stat().st_mode) == 0o600

    # Mock getpass to return the password automatically
    with patch("getpass.getpass", return_value=password):
        # We also need to patch os.getenv to ensure it doesn't try to use env vars
        with patch.dict(os.environ, {}, clear=True):
            # Also need to mock get_config_dir inside load_api_keys via secrets module patch
            # (Wait, mock_config_dir fixture already patches it globally in the module? No, only where imported)
            # The fixture patches 'kraken_bot.secrets.get_config_dir'. Correct.

            result = load_api_keys(allow_interactive_setup=True)
            assert result.api_key == api_key
            assert result.api_secret == api_secret
            assert result.status == CredentialStatus.LOADED
            assert result.source == "secrets_file"


def test_load_api_keys_not_found_without_interactive(mock_config_dir):
    with patch.dict(os.environ, {}, clear=True):
        result = load_api_keys()

    assert result.api_key is None
    assert result.api_secret is None
    assert result.status == CredentialStatus.NOT_FOUND
    assert result.source == "none"


def test_load_api_keys_uses_interactive_setup_when_allowed(mock_config_dir):
    expected_result = CredentialResult("key", "secret", CredentialStatus.LOADED, source="interactive")

    with patch("kraken_bot.secrets._interactive_setup", return_value=expected_result) as mock_setup:
        with patch.dict(os.environ, {}, clear=True):
            result = load_api_keys(allow_interactive_setup=True)

    mock_setup.assert_called_once()
    assert result == expected_result


def test_load_api_keys_requires_password_env_when_non_interactive(mock_config_dir):
    secrets_file = mock_config_dir / "secrets.enc"
    secrets_file.write_text("placeholder")

    with patch.dict(os.environ, {}, clear=True), patch("getpass.getpass") as mock_getpass:
        result = load_api_keys()

    mock_getpass.assert_not_called()
    assert result.status == CredentialStatus.MISSING_PASSWORD
    assert result.api_key is None
    assert result.api_secret is None
    assert result.source == "secrets_file"
    assert "password" in result.validation_error.lower()
    assert "set" in result.validation_error.lower()


def test_load_api_keys_bad_password_returns_auth_error(mock_config_dir):
    api_key = "test_key"
    api_secret = "test_secret"
    correct_password = "correct_password"
    encrypt_secrets(api_key, api_secret, correct_password)

    with patch.dict(os.environ, {"KRAKEN_BOT_SECRET_PW": "wrong_password"}, clear=True), patch(
        "getpass.getpass"
    ) as mock_getpass:
        result = load_api_keys()

    mock_getpass.assert_not_called()
    assert result.status in (CredentialStatus.AUTH_ERROR, CredentialStatus.LOCKED)
    assert result.api_key is None
    assert result.api_secret is None
    assert result.source == "secrets_file"
    assert "password" in result.validation_error.lower()
    assert any(term in result.validation_error.lower() for term in ("invalid", "locked"))


def test_decrypt_bad_password(mock_config_dir):
    api_key = "test_key"
    api_secret = "test_secret"
    password = "correct_password"
    encrypt_secrets(api_key, api_secret, password)

    # Directly call _decrypt_secrets with wrong password to verify exception
    with pytest.raises(SecretsDecryptionError):
        _decrypt_secrets("wrong_password")


def test_encrypt_includes_validation_metadata(mock_config_dir):
    api_key = "meta_key"
    api_secret = "meta_secret"
    password = "pw"

    encrypt_secrets(api_key, api_secret, password, validated=True, validation_error=None)

    secrets = _decrypt_secrets(password)
    assert secrets["api_key"] == api_key
    assert secrets["api_secret"] == api_secret
    assert secrets["validated"] is True
    assert secrets["validated_at"] is not None
    assert secrets["validation_error"] is None


def test_persist_api_keys_requires_force_for_unvalidated(mock_config_dir):
    with pytest.raises(ValueError):
        persist_api_keys("key", "secret", "pw", validated=False)


def test_persist_api_keys_can_force_save_unvalidated(mock_config_dir):
    persist_api_keys("key", "secret", "pw", validated=False, validation_error="service", force_save_unvalidated=True)

    secrets = _decrypt_secrets("pw")
    assert secrets["validated"] is False
    assert secrets["validation_error"] == "service"


def test_interactive_setup_service_error_prompts_and_allows_skip(mock_config_dir):
    with patch("builtins.input", side_effect=["key", "n"]), patch(
        "getpass.getpass", side_effect=["secret"]
    ), patch("kraken_bot.secrets.KrakenRESTClient") as mock_client:
        mock_instance = mock_client.return_value
        mock_instance.get_private.side_effect = ServiceUnavailableError("unavailable")

        result = _interactive_setup()

    assert result.status == CredentialStatus.SERVICE_ERROR
    assert result.validated is False
    assert result.can_force_save is True
    assert result.validation_error == "unavailable"


def test_interactive_setup_auth_error_blocks_force_save(mock_config_dir):
    with patch("builtins.input", return_value="key"), patch(
        "getpass.getpass", side_effect=["secret"]
    ), patch("kraken_bot.secrets.KrakenRESTClient") as mock_client:
        mock_instance = mock_client.return_value
        mock_instance.get_private.side_effect = AuthError("auth bad")

        result = _interactive_setup()

    assert result.status == CredentialStatus.AUTH_ERROR
    assert result.validated is False
    assert result.can_force_save is False
    assert result.validation_error == "auth bad"


def test_interactive_setup_service_error_can_force_save(mock_config_dir):
    with patch("builtins.input", side_effect=["key", "y"]), patch(
        "getpass.getpass", side_effect=["secret", "pw", "pw"]
    ), patch("kraken_bot.secrets.KrakenRESTClient") as mock_client:
        mock_instance = mock_client.return_value
        mock_instance.get_private.side_effect = ServiceUnavailableError("unavailable")

        result = _interactive_setup()

    secrets = _decrypt_secrets("pw")
    assert secrets["api_key"] == "key"
    assert secrets["api_secret"] == "secret"
    assert secrets["validated"] is False
    assert secrets["validation_error"] == "unavailable"

    assert result.status == CredentialStatus.LOADED
    assert result.validated is False
    assert result.validation_error == "unavailable"
