"""UI-specific logging helpers."""

from __future__ import annotations

from fastapi import Request

from krakked.logging_config import get_log_environment, structured_log_extra


def build_request_log_extra(
    request: Request | None, event: str | None = None, **kwargs
):
    """Build a structured ``extra`` payload for UI HTTP logs.

    Ensures that every log entry contains a ``request_id`` and ``event`` along with
    basic route metadata derived from the provided ``Request`` when available.
    """

    request_id = None
    http_method = None
    path = None
    route_name = None
    client_ip = None
    forwarded_for = None
    account_id = None

    if request is not None:
        request_id = getattr(request.state, "request_id", None) or request.headers.get(
            "X-Request-ID"
        )
        http_method = request.method
        path = request.url.path
        if request.client is not None:
            client_ip = request.client.host
        forwarded_for = request.headers.get("X-Forwarded-For") or request.headers.get(
            "Forwarded"
        )

        route = request.scope.get("route") if hasattr(request, "scope") else None
        if route is not None:
            route_name = getattr(route, "name", None)

        app = getattr(request, "app", None)
        app_state = getattr(app, "state", None)
        context = getattr(app_state, "context", None)
        session = getattr(context, "session", None)
        account_id = getattr(session, "account_id", None)

    log_event = kwargs.pop("event", event) or "http_request"

    route_metadata = {
        "http_method": http_method,
        "path": path,
        "route_name": route_name,
        "client_ip": client_ip,
        "forwarded_for": forwarded_for,
        "account_id": account_id,
    }
    for key, value in list(route_metadata.items()):
        if value is None or key in kwargs:
            route_metadata.pop(key)

    return structured_log_extra(
        env=get_log_environment(),
        request_id=request_id,
        event=log_event,
        **route_metadata,
        **kwargs,
    )


__all__ = ["build_request_log_extra"]
