from datetime import datetime, timezone
from types import SimpleNamespace

from krakked.portfolio.sync_status import (
    LIVE_SYNC_COLD_START_REASON,
    LIVE_SYNC_DEGRADED_REASON,
    read_portfolio_sync_status,
)


def test_live_cold_start_without_successful_sync_is_degraded():
    portfolio = SimpleNamespace(
        last_sync_ok=True,
        last_sync_reason=None,
        last_sync_at=None,
    )

    status = read_portfolio_sync_status(portfolio, execution_mode="live")

    assert status.ok is False
    assert status.reason == LIVE_SYNC_COLD_START_REASON
    assert status.last_sync_at is None


def test_live_degraded_sync_uses_normalized_reason_and_timestamp():
    synced_at = datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)
    portfolio = SimpleNamespace(
        last_sync_ok=False,
        last_sync_reason=LIVE_SYNC_DEGRADED_REASON,
        last_sync_at=synced_at,
    )

    status = read_portfolio_sync_status(portfolio, execution_mode="live")

    assert status.ok is False
    assert status.reason == LIVE_SYNC_DEGRADED_REASON
    assert status.last_sync_at is synced_at


def test_live_degraded_sync_sanitizes_legacy_raw_exception_reason():
    synced_at = datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)
    portfolio = SimpleNamespace(
        last_sync_ok=False,
        last_sync_reason="Live balance reconciliation unavailable: API Down",
        last_sync_at=synced_at,
    )

    status = read_portfolio_sync_status(portfolio, execution_mode="live")

    assert status.ok is False
    assert status.reason == LIVE_SYNC_DEGRADED_REASON
    assert status.last_sync_at is synced_at


def test_live_degraded_sync_without_reason_uses_fallback():
    synced_at = datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)
    portfolio = SimpleNamespace(
        last_sync_ok=False,
        last_sync_reason=None,
        last_sync_at=synced_at,
    )

    status = read_portfolio_sync_status(portfolio, execution_mode="live")

    assert status.ok is False
    assert status.reason == LIVE_SYNC_DEGRADED_REASON
    assert status.last_sync_at is synced_at


def test_non_live_cold_start_preserves_compatibility():
    portfolio = SimpleNamespace(
        last_sync_ok=True,
        last_sync_reason=None,
        last_sync_at=None,
    )

    status = read_portfolio_sync_status(portfolio, execution_mode="paper")

    assert status.ok is True
    assert status.reason is None
    assert status.last_sync_at is None


def test_mock_like_bad_reason_and_timestamp_normalize_to_none():
    portfolio = SimpleNamespace(
        last_sync_ok=False,
        last_sync_reason=object(),
        last_sync_at=object(),
    )

    status = read_portfolio_sync_status(portfolio, execution_mode="paper")

    assert status.ok is False
    assert status.reason is None
    assert status.last_sync_at is None


def test_whitespace_reason_normalizes_to_none():
    portfolio = SimpleNamespace(
        last_sync_ok=False,
        last_sync_reason="   ",
        last_sync_at=None,
    )

    paper_status = read_portfolio_sync_status(portfolio, execution_mode="paper")
    live_status = read_portfolio_sync_status(portfolio, execution_mode="live")

    assert paper_status.reason is None
    assert live_status.reason == LIVE_SYNC_COLD_START_REASON
