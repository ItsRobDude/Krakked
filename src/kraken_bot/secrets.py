# src/kraken_bot/secrets.py

import base64
import getpass
import json
import os
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from kraken_bot.config import get_config_dir
from kraken_bot.connection.validation import validate_credentials
from kraken_bot.credentials import CredentialResult, CredentialStatus

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
        raise SecretsDecryptionError(
            "Invalid password or corrupted secrets file."
        ) from e


def _prompt_for_password(*, create: bool) -> str:
    if create:
        while True:
            password = getpass.getpass("Create a master password to encrypt your keys: ")
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
    validation = validate_credentials(api_key, api_secret)

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
        choice = input(
            "Save these credentials as UNVALIDATED anyway? [y/N]: "
        ).strip().lower()
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
    if bool(api_key) ^ bool(api_secret):
        message = "Both KRAKEN_API_KEY and KRAKEN_API_SECRET must be set together."
        print(message)
        return CredentialResult(
            api_key,
            api_secret,
            CredentialStatus.AUTH_ERROR,
            source="environment",
            validation_error=message,
        )

    if api_key and api_secret:
        print("Loaded API keys from environment variables.")
        return CredentialResult(
            api_key, api_secret, CredentialStatus.LOADED, source="environment"
        )

    secrets_path = get_config_dir() / SECRETS_FILE_NAME
    if secrets_path.exists():
        env_password = os.getenv("KRAKEN_BOT_SECRET_PW")
        if not env_password and not allow_interactive_setup:
            message = (
                "Encrypted credentials found but KRAKEN_BOT_SECRET_PW password environment variable "
                "is not set; credentials are unavailable in non-interactive mode."
            )
            print(message)
            return CredentialResult(
                None,
                None,
                CredentialStatus.MISSING_PASSWORD,
                source="secrets_file",
                validation_error=message,
            )

        password = env_password or getpass.getpass(
            "Enter master password to decrypt API keys: "
        )
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
