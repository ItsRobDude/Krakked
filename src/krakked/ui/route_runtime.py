"""Shared runtime helpers for UI route reads."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable
from typing import TypeVar

from fastapi import Request

from krakked.ui.logging import build_request_log_extra
from krakked.ui.models import ApiEnvelope

T = TypeVar("T")

DEFAULT_UI_ROUTE_TIMEOUT_SECONDS = 2.5

_ROUTE_GUARDS: dict[str, threading.Lock] = {}
_ROUTE_GUARDS_LOCK = threading.Lock()


def _route_guard(route_key: str) -> threading.Lock:
    with _ROUTE_GUARDS_LOCK:
        guard = _ROUTE_GUARDS.get(route_key)
        if guard is None:
            guard = threading.Lock()
            _ROUTE_GUARDS[route_key] = guard
        return guard


async def run_bounded_route_read(
    request: Request,
    *,
    route_key: str,
    reader: Callable[[], T],
    logger: logging.Logger,
    timeout_seconds: float | None = None,
    busy_error: str = "Dashboard data refresh is already in progress.",
    timeout_error: str = "Dashboard data request timed out.",
    failure_event: str,
) -> ApiEnvelope[T]:
    """Run a sync UI read with timeout and single-flight protection."""

    timeout_seconds = timeout_seconds or DEFAULT_UI_ROUTE_TIMEOUT_SECONDS
    guard = _route_guard(route_key)
    if not guard.acquire(blocking=False):
        logger.warning(
            "Dashboard route busy",
            extra=build_request_log_extra(
                request,
                event="dashboard_route_busy",
                route_key=route_key,
            ),
        )
        return ApiEnvelope(data=None, error=busy_error)

    started = time.perf_counter()
    loop = asyncio.get_running_loop()
    result_future: asyncio.Future[T] = loop.create_future()

    def _complete_success(data: T) -> None:
        if not result_future.done():
            result_future.set_result(data)

    def _complete_error(exc: Exception) -> None:
        if not result_future.done():
            result_future.set_exception(exc)

    def _schedule_completion(callback: Callable[..., None], *args: object) -> None:
        try:
            loop.call_soon_threadsafe(callback, *args)
        except RuntimeError:
            # The caller already timed out and the request loop is gone.
            return

    def _invoke() -> None:
        try:
            data = reader()
        except Exception as exc:  # pragma: no cover - defensive
            _schedule_completion(_complete_error, exc)
        else:
            _schedule_completion(_complete_success, data)
        finally:
            guard.release()

    worker = threading.Thread(
        target=_invoke,
        name=f"krakked-ui-route-{route_key}",
        daemon=True,
    )
    worker.start()

    try:
        data = await asyncio.wait_for(result_future, timeout_seconds)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        if elapsed_ms >= 500:
            logger.info(
                "Dashboard route completed",
                extra=build_request_log_extra(
                    request,
                    event="dashboard_route_completed",
                    route_key=route_key,
                    elapsed_ms=elapsed_ms,
                ),
            )
        return ApiEnvelope(data=data, error=None)
    except asyncio.TimeoutError:
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        if not result_future.done():
            result_future.cancel()
        logger.warning(
            "Dashboard route timed out",
            extra=build_request_log_extra(
                request,
                event="dashboard_route_timeout",
                route_key=route_key,
                elapsed_ms=elapsed_ms,
            ),
        )
        return ApiEnvelope(data=None, error=timeout_error)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception(
            "Dashboard route failed",
            extra=build_request_log_extra(
                request,
                event=failure_event,
                route_key=route_key,
            ),
        )
        return ApiEnvelope(data=None, error=str(exc))
