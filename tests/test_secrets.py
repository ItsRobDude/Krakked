# tests/test_secrets.py

import os
import stat
import pytest
from unittest.mock import patch
from kraken_bot.secrets import (
    load_api_keys,
    encrypt_secrets,
    _decrypt_secrets,
    SecretsDecryptionError,
    CredentialStatus,
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

             result = load_api_keys()
             assert result.api_key == api_key
             assert result.api_secret == api_secret
             assert result.status == CredentialStatus.LOADED
             assert result.source == "secrets_file"

def test_decrypt_bad_password(mock_config_dir):
    api_key = "test_key"
    api_secret = "test_secret"
    password = "correct_password"
    encrypt_secrets(api_key, api_secret, password)

    # Directly call _decrypt_secrets with wrong password to verify exception
    with pytest.raises(SecretsDecryptionError):
        _decrypt_secrets("wrong_password")
