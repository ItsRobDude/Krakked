"""UI-specific logging helpers."""

from __future__ import annotations

from fastapi import Request

from kraken_bot.logging_config import get_log_environment, structured_log_extra


def build_request_log_extra(request: Request | None, **kwargs):
    request_id = None
    if request is not None:
        request_id = getattr(request.state, "request_id", None) or request.headers.get(
            "X-Request-ID"
        )
    return structured_log_extra(env=get_log_environment(), request_id=request_id, **kwargs)


__all__ = ["build_request_log_extra"]
