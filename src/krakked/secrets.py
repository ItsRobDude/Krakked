# src/krakked/secrets.py

import base64
import getpass
import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

import krakked.connection.validation as validation_mod
from krakked.config import get_config_dir
from krakked.credentials import CredentialResult, CredentialStatus
from krakked.password_store import get_saved_master_password

# --- Constants ---
SECRETS_FILE_NAME = "secrets.enc"
_SALT_SIZE = 16
_KDF_ITERATIONS = 480000  # Recommended by NIST for PBKDF2

logger = logging.getLogger(__name__)


class SecretsDecryptionError(Exception):
    """Raised when decryption fails (wrong password or corrupted file)."""

    pass


# Add logger
logger = logging.getLogger(__name__)

# --- Session In-Memory Store ---

_session_lock = threading.Lock()
_session_master_passwords: dict[str, str] = {}


def set_session_master_password(account_id: str, password: str | None) -> None:
    with _session_lock:
        if password is None:
            _session_master_passwords.pop(account_id, None)
        else:
            _session_master_passwords[account_id] = password


def get_session_master_password(account_id: str) -> str | None:
    with _session_lock:
        return _session_master_passwords.get(account_id)


# --- Cryptographic Helpers ---


def _derive_key(password: str, salt: bytes) -> bytes:
    """Derives a Fernet-compatible key from a password and salt."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=_KDF_ITERATIONS,
        backend=default_backend(),
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
    secrets_path: Path | None = None,
) -> None:
    """Encrypts API credentials and saves them to the secrets file atomically."""
    if secrets_path is None:
        config_dir = get_config_dir()
        config_dir.mkdir(parents=True, exist_ok=True)
        secrets_path = config_dir / SECRETS_FILE_NAME

    # Ensure parent directory exists for non-default paths
    secrets_path.parent.mkdir(parents=True, exist_ok=True)

    # FIX #2: Write to a temporary file first
    tmp_path = secrets_path.with_suffix(".tmp")

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

    # FIX #2: Open tmp_path instead of secrets_path
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(salt + encrypted_data)
    tmp_path.chmod(0o600)

    # FIX #2: Atomic swap
    tmp_path.replace(secrets_path)


def _decrypt_secrets(password: str, secrets_path: Path | None = None) -> dict:
    """Loads and decrypts secrets from the file."""
    if secrets_path is None:
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
        raise SecretsDecryptionError(
            "Invalid password or corrupted secrets file."
        ) from e


def _prompt_for_password(*, create: bool) -> str:
    if create:
        while True:
            password = getpass.getpass(
                "Create a master password to encrypt your keys: "
            )
            password_confirm = getpass.getpass("Confirm master password: ")
            if password == password_confirm:
                return password
            print("Passwords do not match. Please try again.")

    return getpass.getpass("Enter master password to encrypt your keys: ")


# --- First-Time Setup ---


def _interactive_setup() -> CredentialResult:
    """
    Guides the user through the first-time setup process for API keys.
    Validates credentials against Kraken before saving and returns structured results.
    """
    print("--- Kraken API Credential Setup ---")
    print("No API keys found. Please enter them below.")
    api_key = input("Enter your Kraken API key: ").strip()
    api_secret = getpass.getpass("Enter your Kraken API secret: ").strip()

    if not api_key or not api_secret:
        print("API key and secret are required. Nothing will be saved.")
        return CredentialResult(
            api_key=None,
            api_secret=None,
            status=CredentialStatus.AUTH_ERROR,
            source="interactive",
            validated=False,
            can_force_save=False,
            validation_error="Missing API key/secret",
        )

    print("\nValidating credentials with Kraken...")
    validation = validation_mod.validate_credentials(api_key, api_secret)

    if validation.status is CredentialStatus.AUTH_ERROR:
        print(f"\nCredential validation failed: {validation.validation_error}")
        print("Please check your API key and permissions. Nothing will be saved.")
        return CredentialResult(
            api_key=None,
            api_secret=None,
            status=CredentialStatus.AUTH_ERROR,
            source="interactive",
            validated=False,
            can_force_save=False,
            validation_error=validation.validation_error,
            error=validation.error,
        )

    if validation.status is CredentialStatus.SERVICE_ERROR:
        print(
            "\nCould not validate credentials due to a service/network issue: "
            f"{validation.validation_error}"
        )
        choice = (
            input("Save these credentials as UNVALIDATED anyway? [y/N]: ")
            .strip()
            .lower()
        )
        if choice not in ("y", "yes"):
            print("Credentials were NOT saved.")
            return CredentialResult(
                api_key=None,
                api_secret=None,
                status=CredentialStatus.SERVICE_ERROR,
                source="interactive",
                validated=False,
                can_force_save=True,
                validation_error=validation.validation_error,
                error=validation.error,
            )

        password = _prompt_for_password(create=True)
        # Interactive setup typically for default account/first run, so no specific path/id passed
        persist_api_keys(
            api_key=api_key,
            api_secret=api_secret,
            password=password,
            validated=False,
            validation_error=validation.validation_error,
            force_save_unvalidated=True,
        )
        print("Credentials saved as UNVALIDATED. You can re run validation later.")
        return CredentialResult(
            api_key=api_key,
            api_secret=api_secret,
            status=CredentialStatus.LOADED,
            source="secrets_file",
            validated=False,
            can_force_save=True,
            validation_error=validation.validation_error,
            error=validation.error,
        )

    password = _prompt_for_password(create=True)
    persist_api_keys(
        api_key=api_key,
        api_secret=api_secret,
        password=password,
        validated=True,
        validation_error=None,
    )
    print("Credentials encrypted and saved to secrets.enc.")
    return CredentialResult(
        api_key=api_key,
        api_secret=api_secret,
        status=CredentialStatus.LOADED,
        source="secrets_file",
        validated=True,
        can_force_save=False,
        validation_error=None,
        error=None,
    )


def persist_api_keys(
    api_key: str,
    api_secret: str,
    password: str,
    *,
    validated: bool | None = None,
    validation_error: str | None = None,
    force_save_unvalidated: bool = False,
    secrets_path: Path | None = None,
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
        secrets_path=secrets_path,
    )


def unlock_secrets(password: str, secrets_path: Path | None = None) -> dict:
    """
    Attempts to decrypt the secrets file with the provided password.
    Returns the dictionary of secrets if successful.
    """
    return _decrypt_secrets(password, secrets_path=secrets_path)


def delete_secrets(secrets_path: Path | None = None) -> None:
    """
    Safely deletes the secrets file if it exists.
    """
    if secrets_path is None:
        secrets_path = get_config_dir() / SECRETS_FILE_NAME

    if secrets_path.exists():
        secrets_path.unlink()


# --- Core Credential Loading ---


def load_api_keys(
    allow_interactive_setup: bool = False,
    secrets_path: Path | None = None,
    account_id: str = "default",
) -> CredentialResult:
    """
    Loads API keys with strict precedence checks to prevent 'Shadow Configuration'.

    Args:
        allow_interactive_setup: If true, prompts user on CLI if keys missing.
        secrets_path: Path to the encrypted secrets file. Defaults to standard location.
        account_id: The ID of the account to load keys for (used for password lookup).
    """
    api_key = os.getenv("KRAKEN_API_KEY")
    api_secret = os.getenv("KRAKEN_API_SECRET")

    # Treat whitespace-only env vars as missing.
    if api_key is not None:
        api_key = api_key.strip() or None
    if api_secret is not None:
        api_secret = api_secret.strip() or None

    # FIX #4: Deep Logic for Shadow Configuration
    if bool(api_key) ^ bool(api_secret):
        missing = "KRAKEN_API_SECRET" if api_key else "KRAKEN_API_KEY"
        logger.warning(
            "AMBIGUOUS CONFIGURATION DETECTED:\n"
            f"   Found environment variable for API Key/Secret, but {missing} is missing.\n"
            "   -> ACTION: Discarding broken environment variables.\n"
            "   -> ACTION: Falling back to 'secrets.enc' (if available).\n"
            "   PLEASE FIX YOUR ENVIRONMENT VARIABLES TO AVOID USING OLD CREDENTIALS."
        )
        api_key = None
        api_secret = None

    if api_key and api_secret:
        logger.info("Loaded API keys from environment variables.")
        return CredentialResult(
            api_key, api_secret, CredentialStatus.LOADED, source="environment"
        )

    if secrets_path is None:
        secrets_path = get_config_dir() / SECRETS_FILE_NAME

    if secrets_path.exists():
        password = (
            os.getenv("KRAKKED_SECRET_PW")
            or get_session_master_password(account_id)
            or get_saved_master_password(account_id)
        )

        if not password and not allow_interactive_setup:
            message = (
                "Encrypted credentials found but master password is not available "
                "(env var, session, or keychain). Credentials unavailable in non-interactive mode."
            )
            print(message)
            return CredentialResult(
                None,
                None,
                CredentialStatus.MISSING_PASSWORD,
                source="secrets_file",
                validation_error=message,
            )

        if not password:
            password = getpass.getpass(
                f"Enter master password for account '{account_id}': "
            )

        try:
            secrets = _decrypt_secrets(password, secrets_path=secrets_path)
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
            message = str(e)
            print("Failed to decrypt secrets file with provided password.")
            return CredentialResult(
                None,
                None,
                CredentialStatus.AUTH_ERROR,
                source="secrets_file",
                validation_error=message,
                error=e,
            )
        except Exception as e:
            print(f"Error loading secrets: {e}")
            return CredentialResult(
                None,
                None,
                CredentialStatus.SERVICE_ERROR,
                source="secrets_file",
                error=e,
            )

    if allow_interactive_setup:
        return _interactive_setup()

    return CredentialResult(None, None, CredentialStatus.NOT_FOUND, source="none")
