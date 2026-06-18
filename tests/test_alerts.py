from krakked.alerts import WebhookAlertNotifier
from krakked.config_models import AlertConfig


class _Response:
    def __init__(self, *, raises: Exception | None = None) -> None:
        self.raises = raises

    def raise_for_status(self) -> None:
        if self.raises:
            raise self.raises


class _Session:
    def __init__(self, response: _Response) -> None:
        self.response = response
        self.calls: list[dict] = []

    def post(self, url: str, *, json: dict, timeout: float) -> _Response:
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        return self.response


def test_webhook_alert_disabled_does_not_send() -> None:
    session = _Session(_Response())
    notifier = WebhookAlertNotifier(AlertConfig(enabled=False), session=session)

    sent = notifier.send(event="test", title="T", message="M")

    assert sent is False
    assert session.calls == []


def test_webhook_alert_success_posts_payload() -> None:
    session = _Session(_Response())
    notifier = WebhookAlertNotifier(
        AlertConfig(
            enabled=True,
            webhook_url="https://alerts.example/hook",
            timeout_seconds=2.5,
        ),
        session=session,
    )

    sent = notifier.send(
        event="order_submit_unknown",
        title="Unknown",
        message="State unknown",
        context={"local_order_id": "local-1"},
    )

    assert sent is True
    assert session.calls
    call = session.calls[0]
    assert call["url"] == "https://alerts.example/hook"
    assert call["timeout"] == 2.5
    assert call["json"]["event"] == "order_submit_unknown"
    assert call["json"]["context"] == {"local_order_id": "local-1"}


def test_webhook_alert_failure_returns_false() -> None:
    session = _Session(_Response(raises=RuntimeError("down")))
    notifier = WebhookAlertNotifier(
        AlertConfig(enabled=True, webhook_url="https://alerts.example/hook"),
        session=session,
    )

    sent = notifier.send(event="test", title="T", message="M")

    assert sent is False
    assert len(session.calls) == 1


def test_webhook_alert_failure_redacts_url() -> None:
    secret_url = "https://hooks.example/services/T000/B111/SECRETTOKEN"
    session = _Session(_Response(raises=RuntimeError(f"Max retries to {secret_url}")))
    notifier = WebhookAlertNotifier(
        AlertConfig(enabled=True, webhook_url=secret_url), session=session
    )

    sent = notifier.send(event="test", title="T", message="M")

    assert sent is False
    assert notifier.last_attempt is not None
    assert notifier.last_attempt["delivered"] is False
    assert secret_url not in notifier.last_attempt["error"]
    assert "[redacted-webhook-url]" in notifier.last_attempt["error"]


def test_webhook_alert_success_records_last_attempt() -> None:
    session = _Session(_Response())
    notifier = WebhookAlertNotifier(
        AlertConfig(enabled=True, webhook_url="https://alerts.example/hook"),
        session=session,
    )

    notifier.send(event="order_submit_unknown", title="T", message="M")

    assert notifier.last_attempt is not None
    assert notifier.last_attempt["delivered"] is True
    assert notifier.last_attempt["event"] == "order_submit_unknown"
    assert notifier.last_attempt["error"] is None
