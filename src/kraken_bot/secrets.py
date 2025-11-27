# src/kraken_bot/secrets.py

import os
import json
import getpass
import base64
from pathlib import Path
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.backends import default_backend

from kraken_bot.config import get_config_dir
from kraken_bot.connection.rest_client import KrakenRESTClient

# --- Constants ---
SECRETS_FILE_NAME = "secrets.enc"
_SALT_SIZE = 16
_KDF_ITERATIONS = 480000  # Recommended by NIST for PBKDF2

class SecretsDecryptionError(Exception):
    """Raised when decryption fails (wrong password or corrupted file)."""
    pass

# --- Cryptographic Helpers ---

def _derive_key(password: str, salt: bytes) -> bytes:
    """Derives a Fernet-compatible key from a password and salt."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=_KDF_ITERATIONS,
        backend=default_backend()
    )
    return base64.urlsafe_b64encode(kdf.derive(password.encode()))

def encrypt_secrets(api_key: str, api_secret: str, password: str) -> None:
    """Encrypts API credentials and saves them to the secrets file."""
    config_dir = get_config_dir()
    config_dir.mkdir(parents=True, exist_ok=True)
    secrets_path = config_dir / SECRETS_FILE_NAME

    salt = os.urandom(_SALT_SIZE)
    key = _derive_key(password, salt)
    fernet = Fernet(key)

    secrets_data = json.dumps({"api_key": api_key, "api_secret": api_secret}).encode()
    encrypted_data = fernet.encrypt(secrets_data)

    with open(secrets_path, "wb") as f:
        f.write(salt + encrypted_data)

def _decrypt_secrets(password: str) -> dict:
    """Loads and decrypts secrets from the file."""
    secrets_path = get_config_dir() / SECRETS_FILE_NAME
    if not secrets_path.exists():
        raise FileNotFoundError(f"Secrets file not found at {secrets_path}")

    with open(secrets_path, "rb") as f:
        encrypted_blob = f.read()

    salt = encrypted_blob[:_SALT_SIZE]
    encrypted_data = encrypted_blob[_SALT_SIZE:]

    key = _derive_key(password, salt)
    fernet = Fernet(key)

    try:
        decrypted_data = fernet.decrypt(encrypted_data)
        return json.loads(decrypted_data)
    except InvalidToken as e:
        raise SecretsDecryptionError("Invalid password or corrupted secrets file.") from e

# --- First-Time Setup ---

def _interactive_setup() -> tuple[str | None, str | None]:
    """
    Guides the user through the first-time setup process for API keys.
    Validates credentials against Kraken before saving.
    """
    print("--- Kraken API Credential Setup ---")
    print("No API keys found. Please enter them below.")
    api_key = input("Enter your Kraken API Key: ").strip()
    api_secret = getpass.getpass("Enter your Kraken API Secret: ").strip()

    print("\nValidating credentials with Kraken...")
    try:
        # We need a client that can sign requests.
        # But we don't have one fully initialized yet.
        # We'll instantiate a temporary client with these keys.
        client = KrakenRESTClient(api_key=api_key, api_secret=api_secret)
        # Attempt a private call (Balance) to verify permissions
        client.get_private("Balance")
        print("Credentials are valid.")
    except Exception as e:
        print(f"\nCredential validation failed: {e}")
        print("Please check your API key and permissions. Nothing will be saved.")
        return None, None

    while True:
        password = getpass.getpass("Create a master password to encrypt your keys: ")
        password_confirm = getpass.getpass("Confirm master password: ")
        if password == password_confirm:
            break
        print("Passwords do not match. Please try again.")

    try:
        encrypt_secrets(api_key, api_secret, password)
        # Re-construct path just for display
        secrets_path = get_config_dir() / SECRETS_FILE_NAME
        print(f"\nCredentials encrypted and saved to: {secrets_path}")
        print("IMPORTANT: You must remember this password to run the bot.")
        return api_key, api_secret
    except Exception as e:
        print(f"\nAn error occurred while saving secrets: {e}")
        return None, None


# --- Core Credential Loading ---

def load_api_keys() -> tuple[str | None, str | None]:
    """
    Loads API keys, following a specific priority.

    Priority:
    1. Environment variables (KRAKEN_API_KEY, KRAKEN_API_SECRET)
    2. Encrypted secrets file (secrets.enc)
    3. Interactive first-time setup

    Returns:
        A tuple of (api_key, api_secret), or (None, None) if not found.
    """
    # 1. Environment variables
    api_key = os.getenv("KRAKEN_API_KEY")
    api_secret = os.getenv("KRAKEN_API_SECRET")
    if api_key and api_secret:
        print("Loaded API keys from environment variables.")
        return api_key, api_secret

    # 2. Encrypted secrets file
    secrets_path = get_config_dir() / SECRETS_FILE_NAME
    if secrets_path.exists():
        password = os.getenv("KRAKEN_BOT_SECRET_PW") or getpass.getpass("Enter master password to decrypt API keys: ")
        try:
            secrets = _decrypt_secrets(password)
            print("Loaded API keys from encrypted file.")
            return secrets.get("api_key"), secrets.get("api_secret")
        except SecretsDecryptionError as e:
            # Here we let the exception bubble up or handle it.
            # Per feedback, better to raise or let caller handle, but existing logic returned None.
            # We will print the error and return None to trigger interactive setup or failure,
            # BUT the instruction said "Raise a specific exception ... so the caller can decide".
            # Since this is the top-level loader, raising effectively crashes the CLI, which is fine if auth fails.
            # However, `load_api_keys` signature implies returning optional keys.
            # For strict compliance with feedback:
            raise e
        except Exception as e:
            print(f"Error loading secrets: {e}")
            return None, None

    # 3. No credentials found, trigger interactive setup
    return _interactive_setup()
