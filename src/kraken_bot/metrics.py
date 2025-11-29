"""Lightweight in-memory counters for operational visibility."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from threading import Lock
from typing import Deque, Dict, List, Optional


class SystemMetrics:
    """Thread-safe, low-overhead counters for bot activity."""

    def __init__(self, max_errors: int = 50) -> None:
        self._lock = Lock()
        self._recent_errors: Deque[Dict[str, str]] = deque(maxlen=max_errors)
        self.plans_generated = 0
        self.plans_executed = 0
        self.blocked_actions = 0
        self.execution_errors = 0

    def record_plan(self, blocked_actions: int = 0) -> None:
        """Increment plan generation metrics."""

        with self._lock:
            self.plans_generated += 1
            self.blocked_actions += max(blocked_actions, 0)

    def record_plan_execution(self, errors: Optional[List[str]] = None) -> None:
        """Track an execution attempt and any associated errors."""

        error_count = len(errors or [])
        with self._lock:
            self.plans_executed += 1
            self.execution_errors += error_count
            for message in errors or []:
                self._recent_errors.appendleft(self._format_error(message))

    def record_error(self, message: str) -> None:
        """Store an ad-hoc error message in the rolling buffer."""

        with self._lock:
            self.execution_errors += 1
            self._recent_errors.appendleft(self._format_error(message))

    def snapshot(self) -> Dict[str, object]:
        """Return a read-only snapshot of current counters."""

        with self._lock:
            return {
                "plans_generated": self.plans_generated,
                "plans_executed": self.plans_executed,
                "blocked_actions": self.blocked_actions,
                "execution_errors": self.execution_errors,
                "recent_errors": list(self._recent_errors),
            }

    @staticmethod
    def _format_error(message: str) -> Dict[str, str]:
        return {
            "at": datetime.now(timezone.utc).isoformat(),
            "message": message,
        }


__all__ = ["SystemMetrics"]
