import os
import json
import pytest
from unittest import mock

from kraken_trader import secrets
from kraken_trader.connection import KrakenClient, KrakenAPIError

@pytest.fixture
def mock_secrets_dir(tmp_path):
    """Mocks the config directory to use a temporary path for secrets."""
    with mock.patch('kraken_trader.config.get_config_dir', return_value=str(tmp_path)):
        secrets.SECRETS_FILE_PATH = os.path.join(str(tmp_path), "secrets.enc")
        yield str(tmp_path)

@pytest.fixture
def mock_kraken_client():
    """Mocks the KrakenClient to control its behavior during tests."""
    with mock.patch('kraken_trader.secrets.KrakenClient') as MockClient:
        instance = MockClient.return_value
        instance.get_balance.return_value = {"ZUSD": "1000.00"}
        yield instance

# --- Test Encryption/Decryption ---

def test_encrypt_decrypt_roundtrip(mock_secrets_dir):
    """Tests that encrypting and then decrypting returns the original secrets."""
    api_key = "test_key"
    api_secret = "test_secret"
    password = "test_password"

    secrets.encrypt_secrets(api_key, api_secret, password)

    decrypted = secrets._decrypt_secrets(password)
    assert decrypted["api_key"] == api_key
    assert decrypted["api_secret"] == api_secret

def test_decrypt_with_wrong_password_raises_error(mock_secrets_dir):
    """Tests that using the wrong password fails with a ValueError."""
    secrets.encrypt_secrets("key", "secret", "correct_password")

    with pytest.raises(ValueError, match="Invalid password"):
        secrets._decrypt_secrets("wrong_password")

# --- Test Credential Loading Logic ---

def test_load_api_keys_from_env_vars(monkeypatch, mock_secrets_dir):
    """Tests that environment variables are prioritized for loading keys."""
    monkeypatch.setenv("KRAKEN_API_KEY", "env_key")
    monkeypatch.setenv("KRAKEN_API_SECRET", "env_secret")

    # Create a secrets file to ensure env vars are still prioritized
    secrets.encrypt_secrets("file_key", "file_secret", "password")

    key, secret = secrets.load_api_keys()
    assert key == "env_key"
    assert secret == "env_secret"

@mock.patch('getpass.getpass', return_value="password")
def test_load_api_keys_from_encrypted_file(mock_getpass, mock_secrets_dir):
    """Tests loading keys from an encrypted file."""
    secrets.encrypt_secrets("file_key", "file_secret", "password")

    key, secret = secrets.load_api_keys()
    assert key == "file_key"
    assert secret == "file_secret"

# --- Test Interactive Setup ---

@mock.patch('builtins.input', return_value="test_key")
@mock.patch('getpass.getpass', side_effect=["test_secret", "password", "password"])
def test_interactive_setup_success(mock_getpass, mock_input, mock_secrets_dir, mock_kraken_client):
    """Tests the successful interactive setup flow."""
    key, secret = secrets._interactive_setup()

    assert key == "test_key"
    assert secret == "test_secret"
    assert os.path.exists(secrets.SECRETS_FILE_PATH)

    # Verify the client was called for validation
    mock_kraken_client.get_balance.assert_called_once()

    # Verify the saved file can be decrypted
    decrypted = secrets._decrypt_secrets("password")
    assert decrypted["api_key"] == "test_key"

@mock.patch('builtins.input', return_value="bad_key")
@mock.patch('getpass.getpass', return_value="bad_secret")
def test_interactive_setup_validation_failure(mock_getpass, mock_input, mock_secrets_dir, mock_kraken_client):
    """Tests that setup aborts if credential validation fails."""
    mock_kraken_client.get_balance.side_effect = KrakenAPIError("Invalid API Key")

    key, secret = secrets._interactive_setup()

    assert key is None
    assert secret is None
    assert not os.path.exists(secrets.SECRETS_FILE_PATH)
