from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from krakked.portfolio.sync_status import (
    DEFAULT_PORTFOLIO_SYNC_INTERVAL_SECONDS,
    LIVE_SYNC_COLD_START_REASON,
    LIVE_SYNC_DEGRADED_REASON,
    MAX_LIVE_PORTFOLIO_SYNC_INTERVAL_SECONDS,
    effective_portfolio_sync_interval_seconds,
    live_sync_stale_reason,
    max_live_sync_age_seconds,
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


def test_live_sync_in_progress_preserves_fresh_previous_success():
    now = datetime(2026, 1, 2, 3, 5, tzinfo=timezone.utc)
    synced_at = now - timedelta(seconds=30)
    portfolio = SimpleNamespace(
        config=SimpleNamespace(sync_interval_seconds=300),
        last_sync_ok=True,
        last_sync_reason=None,
        last_sync_at=synced_at,
        sync_in_progress=True,
    )

    status = read_portfolio_sync_status(portfolio, execution_mode="live", now=now)

    assert status.ok is True
    assert status.reason is None
    assert status.last_sync_at is synced_at
    assert status.in_progress is True
    assert status.age_seconds == 30


def test_live_sync_in_progress_blocks_when_previous_success_is_stale():
    now = datetime(2026, 1, 2, 3, 20, tzinfo=timezone.utc)
    synced_at = now - timedelta(seconds=601)
    portfolio = SimpleNamespace(
        config=SimpleNamespace(sync_interval_seconds=300),
        last_sync_ok=True,
        last_sync_reason=None,
        last_sync_at=synced_at,
        sync_in_progress=True,
    )

    status = read_portfolio_sync_status(portfolio, execution_mode="live", now=now)

    assert status.ok is False
    assert status.reason == live_sync_stale_reason(600)
    assert status.last_sync_at is synced_at
    assert status.in_progress is True


def test_live_sync_in_progress_blocks_when_previous_sync_failed():
    synced_at = datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)
    portfolio = SimpleNamespace(
        last_sync_ok=False,
        last_sync_reason=LIVE_SYNC_DEGRADED_REASON,
        last_sync_at=synced_at,
        sync_in_progress=True,
    )

    status = read_portfolio_sync_status(portfolio, execution_mode="live")

    assert status.ok is False
    assert status.reason == LIVE_SYNC_DEGRADED_REASON
    assert status.last_sync_at is synced_at
    assert status.in_progress is True


def test_non_live_sync_in_progress_preserves_previous_healthy_state():
    synced_at = datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)
    portfolio = SimpleNamespace(
        last_sync_ok=True,
        last_sync_reason=None,
        last_sync_at=synced_at,
        sync_in_progress=True,
    )

    status = read_portfolio_sync_status(portfolio, execution_mode="paper")

    assert status.ok is True
    assert status.reason is None
    assert status.last_sync_at is synced_at
    assert status.in_progress is True


def test_non_live_reasonless_sync_in_progress_is_neutral():
    portfolio = SimpleNamespace(
        last_sync_ok=False,
        last_sync_reason=None,
        last_sync_at=None,
        sync_in_progress=True,
    )

    status = read_portfolio_sync_status(portfolio, execution_mode="paper")

    assert status.ok is True
    assert status.reason is None
    assert status.last_sync_at is None
    assert status.in_progress is True


def test_non_live_sync_in_progress_with_failure_reason_stays_degraded():
    portfolio = SimpleNamespace(
        last_sync_ok=False,
        last_sync_reason="Ledger verification failed",
        last_sync_at=None,
        sync_in_progress=True,
    )

    status = read_portfolio_sync_status(portfolio, execution_mode="paper")

    assert status.ok is False
    assert status.reason == "Ledger verification failed"
    assert status.in_progress is True


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


def test_live_old_sync_is_degraded_with_derived_max_age():
    now = datetime(2026, 1, 2, 12, 0, tzinfo=timezone.utc)
    portfolio = SimpleNamespace(
        config=SimpleNamespace(sync_interval_seconds=300),
        last_sync_ok=True,
        last_sync_reason=None,
        last_sync_at=now - timedelta(seconds=601),
    )

    status = read_portfolio_sync_status(
        portfolio,
        execution_mode="live",
        now=now,
    )

    assert status.ok is False
    assert status.max_age_seconds == 600
    assert status.reason == live_sync_stale_reason(600)


def test_live_configured_interval_above_cap_uses_effective_live_interval():
    config = SimpleNamespace(sync_interval_seconds=3600)

    assert (
        effective_portfolio_sync_interval_seconds(config, execution_mode="live")
        == MAX_LIVE_PORTFOLIO_SYNC_INTERVAL_SECONDS
    )
    assert max_live_sync_age_seconds(config) == 600


def test_non_live_old_sync_remains_compatible():
    now = datetime(2026, 1, 2, 12, 0, tzinfo=timezone.utc)
    portfolio = SimpleNamespace(
        config=SimpleNamespace(sync_interval_seconds=300),
        last_sync_ok=True,
        last_sync_reason=None,
        last_sync_at=now - timedelta(days=1),
    )

    status = read_portfolio_sync_status(
        portfolio,
        execution_mode="paper",
        now=now,
    )

    assert status.ok is True
    assert status.reason is None
    assert status.max_age_seconds is None


def test_bad_interval_uses_default_sync_interval():
    config = SimpleNamespace(sync_interval_seconds="not-a-number")

    assert (
        effective_portfolio_sync_interval_seconds(config, execution_mode="paper")
        == DEFAULT_PORTFOLIO_SYNC_INTERVAL_SECONDS
    )
