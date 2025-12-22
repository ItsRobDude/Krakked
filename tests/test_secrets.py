"""Tests for secrets encryption and credential loading."""

import os
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from kraken_bot.credentials import CredentialStatus
from kraken_bot.password_store import get_saved_master_password, save_master_password
from kraken_bot.secrets import (
    SECRETS_FILE_NAME,
    SecretsDecryptionError,
    delete_secrets,
    encrypt_secrets,
    load_api_keys,
    persist_api_keys,
    unlock_secrets,
)


@pytest.fixture
def mock_config_dir(tmp_path):
    with (
        patch("kraken_bot.secrets.get_config_dir", return_value=tmp_path),
        patch("kraken_bot.config.get_config_dir", return_value=tmp_path),
    ):
        yield tmp_path


@pytest.fixture
def mock_keyring():
    store = {}

    def get_password(service, username):
        return store.get((service, username))

    def set_password(service, username, password):
        store[(service, username)] = password

    def delete_password(service, username):
        if (service, username) in store:
            del store[(service, username)]

    with (
        patch("keyring.get_password", side_effect=get_password),
        patch("keyring.set_password", side_effect=set_password),
        patch("keyring.delete_password", side_effect=delete_password),
    ):
        yield store


def test_encrypt_secrets_writes_file(mock_config_dir):
    password = "secure_password"
    api_key = "my_key"
    api_secret = "my_secret"

    encrypt_secrets(api_key, api_secret, password)

    secrets_path = mock_config_dir / SECRETS_FILE_NAME
    assert secrets_path.exists()
    assert secrets_path.stat().st_mode & 0o777 == 0o600

    with open(secrets_path, "rb") as f:
        content = f.read()
    assert len(content) > 32  # Salt + Encrypted Data


def test_unlock_secrets_decrypts_successfully(mock_config_dir):
    password = "secure_password"
    api_key = "my_key"
    api_secret = "my_secret"

    encrypt_secrets(api_key, api_secret, password)

    secrets = unlock_secrets(password)
    assert secrets["api_key"] == api_key
    assert secrets["api_secret"] == api_secret
    assert secrets["validated"] is None


def test_unlock_secrets_wrong_password(mock_config_dir):
    encrypt_secrets("key", "secret", "correct_password")

    with pytest.raises(SecretsDecryptionError, match="Invalid password"):
        unlock_secrets("wrong_password")


def test_load_api_keys_from_env(mock_config_dir):
    with patch.dict(
        os.environ,
        {"KRAKEN_API_KEY": "env_key", "KRAKEN_API_SECRET": "env_secret"},
    ):
        result = load_api_keys()
        assert result.status == CredentialStatus.LOADED
        assert result.api_key == "env_key"
        assert result.source == "environment"


def test_load_api_keys_from_file_with_env_password(mock_config_dir):
    encrypt_secrets("file_key", "file_secret", "env_pw")

    with patch.dict(os.environ, {"KRAKEN_BOT_SECRET_PW": "env_pw"}):
        result = load_api_keys()
        assert result.status == CredentialStatus.LOADED
        assert result.api_key == "file_key"
        assert result.source == "secrets_file"


def test_load_api_keys_from_file_missing_password(mock_config_dir):
    encrypt_secrets("file_key", "file_secret", "env_pw")

    # No env var, no interactive mode
    result = load_api_keys(allow_interactive_setup=False)
    assert result.status == CredentialStatus.MISSING_PASSWORD
    assert "Credentials unavailable" in result.validation_error


def test_persist_api_keys_with_validation_metadata(mock_config_dir):
    password = "pw"
    ts = datetime(2023, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    with patch("kraken_bot.secrets.datetime") as mock_dt:
        mock_dt.now.return_value = ts
        persist_api_keys(
            "k",
            "s",
            password,
            validated=True,
            validation_error=None,
        )

    secrets = unlock_secrets(password)
    assert secrets["validated"] is True
    assert secrets["validated_at"] == ts.isoformat()
    assert secrets["validation_error"] is None


def test_persist_api_keys_refuses_unvalidated_without_force(mock_config_dir):
    with pytest.raises(ValueError, match="Refusing to save"):
        persist_api_keys("k", "s", "pw", validated=False)


def test_persist_api_keys_allows_unvalidated_with_force(mock_config_dir):
    persist_api_keys("k", "s", "pw", validated=False, force_save_unvalidated=True)
    secrets = unlock_secrets("pw")
    assert secrets["validated"] is False


def test_delete_secrets_removes_file(mock_config_dir):
    path = mock_config_dir / SECRETS_FILE_NAME
    path.touch()
    assert path.exists()

    delete_secrets()
    assert not path.exists()


def test_delete_secrets_idempotent(mock_config_dir):
    delete_secrets()  # File doesn't exist, should not raise


def test_load_api_keys_shadow_config_warning(mock_config_dir, caplog):
    """Ensure mixed env/file config results in discarding env vars."""
    encrypt_secrets("file_key", "file_secret", "pw")
    with patch.dict(
        os.environ, {"KRAKEN_API_KEY": "env_key", "KRAKEN_BOT_SECRET_PW": "pw"}
    ):
        # Missing KRAKEN_API_SECRET
        result = load_api_keys(allow_interactive_setup=False)

        # Should fall back to file
        assert result.status == CredentialStatus.LOADED
        assert result.api_key == "file_key"
        assert "AMBIGUOUS CONFIGURATION DETECTED" in caplog.text


def test_default_account_migration(mock_config_dir, mock_keyring):
    """Test that default account migrates legacy password to new key format."""
    legacy_pw = "legacy_secret"

    # Setup legacy state
    mock_keyring[("Krakked", "master_password")] = legacy_pw

    # 1. Read using new account-aware function for "default"
    # Should find legacy key and migrate it
    pw = get_saved_master_password("default")
    assert pw == legacy_pw

    # 2. Verify migration happened (new key exists)
    assert mock_keyring[("Krakked", "master_password:default")] == legacy_pw

    # 3. Verify subsequent read uses new key
    # Clear legacy key to prove we aren't reading it anymore
    del mock_keyring[("Krakked", "master_password")]

    pw_new = get_saved_master_password("default")
    assert pw_new == legacy_pw


def test_password_store_per_account(mock_config_dir, mock_keyring):
    """Test per-account password storage isolation."""
    save_master_password("acc1", "pw1")
    save_master_password("acc2", "pw2")

    assert get_saved_master_password("acc1") == "pw1"
    assert get_saved_master_password("acc2") == "pw2"
    assert get_saved_master_password("acc3") is None
