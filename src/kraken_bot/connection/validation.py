from __future__ import annotations

from kraken_bot.connection.exceptions import (
    AuthError,
    KrakenAPIError,
    RateLimitError,
    ServiceUnavailableError,
)
from kraken_bot.connection.rest_client import KrakenRESTClient
from kraken_bot.credentials import CredentialResult, CredentialStatus


def validate_credentials(api_key: str, api_secret: str) -> CredentialResult:
    """
    Perform a low risk private call to validate credentials and classify the failure.

    This NEVER logs anything and NEVER raises a secret bearing exception.
    Callers can decide whether to persist unvalidated creds based on the flags.
    """
    client = KrakenRESTClient(api_key=api_key, api_secret=api_secret)

    try:
        # Low risk probe per contract: private Balance.
        client.get_private("Balance")
        return CredentialResult(
            api_key=api_key,
            api_secret=api_secret,
            status=CredentialStatus.LOADED,
            source="validation",
            validated=True,
            can_force_save=False,
            validation_error=None,
            error=None,
        )
    except AuthError as exc:
        return CredentialResult(
            api_key=api_key,
            api_secret=api_secret,
            status=CredentialStatus.AUTH_ERROR,
            source="validation",
            validated=False,
            can_force_save=False,
            validation_error=str(exc),
            error=exc,
        )
    except (RateLimitError, ServiceUnavailableError, KrakenAPIError) as exc:
        return CredentialResult(
            api_key=api_key,
            api_secret=api_secret,
            status=CredentialStatus.SERVICE_ERROR,
            source="validation",
            validated=False,
            can_force_save=True,
            validation_error=str(exc),
            error=exc,
        )
    except Exception as exc:  # noqa: BLE001
        return CredentialResult(
            api_key=api_key,
            api_secret=api_secret,
            status=CredentialStatus.SERVICE_ERROR,
            source="validation",
            validated=False,
            can_force_save=True,
            validation_error=str(exc),
            error=exc,
        )
