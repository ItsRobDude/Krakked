"""Shared interpretation of portfolio sync health for live safety surfaces."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

LIVE_SYNC_COLD_START_REASON = (
    "Starting up - verifying your live account before allowing orders."
)
LIVE_SYNC_DEGRADED_REASON = (
    "Krakked could not verify live balances with Kraken. Orders will resume "
    "automatically once account sync recovers."
)
_RAW_BALANCE_UNAVAILABLE_PREFIX = "Live balance reconciliation unavailable:"


@dataclass(frozen=True)
class PortfolioSyncStatus:
    ok: bool
    reason: str | None
    last_sync_at: datetime | None


def read_portfolio_sync_status(
    portfolio: Any, *, execution_mode: str | None
) -> PortfolioSyncStatus:
    """Normalize portfolio sync state for operator and live-order safety checks."""

    raw_ok = bool(getattr(portfolio, "last_sync_ok", True))
    raw_reason = getattr(portfolio, "last_sync_reason", None)
    reason = raw_reason.strip() if isinstance(raw_reason, str) else None
    if reason == "":
        reason = None
    elif reason is not None and reason.startswith(_RAW_BALANCE_UNAVAILABLE_PREFIX):
        reason = LIVE_SYNC_DEGRADED_REASON

    raw_last_sync_at = getattr(portfolio, "last_sync_at", None)
    last_sync_at = raw_last_sync_at if isinstance(raw_last_sync_at, datetime) else None

    if str(execution_mode or "").lower() != "live":
        return PortfolioSyncStatus(
            ok=raw_ok,
            reason=reason,
            last_sync_at=last_sync_at,
        )

    if last_sync_at is None:
        return PortfolioSyncStatus(
            ok=False,
            reason=reason or LIVE_SYNC_COLD_START_REASON,
            last_sync_at=None,
        )

    if not raw_ok:
        return PortfolioSyncStatus(
            ok=False,
            reason=reason or LIVE_SYNC_DEGRADED_REASON,
            last_sync_at=last_sync_at,
        )

    return PortfolioSyncStatus(ok=True, reason=None, last_sync_at=last_sync_at)
