# tests/test_secrets.py

import pytest
import os
import json
from unittest.mock import patch, MagicMock
from kraken_bot.secrets import load_api_keys, encrypt_secrets, _decrypt_secrets, SecretsDecryptionError
from kraken_bot.connection.exceptions import AuthError

# Mock config dir to avoid writing to real system
@pytest.fixture
def mock_config_dir(tmp_path):
    with patch("kraken_bot.secrets.get_config_dir", return_value=tmp_path):
        yield tmp_path

def test_load_from_env_vars(mock_config_dir):
    with patch.dict(os.environ, {"KRAKEN_API_KEY": "env_key", "KRAKEN_API_SECRET": "env_secret"}):
        key, secret = load_api_keys()
        assert key == "env_key"
        assert secret == "env_secret"

def test_encrypt_and_decrypt_flow(mock_config_dir):
    api_key = "test_key"
    api_secret = "test_secret"
    password = "secure_password"

    encrypt_secrets(api_key, api_secret, password)

    # Verify file exists
    assert (mock_config_dir / "secrets.enc").exists()

    # Mock getpass to return the password automatically
    with patch("getpass.getpass", return_value=password):
        # We also need to patch os.getenv to ensure it doesn't try to use env vars
        with patch.dict(os.environ, {}, clear=True):
             # Also need to mock get_config_dir inside load_api_keys via secrets module patch
             # (Wait, mock_config_dir fixture already patches it globally in the module? No, only where imported)
             # The fixture patches 'kraken_bot.secrets.get_config_dir'. Correct.

             loaded_key, loaded_secret = load_api_keys()
             assert loaded_key == api_key
             assert loaded_secret == api_secret

def test_decrypt_bad_password(mock_config_dir):
    api_key = "test_key"
    api_secret = "test_secret"
    password = "correct_password"
    encrypt_secrets(api_key, api_secret, password)

    # Directly call _decrypt_secrets with wrong password to verify exception
    with pytest.raises(SecretsDecryptionError):
        _decrypt_secrets("wrong_password")
