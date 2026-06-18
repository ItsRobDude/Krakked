"""Tests for display-only alert-delivery visibility in the health payload."""

from __future__ import annotations

from types import SimpleNamespace

from krakked.alerts import WebhookAlertNotifier
from krakked.config_models import AlertConfig
from krakked.ui.routes.system import _alert_status


def _ctx_with_notifier(notifier) -> SimpleNamespace:
    return SimpleNamespace(execution_service=SimpleNamespace(alert_notifier=notifier))


def test_alert_status_surfaces_failed_delivery() -> None:
    notifier = WebhookAlertNotifier(
        AlertConfig(enabled=True, webhook_url="https://hooks.example/x")
    )
    notifier.last_attempt = {
        "event": "order_submit_unknown",
        "attempted_at": "2026-06-18T00:00:00+00:00",
        "delivered": False,
        "error": "ConnectionError: [redacted-webhook-url]",
    }

    status = _alert_status(_ctx_with_notifier(notifier))

    assert status["alerts_enabled"] is True
    assert status["alert_last_event"] == "order_submit_unknown"
    assert status["alert_last_attempt_at"] == "2026-06-18T00:00:00+00:00"
    assert status["alert_last_delivered"] is False
    assert "[redacted-webhook-url]" in status["alert_last_error"]


def test_alert_status_quiet_when_no_attempt_yet() -> None:
    notifier = WebhookAlertNotifier(
        AlertConfig(enabled=True, webhook_url="https://hooks.example/x")
    )

    status = _alert_status(_ctx_with_notifier(notifier))

    assert status["alerts_enabled"] is True
    assert status["alert_last_event"] is None
    assert status["alert_last_delivered"] is None
    assert status["alert_last_error"] is None


def test_alert_status_defaults_without_notifier() -> None:
    status = _alert_status(SimpleNamespace(execution_service=None))

    assert status == {
        "alerts_enabled": False,
        "alert_last_event": None,
        "alert_last_attempt_at": None,
        "alert_last_delivered": None,
        "alert_last_error": None,
    }
