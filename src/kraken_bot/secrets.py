# src/kraken_bot/secrets.py

import os
import json
import getpass
import base64
from enum import Enum
from dataclasses import dataclass
from datetime import datetime, timezone
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.backends import default_backend

from kraken_bot.config import get_config_dir
from kraken_bot.connection.rest_client import KrakenRESTClient
from kraken_bot.connection.exceptions import AuthError, ServiceUnavailableError, KrakenAPIError

# --- Constants ---
SECRETS_FILE_NAME = "secrets.enc"
_SALT_SIZE = 16
_KDF_ITERATIONS = 480000  # Recommended by NIST for PBKDF2


class CredentialStatus(Enum):
    """Explicit status for credential loading/validation flows."""

    LOADED = "loaded"
    NOT_FOUND = "not_found"
    AUTH_ERROR = "auth_error"
    SERVICE_ERROR = "service_error"
    DECRYPTION_FAILED = "decryption_failed"


@dataclass
class CredentialResult:
    api_key: str | None
    api_secret: str | None
    status: CredentialStatus
    source: str | None = None
    validated: bool | None = None
    can_force_save: bool = False
    validation_error: str | None = None
    error: Exception | None = None

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

def encrypt_secrets(
    api_key: str,
    api_secret: str,
    password: str,
    *,
    validated: bool | None = None,
    validated_at: datetime | None = None,
    validation_error: str | None = None,
) -> None:
    """Encrypts API credentials and saves them to the secrets file.

    Validation metadata is stored alongside the keys to inform later flows
    whether the credentials were confirmed against Kraken (or why they were
    not).
    """
    config_dir = get_config_dir()
    config_dir.mkdir(parents=True, exist_ok=True)
    secrets_path = config_dir / SECRETS_FILE_NAME

    salt = os.urandom(_SALT_SIZE)
    key = _derive_key(password, salt)
    fernet = Fernet(key)

    metadata_timestamp = None
    if validated is not None or validation_error is not None:
        metadata_timestamp = (validated_at or datetime.now(timezone.utc)).isoformat()

    secrets_data = json.dumps(
        {
            "api_key": api_key,
            "api_secret": api_secret,
            "validated": validated,
            "validated_at": metadata_timestamp,
            "validation_error": validation_error,
        }
    ).encode()
    encrypted_data = fernet.encrypt(secrets_data)

    # Ensure file is created with user-only permissions
    fd = os.open(secrets_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(salt + encrypted_data)
    secrets_path.chmod(0o600)

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

def _interactive_setup() -> CredentialResult:
    """
    Guides the user through the first-time setup process for API keys.
    Validates credentials against Kraken before saving and returns structured results.
    """
    print("--- Kraken API Credential Setup ---")
    print("No API keys found. Please enter them below.")
    api_key = input("Enter your Kraken API Key: ").strip()
    api_secret = getpass.getpass("Enter your Kraken API Secret: ").strip()

    print("\nValidating credentials with Kraken...")
    try:
        client = KrakenRESTClient(api_key=api_key, api_secret=api_secret)
        client.get_private("Balance")
        print("Credentials are valid.")
        validation_error: str | None = None
    except AuthError as e:
        print(f"\nCredential validation failed: {e}")
        print("Please check your API key and permissions. Nothing will be saved.")
        return CredentialResult(
            api_key,
            api_secret,
            CredentialStatus.AUTH_ERROR,
            source="interactive",
            validated=False,
            can_force_save=False,
            validation_error=str(e),
            error=e,
        )
    except (ServiceUnavailableError, KrakenAPIError) as e:
        print(f"\nCould not validate credentials due to a service/network issue: {e}")
        print("Please retry later. Keys have not been saved.")
        return CredentialResult(
            api_key,
            api_secret,
            CredentialStatus.SERVICE_ERROR,
            source="interactive",
            validated=False,
            can_force_save=True,
            validation_error=str(e),
            error=e,
        )
    except Exception as e:
        print(f"\nAn unexpected error occurred during validation: {e}")
        return CredentialResult(
            api_key,
            api_secret,
            CredentialStatus.SERVICE_ERROR,
            source="interactive",
            validated=False,
            can_force_save=True,
            validation_error=str(e),
            error=e,
        )

    while True:
        password = getpass.getpass("Create a master password to encrypt your keys: ")
        password_confirm = getpass.getpass("Confirm master password: ")
        if password == password_confirm:
            break
        print("Passwords do not match. Please try again.")

    try:
        persist_api_keys(
            api_key,
            api_secret,
            password,
            validated=True,
            validation_error=validation_error,
        )
        secrets_path = get_config_dir() / SECRETS_FILE_NAME
        print(f"\nCredentials encrypted and saved to: {secrets_path}")
        print("IMPORTANT: You must remember this password to run the bot.")
        return CredentialResult(
            api_key,
            api_secret,
            CredentialStatus.LOADED,
            source="interactive",
            validated=True,
            validation_error=validation_error,
        )
    except Exception as e:
        print(f"\nAn error occurred while saving secrets: {e}")
        return CredentialResult(
            None,
            None,
            CredentialStatus.SERVICE_ERROR,
            source="interactive",
            validated=True,
            validation_error=validation_error,
            error=e,
        )


def persist_api_keys(
    api_key: str,
    api_secret: str,
    password: str,
    *,
    validated: bool | None = None,
    validation_error: str | None = None,
    force_save_unvalidated: bool = False,
) -> None:
    """Persist API keys to the encrypted secrets file with validation metadata.

    Args:
        validated: Whether the credentials were successfully validated against Kraken.
        validation_error: Optional textual reason validation failed.
        force_save_unvalidated: If True, secrets are saved even when validation
            did not succeed. This should only be used when the caller explicitly
            allows storing unvalidated credentials (e.g., due to service outages).
    """

    if (validated is False) and not force_save_unvalidated:
        raise ValueError(
            "Refusing to save unvalidated credentials without force_save_unvalidated=True."
        )

    encrypt_secrets(
        api_key,
        api_secret,
        password,
        validated=validated,
        validation_error=validation_error,
    )


# --- Core Credential Loading ---

def load_api_keys(allow_interactive_setup: bool = False) -> CredentialResult:
    """
    Loads API keys, following a specific priority, and returns a structured result
    describing how credentials were obtained or why they could not be retrieved.

    Args:
        allow_interactive_setup: When True, the function will prompt the user to
            perform the interactive setup flow if no credentials are available.
            When False, missing credentials return a NOT_FOUND status without
            prompting, enabling non-interactive environments to detect the state.
    """
    api_key = os.getenv("KRAKEN_API_KEY")
    api_secret = os.getenv("KRAKEN_API_SECRET")
    if api_key and api_secret:
        print("Loaded API keys from environment variables.")
        return CredentialResult(api_key, api_secret, CredentialStatus.LOADED, source="environment")

    secrets_path = get_config_dir() / SECRETS_FILE_NAME
    if secrets_path.exists():
        password = os.getenv("KRAKEN_BOT_SECRET_PW") or getpass.getpass("Enter master password to decrypt API keys: ")
        try:
            secrets = _decrypt_secrets(password)
            print("Loaded API keys from encrypted file.")
            return CredentialResult(
                secrets.get("api_key"),
                secrets.get("api_secret"),
                CredentialStatus.LOADED,
                source="secrets_file",
                validated=secrets.get("validated"),
                validation_error=secrets.get("validation_error"),
            )
        except SecretsDecryptionError as e:
            print("Failed to decrypt secrets file with provided password.")
            return CredentialResult(None, None, CredentialStatus.DECRYPTION_FAILED, source="secrets_file", error=e)
        except Exception as e:
            print(f"Error loading secrets: {e}")
            return CredentialResult(None, None, CredentialStatus.SERVICE_ERROR, source="secrets_file", error=e)

    if allow_interactive_setup:
        return _interactive_setup()

    return CredentialResult(None, None, CredentialStatus.NOT_FOUND, source="none")
