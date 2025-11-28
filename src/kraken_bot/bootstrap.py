"""Convenience bootstrapper for loading config, credentials, and a REST client."""

import logging
from typing import Tuple

from kraken_bot.config import AppConfig, load_config
from kraken_bot.connection.rest_client import KrakenRESTClient
from kraken_bot.secrets import CredentialResult, CredentialStatus, load_api_keys

logger = logging.getLogger(__name__)


class CredentialBootstrapError(RuntimeError):
    """Raised when credentials cannot be prepared for the REST client."""


def _validate_credentials(result: CredentialResult) -> Tuple[str, str]:
    """Ensure credentials are loaded and usable for the REST client."""

    if result.status is not CredentialStatus.LOADED:
        detail = result.validation_error or result.status.value
        raise CredentialBootstrapError(
            f"Unable to load Kraken API credentials ({result.status.value}): {detail}"
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
) -> Tuple[KrakenRESTClient, AppConfig]:
    """Load configuration, fetch credentials, and return a ready REST client.

    Args:
        allow_interactive_setup: Whether credential loading may prompt the user
            to perform the interactive secrets flow when no credentials exist.

    Returns:
        A tuple of ``(KrakenRESTClient, AppConfig)`` ready for use.

    Raises:
        CredentialBootstrapError: If credentials cannot be loaded or are invalid.
    """

    config = load_config()
    credential_result = load_api_keys(allow_interactive_setup=allow_interactive_setup)
    api_key, api_secret = _validate_credentials(credential_result)

    client = KrakenRESTClient(api_key=api_key, api_secret=api_secret)
    return client, config


__all__ = ["bootstrap", "CredentialBootstrapError", "AppConfig", "KrakenRESTClient"]
