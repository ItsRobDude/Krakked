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
        self.market_data_errors = 0
        self.last_equity_usd: Optional[float] = None
        self.last_realized_pnl_usd: Optional[float] = None
        self.last_unrealized_pnl_usd: Optional[float] = None
        self.open_orders_count = 0
        self.open_positions_count = 0
        self.drift_detected = False
        self.drift_reason: Optional[str] = None
        self.market_data_ok = False
        self.market_data_stale = False
        self.market_data_reason: Optional[str] = None
        self.market_data_max_staleness: Optional[float] = None

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

    def record_blocked_actions(self, blocked_actions: int) -> None:
        """Increment blocked action count without affecting plan counters."""

        if blocked_actions <= 0:
            return

        with self._lock:
            self.blocked_actions += blocked_actions

    def record_error(self, message: str) -> None:
        """Store an ad-hoc error message in the rolling buffer."""

        with self._lock:
            self.execution_errors += 1
            self._recent_errors.appendleft(self._format_error(message))

    def record_market_data_error(self, message: str) -> None:
        """Track market-data-related issues without affecting execution counters."""

        with self._lock:
            self.market_data_errors += 1
            self._recent_errors.appendleft(self._format_error(message))

    def update_portfolio_state(
        self,
        *,
        equity_usd: Optional[float],
        realized_pnl_usd: Optional[float],
        unrealized_pnl_usd: Optional[float],
        open_orders_count: int,
        open_positions_count: int,
    ) -> None:
        """Update portfolio-related metrics atomically."""

        with self._lock:
            self.last_equity_usd = equity_usd
            self.last_realized_pnl_usd = realized_pnl_usd
            self.last_unrealized_pnl_usd = unrealized_pnl_usd
            self.open_orders_count = open_orders_count
            self.open_positions_count = open_positions_count

    def record_drift(self, drift_flag: bool, message: Optional[str] = None) -> None:
        """Track the latest portfolio drift state."""

        with self._lock:
            self.drift_detected = drift_flag
            self.drift_reason = message if drift_flag else None
            if drift_flag and message:
                self._recent_errors.appendleft(self._format_error(message))

    def update_market_data_status(
        self,
        *,
        ok: bool,
        stale: bool,
        reason: Optional[str],
        max_staleness: Optional[float],
    ) -> None:
        """Capture the latest market data health state."""

        with self._lock:
            self.market_data_ok = ok
            self.market_data_stale = stale
            self.market_data_reason = reason
            self.market_data_max_staleness = max_staleness

    def snapshot(self) -> Dict[str, object]:
        """Return a read-only snapshot of current counters."""

        with self._lock:
            return {
                "plans_generated": self.plans_generated,
                "plans_executed": self.plans_executed,
                "blocked_actions": self.blocked_actions,
                "execution_errors": self.execution_errors,
                "market_data_errors": self.market_data_errors,
                "recent_errors": list(self._recent_errors),
                "last_equity_usd": self.last_equity_usd,
                "last_realized_pnl_usd": self.last_realized_pnl_usd,
                "last_unrealized_pnl_usd": self.last_unrealized_pnl_usd,
                "open_orders_count": self.open_orders_count,
                "open_positions_count": self.open_positions_count,
                "drift_detected": self.drift_detected,
                "drift_reason": self.drift_reason,
                "market_data_ok": self.market_data_ok,
                "market_data_stale": self.market_data_stale,
                "market_data_reason": self.market_data_reason,
                "market_data_max_staleness": self.market_data_max_staleness,
            }

    @staticmethod
    def _format_error(message: str) -> Dict[str, str]:
        return {
            "at": datetime.now(timezone.utc).isoformat(),
            "message": message,
        }


__all__ = ["SystemMetrics"]
