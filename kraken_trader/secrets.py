import os
import json
import getpass
import base64
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.backends import default_backend

from .config import get_config_dir
from .connection import KrakenClient

# --- Constants ---
SECRETS_FILE_PATH = os.path.join(get_config_dir(), "secrets.enc")
_SALT_SIZE = 16
_KDF_ITERATIONS = 480000  # Recommended by NIST for PBKDF2

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
    salt = os.urandom(_SALT_SIZE)
    key = _derive_key(password, salt)
    fernet = Fernet(key)

    secrets_data = json.dumps({"api_key": api_key, "api_secret": api_secret}).encode()
    encrypted_data = fernet.encrypt(secrets_data)

    with open(SECRETS_FILE_PATH, "wb") as f:
        f.write(salt + encrypted_data)

def _decrypt_secrets(password: str) -> dict:
    """Loads and decrypts secrets from the file."""
    with open(SECRETS_FILE_PATH, "rb") as f:
        encrypted_blob = f.read()

    salt = encrypted_blob[:_SALT_SIZE]
    encrypted_data = encrypted_blob[_SALT_SIZE:]

    key = _derive_key(password, salt)
    fernet = Fernet(key)

    try:
        decrypted_data = fernet.decrypt(encrypted_data)
        return json.loads(decrypted_data)
    except InvalidToken:
        raise ValueError("Invalid password or corrupted secrets file.")

# --- First-Time Setup ---

def interactive_setup() -> tuple[str | None, str | None]:
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
        client = KrakenClient(api_key, api_secret)
        client.get_balance()
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
        print(f"\nCredentials encrypted and saved to: {SECRETS_FILE_PATH}")
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
    if os.path.exists(SECRETS_FILE_PATH):
        password = os.getenv("KRAKEN_BOT_SECRET_PW") or getpass.getpass("Enter master password to decrypt API keys: ")
        try:
            secrets = _decrypt_secrets(password)
            print("Loaded API keys from encrypted file.")
            return secrets.get("api_key"), secrets.get("api_secret")
        except (ValueError, FileNotFoundError) as e:
            print(f"Error loading secrets: {e}")
            return None, None

    # 3. No credentials found, trigger interactive setup
    return interactive_setup()
