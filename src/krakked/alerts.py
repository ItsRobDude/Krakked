"""Out-of-band alert helpers for fail-closed safety events."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, Optional, Protocol

import requests

from krakked.config_models import AlertConfig
from krakked.logging_config import structured_log_extra

logger = logging.getLogger(__name__)


class WebhookSession(Protocol):
    def post(self, url: str, *, json: dict[str, Any], timeout: float) -> Any:
        """Post a JSON payload and return a response-like object."""


class WebhookAlertNotifier:
    """Send safety alerts to a configured generic webhook endpoint."""

    def __init__(
        self,
        config: Optional[AlertConfig] = None,
        *,
        session: Optional[WebhookSession] = None,
    ) -> None:
        self.config = config or AlertConfig()
        self.session = session or requests.Session()
        # Last delivery attempt, for operator visibility beyond logs.
        self.last_attempt: Optional[dict[str, Any]] = None

    def _redact(self, message: str) -> str:
        """Remove the configured webhook URL (a secret) from a string."""
        url = self.config.webhook_url
        if url:
            message = message.replace(url, "[redacted-webhook-url]")
        return message

    def send(
        self,
        *,
        event: str,
        title: str,
        message: str,
        severity: str = "error",
        context: Optional[dict[str, Any]] = None,
    ) -> bool:
        if not self.config.enabled or not self.config.webhook_url:
            return False

        payload = {
            "event": event,
            "title": title,
            "message": message,
            "severity": severity,
            "timestamp": datetime.now(UTC).isoformat(),
            "context": context or {},
        }

        attempted_at = datetime.now(UTC).isoformat()
        try:
            response = self.session.post(
                self.config.webhook_url,
                json=payload,
                timeout=self.config.timeout_seconds,
            )
            response.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            error_type = type(exc).__name__
            error_summary = self._redact(str(exc))
            self.last_attempt = {
                "event": event,
                "attempted_at": attempted_at,
                "delivered": False,
                "error": f"{error_type}: {error_summary}",
            }
            logger.error(
                "Failed to send alert webhook",
                extra=structured_log_extra(
                    event="alert_webhook_failed",
                    alert_event=event,
                    error_type=error_type,
                    error=error_summary,
                ),
            )
            return False

        self.last_attempt = {
            "event": event,
            "attempted_at": attempted_at,
            "delivered": True,
            "error": None,
        }
        logger.info(
            "Sent alert webhook",
            extra=structured_log_extra(event="alert_webhook_sent", alert_event=event),
        )
        return True
