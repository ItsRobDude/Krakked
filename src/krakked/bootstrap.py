"""Convenience bootstrapper for loading config, credentials, and a REST client."""

import logging
from typing import Tuple

from krakked.accounts import ensure_default_account, resolve_secrets_path
from krakked.config import AppConfig, load_config
from krakked.connection.rate_limiter import RateLimiter
from krakked.connection.rest_client import KrakenRESTClient
from krakked.credentials import CredentialResult, CredentialStatus
from krakked.safety import check_safety, log_safety_status
from krakked.secrets import load_api_keys

logger = logging.getLogger(__name__)


class CredentialBootstrapError(RuntimeError):
    """Raised when credentials cannot be prepared for the REST client."""


def _validate_credentials(result: CredentialResult) -> Tuple[str, str]:
    """Ensure credentials are loaded and usable for the REST client."""

    if result.status is CredentialStatus.MISSING_PASSWORD:
        raise CredentialBootstrapError(
            result.validation_error
            or "Encrypted credentials are locked. Provide the master password via KRAKKED_SECRET_PW, "
            "a prior UI unlock (session), or OS keychain (remember me)."
        )

    if result.status is not CredentialStatus.LOADED:
        detail = result.validation_error or result.status.value
        raise CredentialBootstrapError(
            "Unable to load Kraken API credentials "
            f"({result.status.value}): {detail}"
        )

    if not result.api_key or not result.api_secret:
        raise CredentialBootstrapError(
            "Credentials were reported as loaded but API key/secret are missing."
        )

    if result.validated is False:
        logger.warning(
            "Using unvalidated API credentials: %s",
            result.validation_error or "validation was skipped",
            extra={
                "event": "credentials_unvalidated",
                "source": result.source,
                "validation_error": result.validation_error,
            },
        )

    return result.api_key, result.api_secret


def bootstrap(
    allow_interactive_setup: bool = True,
) -> Tuple[KrakenRESTClient, AppConfig, RateLimiter]:
    """Load configuration, fetch credentials, and return a ready REST client.

    Args:
        allow_interactive_setup: Whether credential loading may prompt the user
            to perform the interactive secrets flow when no credentials exist.

    Returns:
        A tuple of ``(KrakenRESTClient, AppConfig, RateLimiter)`` ready for use.

    Raises:
        CredentialBootstrapError: If credentials cannot be loaded or are invalid.
    """

    config = load_config()
    safety_status = check_safety(config)
    log_safety_status(safety_status)

    # Resolve account and secrets path
    account_id = config.session.account_id or "default"
    # Ensure registry integrity (creates default if missing)
    ensure_default_account()

    try:
        secrets_path = resolve_secrets_path(None, account_id)
    except ValueError as e:
        # Should not happen given ensure_default_account, but fail gracefully
        logger.error(f"Failed to resolve secrets for account {account_id}: {e}")
        # Fallback to default secrets location if resolution fails
        from krakked.config import get_config_dir

        secrets_path = get_config_dir() / "secrets.enc"

    credential_result = load_api_keys(
        allow_interactive_setup=allow_interactive_setup,
        secrets_path=secrets_path,
        account_id=account_id,
    )
    api_key, api_secret = _validate_credentials(credential_result)

    rate_limiter = RateLimiter(calls_per_second=0.5)
    client = KrakenRESTClient(
        api_key=api_key, api_secret=api_secret, rate_limiter=rate_limiter
    )
    return client, config, rate_limiter


__all__ = [
    "bootstrap",
    "CredentialBootstrapError",
    "AppConfig",
    "KrakenRESTClient",
    "RateLimiter",
]
