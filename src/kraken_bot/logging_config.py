"""Structured logging configuration helpers for the Kraken bot."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict

DEFAULT_ENV = os.getenv("KRAKEN_BOT_ENV", os.getenv("ENV", "local"))


class JsonFormatter(logging.Formatter):
    """Minimal JSON formatter that emits a stable set of fields.

    The formatter preserves all values provided through the logging ``extra``
    dictionary so callers can attach contextual identifiers (e.g. ``event``,
    ``plan_id``, ``strategy_id``, ``order_id``, ``pair``) without worrying about
    them being dropped. The ``event`` field is treated as a lightweight,
    machine-readable label for the log line that downstream systems can rely on
    for alerting or analytics.
    """

    def __init__(self, env: str | None = None) -> None:
        super().__init__()
        self.env = env or DEFAULT_ENV

    def format(self, record: logging.LogRecord) -> str:  # type: ignore[override]
        log_time = datetime.fromtimestamp(record.created, tz=timezone.utc)
        payload: Dict[str, Any] = {
            "timestamp": log_time.isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "event": getattr(record, "event", None),
            "env": getattr(record, "env", self.env),
            "strategy_id": getattr(record, "strategy_id", None),
            "plan_id": getattr(record, "plan_id", None),
            "request_id": getattr(record, "request_id", None),
        }

        for key, value in record.__dict__.items():
            if key in {
                "name",
                "msg",
                "args",
                "levelname",
                "levelno",
                "pathname",
                "filename",
                "module",
                "exc_info",
                "exc_text",
                "stack_info",
                "lineno",
                "funcName",
                "created",
                "msecs",
                "relativeCreated",
                "thread",
                "threadName",
                "processName",
                "process",
            }:
                continue
            payload.setdefault(key, value)

        return json.dumps(payload)


def configure_logging(level: int = logging.INFO, env: str | None = None) -> None:
    """Configure root logging with a JSON formatter and stdout handler."""

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)

    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter(env=env))
    root_logger.addHandler(handler)


def structured_log_extra(
    *,
    env: str | None = None,
    request_id: str | None = None,
    event: str | None = None,
    strategy_id: str | None = None,
    plan_id: str | None = None,
    pair: str | None = None,
    order_id: str | None = None,
    kraken_order_id: str | None = None,
    local_order_id: str | None = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Build a consistent set of logging extras with common fields.

    The ``event`` key should be a short, stable identifier for the log. Common
    contextual identifiers (``plan_id``, ``strategy_id``, ``order_id``,
    ``local_order_id``, ``kraken_order_id``, ``pair``, etc.) can be provided as
    keyword arguments and will be forwarded into the structured payload. All
    identifiers are optional; when omitted they are simply absent from the
    resulting ``extra`` dict, keeping existing call sites backwards compatible.
    Additional custom fields are preserved via ``**kwargs``.
    """

    extra: Dict[str, Any] = {
        "event": kwargs.pop("event", event),
        "env": env or DEFAULT_ENV,
        "strategy_id": kwargs.pop("strategy_id", strategy_id),
        "plan_id": kwargs.pop("plan_id", plan_id),
        "request_id": request_id,
    }

    identifier_fields = {
        "pair": kwargs.pop("pair", pair),
        "order_id": kwargs.pop("order_id", order_id),
        "kraken_order_id": kwargs.pop("kraken_order_id", kraken_order_id),
        "local_order_id": kwargs.pop("local_order_id", local_order_id),
    }
    for key, value in identifier_fields.items():
        if value is not None:
            extra[key] = value

    extra.update(kwargs)
    return extra


def get_log_environment() -> str:
    """Expose the configured environment for downstream helpers."""

    return DEFAULT_ENV


__all__: list[str] = [
    "configure_logging",
    "structured_log_extra",
    "get_log_environment",
]
