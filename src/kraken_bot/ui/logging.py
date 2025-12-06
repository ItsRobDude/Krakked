"""UI-specific logging helpers."""

from __future__ import annotations

from fastapi import Request

from kraken_bot.logging_config import get_log_environment, structured_log_extra


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

    if request is not None:
        request_id = getattr(request.state, "request_id", None) or request.headers.get(
            "X-Request-ID"
        )
        http_method = request.method
        path = request.url.path

        route = request.scope.get("route") if hasattr(request, "scope") else None
        if route is not None:
            route_name = getattr(route, "name", None)

    log_event = kwargs.pop("event", event) or "http_request"

    route_metadata = {
        "http_method": http_method,
        "path": path,
        "route_name": route_name,
    }
    for key, value in list(route_metadata.items()):
        if value is None:
            route_metadata.pop(key)

    return structured_log_extra(
        env=get_log_environment(),
        request_id=request_id,
        event=log_event,
        **route_metadata,
        **kwargs,
    )


__all__ = ["build_request_log_extra"]
