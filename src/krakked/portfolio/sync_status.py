"""Shared interpretation of portfolio sync health for live safety surfaces."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

DEFAULT_PORTFOLIO_SYNC_INTERVAL_SECONDS = 300
MAX_LIVE_PORTFOLIO_SYNC_INTERVAL_SECONDS = 300
MIN_LIVE_SYNC_MAX_AGE_SECONDS = 120
MAX_LIVE_SYNC_MAX_AGE_SECONDS = 600

LIVE_SYNC_COLD_START_REASON = (
    "Starting up - verifying your live account before allowing orders."
)
LIVE_SYNC_DEGRADED_REASON = (
    "Krakked could not verify live balances with Kraken. Orders will resume "
    "automatically once account sync recovers."
)
LIVE_SYNC_TRADES_UNAVAILABLE_REASON = (
    "Krakked could not verify live trade history with Kraken. Orders will resume "
    "automatically once account sync recovers."
)
LIVE_SYNC_LEDGERS_UNAVAILABLE_REASON = (
    "Krakked could not verify live ledger history with Kraken. Orders will resume "
    "automatically once account sync recovers."
)
LIVE_SYNC_TRADE_HISTORY_LAGGING_REASON = (
    "Kraken trade history has not caught up to new ledger entries yet. Orders "
    "will resume automatically once account sync recovers."
)
LIVE_SYNC_TRADE_HISTORY_LAG_ALERT_TITLE = "Kraken trade history needs review"
_RAW_BALANCE_UNAVAILABLE_PREFIX = "Live balance reconciliation unavailable:"


@dataclass(frozen=True)
class PortfolioSyncStatus:
    ok: bool
    reason: str | None
    last_sync_at: datetime | None
    max_age_seconds: int | None = None
    age_seconds: float | None = None


def coerce_portfolio_sync_interval_seconds(value: Any) -> int:
    if isinstance(value, bool):
        return DEFAULT_PORTFOLIO_SYNC_INTERVAL_SECONDS
    if isinstance(value, (int, float)):
        interval = int(value)
    elif isinstance(value, str):
        stripped = value.strip()
        if not stripped.isdigit():
            return DEFAULT_PORTFOLIO_SYNC_INTERVAL_SECONDS
        interval = int(stripped)
    else:
        return DEFAULT_PORTFOLIO_SYNC_INTERVAL_SECONDS
    if interval <= 0:
        return DEFAULT_PORTFOLIO_SYNC_INTERVAL_SECONDS
    return interval


def effective_portfolio_sync_interval_seconds(
    portfolio_or_config: Any, *, execution_mode: str | None
) -> int:
    """Return the sync interval live safety should use for scheduling and age."""

    source = getattr(portfolio_or_config, "config", portfolio_or_config)
    raw_interval = getattr(
        source,
        "sync_interval_seconds",
        DEFAULT_PORTFOLIO_SYNC_INTERVAL_SECONDS,
    )
    interval = coerce_portfolio_sync_interval_seconds(raw_interval)
    if str(execution_mode or "").lower() == "live":
        return min(interval, MAX_LIVE_PORTFOLIO_SYNC_INTERVAL_SECONDS)
    return interval


def max_live_sync_age_seconds(portfolio_or_config: Any) -> int:
    interval = effective_portfolio_sync_interval_seconds(
        portfolio_or_config, execution_mode="live"
    )
    return min(
        max(2 * interval, MIN_LIVE_SYNC_MAX_AGE_SECONDS),
        MAX_LIVE_SYNC_MAX_AGE_SECONDS,
    )


def live_sync_stale_reason(max_age_seconds: int) -> str:
    return (
        "Krakked last verified live balances more than "
        f"{max_age_seconds} seconds ago. Orders will resume automatically once "
        "account sync refreshes."
    )


def live_sync_trade_history_lag_escalated_reason(max_age_seconds: int) -> str:
    return (
        "Kraken trade history has not matched ledger trade entries for more than "
        f"{max_age_seconds} seconds. Live orders remain blocked. Krakked will "
        "keep retrying automatically; review Kraken trade and ledger history."
    )


def _normalize_datetime(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def read_portfolio_sync_status(
    portfolio: Any, *, execution_mode: str | None, now: datetime | None = None
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
    last_sync_at = _normalize_datetime(raw_last_sync_at)

    if str(execution_mode or "").lower() != "live":
        return PortfolioSyncStatus(
            ok=raw_ok,
            reason=reason,
            last_sync_at=last_sync_at,
        )

    if last_sync_at is None:
        max_age = max_live_sync_age_seconds(portfolio)
        return PortfolioSyncStatus(
            ok=False,
            reason=reason or LIVE_SYNC_COLD_START_REASON,
            last_sync_at=None,
            max_age_seconds=max_age,
        )

    if not raw_ok:
        max_age = max_live_sync_age_seconds(portfolio)
        return PortfolioSyncStatus(
            ok=False,
            reason=reason or LIVE_SYNC_DEGRADED_REASON,
            last_sync_at=last_sync_at,
            max_age_seconds=max_age,
        )

    now_value = _normalize_datetime(now) or datetime.now(UTC)
    max_age = max_live_sync_age_seconds(portfolio)
    age_seconds = max((now_value - last_sync_at).total_seconds(), 0.0)
    if age_seconds > max_age:
        return PortfolioSyncStatus(
            ok=False,
            reason=live_sync_stale_reason(max_age),
            last_sync_at=last_sync_at,
            max_age_seconds=max_age,
            age_seconds=age_seconds,
        )

    return PortfolioSyncStatus(
        ok=True,
        reason=None,
        last_sync_at=last_sync_at,
        max_age_seconds=max_age,
        age_seconds=age_seconds,
    )
