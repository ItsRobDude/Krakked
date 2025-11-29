"""System and health endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel

from kraken_bot.connection.exceptions import AuthError, ServiceUnavailableError
from kraken_bot.connection.rest_client import KrakenRESTClient
from kraken_bot.ui.models import ApiEnvelope, SystemHealthPayload

logger = logging.getLogger(__name__)

router = APIRouter()


class CredentialPayload(BaseModel):
    """Payload expected from the UI when validating credentials."""

    apiKey: str
    apiSecret: str


def _context(request: Request):
    return request.app.state.context


@router.get("/health", response_model=ApiEnvelope[SystemHealthPayload])
async def system_health(request: Request) -> ApiEnvelope[SystemHealthPayload]:
    try:
        ctx = _context(request)
        data_status = ctx.market_data.get_data_status()
        return ApiEnvelope(
            data=SystemHealthPayload(
                rest_api_reachable=data_status.rest_api_reachable,
                websocket_connected=data_status.websocket_connected,
                streaming_pairs=data_status.streaming_pairs,
                stale_pairs=data_status.stale_pairs,
                subscription_errors=data_status.subscription_errors,
            ),
            error=None,
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Failed to fetch system health")
        return ApiEnvelope(data=None, error=str(exc))


@router.post("/credentials/validate", response_model=ApiEnvelope[dict])
async def validate_credentials(payload: CredentialPayload) -> ApiEnvelope[dict]:
    """Validate API credentials by pinging a private Kraken endpoint."""

    client = KrakenRESTClient(api_key=payload.apiKey, api_secret=payload.apiSecret)

    try:
        client.get_private("Balance")
        return ApiEnvelope(
            data={"success": True, "message": "Credentials validated successfully."},
            error=None,
        )
    except AuthError as exc:
        logger.warning("Credential validation failed", extra={"error": str(exc)})
        return ApiEnvelope(
            data=None,
            error="Authentication failed. Please verify your API key/secret.",
        )
    except ServiceUnavailableError:
        return ApiEnvelope(
            data=None,
            error="Kraken service is unavailable. Try again shortly or continue with caution.",
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Unexpected error during credential validation")
        return ApiEnvelope(data=None, error=str(exc))
