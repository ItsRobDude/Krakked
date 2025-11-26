# Kraken Trader

Kraken Trader is a Python-based trading bot for the Kraken cryptocurrency exchange. This initial version focuses on establishing a secure and robust connection module, which will serve as the foundation for all future trading logic.

## Configuration and Secrets Management

The application's configuration is split between two files, located in an OS-specific configuration directory to ensure a clean and organized setup.

-   **Linux:** `~/.config/kraken_bot/`
-   **macOS:** `~/Library/Application Support/kraken_bot/`
-   **Windows:** `%APPDATA%\\kraken_bot\\`

### 1. Non-Sensitive Configuration (`config.yaml`)

This file stores non-sensitive information, such as region settings and capabilities. On the first run, a default `config.yaml` is created automatically:

```yaml
region: "US_CA"
supports_margin: false
supports_futures: false
default_quote: "USD"
```

### 2. Secure Credential Storage (`secrets.enc`)

This file stores your sensitive Kraken API key and secret in an encrypted format. The file is encrypted using a master password that you provide during the first-time setup.

### Credential Loading Precedence

The application loads API credentials in the following order of priority:

1.  **Environment Variables:** If `KRAKEN_API_KEY` and `KRAKEN_API_SECRET` are both set, they will be used. This is the recommended method for production or CI/CD environments.
2.  **Encrypted Secrets File:** If environment variables are not found, the application will attempt to load credentials from `secrets.enc`. You will be prompted for your master password to decrypt the file.
3.  **First-Time Interactive Setup:** If neither of the above methods yields credentials, the application will automatically trigger a first-time setup process.

### First-Time Setup Flow

When you run the application for the first time without any credentials configured, it will guide you through the following secure setup process:

1.  You will be prompted to enter your Kraken API key and secret.
2.  The application will immediately use these credentials to make a test call to a private Kraken endpoint (e.g., to fetch your balance). This **validates** that the credentials are correct and have the necessary permissions.
3.  **Only if the validation is successful**, you will be prompted to create and confirm a master password.
4.  Your credentials will then be encrypted with this password and saved to `secrets.enc`.

This ensures that no invalid credentials are ever saved and that your secrets are securely stored at rest.
