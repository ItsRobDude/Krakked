import logging
from datetime import datetime, timedelta, timezone
from threading import Event, Lock, RLock, Thread
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from krakked.config import PortfolioConfig
from krakked.market_data.exceptions import PairNotFoundError
from krakked.portfolio import manager as manager_module
from krakked.portfolio.manager import PortfolioService, _TradeHistoryLagStatus
from krakked.portfolio.portfolio import Portfolio
from krakked.portfolio.sync_status import (
    LIVE_ACCOUNT_TRUTH_REFRESH_TIMEOUT_REASON,
    LIVE_SYNC_DEGRADED_REASON,
    LIVE_SYNC_LEDGERS_UNAVAILABLE_REASON,
    LIVE_SYNC_STUCK_REASON,
    LIVE_SYNC_TRADE_HISTORY_LAGGING_REASON,
    LIVE_SYNC_TRADES_UNAVAILABLE_REASON,
    PORTFOLIO_SYNC_FAILED_REASON,
    max_live_sync_age_seconds,
    read_portfolio_sync_status,
)


def _build_service(store, portfolio, api_client):
    store.get_unmatched_trade_ledger_ref_times.return_value = {}
    store.get_trade_ids_by_ids.side_effect = lambda trade_ids: set()
    service = PortfolioService.__new__(PortfolioService)
    service.config = PortfolioConfig()
    service.app_config = SimpleNamespace(execution=SimpleNamespace(mode="live"))
    service._clock = None
    service.store = store
    service.portfolio = portfolio
    service.rest_client = api_client
    service._bootstrapped = True
    service._account_truth_lock = RLock()
    service._sync_run_lock = Lock()
    service._sync_run_kind = None
    service._last_sync_ok = True
    service._last_sync_reason = None
    service._last_sync_at = None
    service._last_balance_reconcile_at = None
    service._last_sync_result = {"new_trades": 0, "new_cash_flows": 0}
    service._sync_generation = 0
    service._last_sync_result_generation = 0
    service._sync_in_progress = False
    service._sync_started_at = None
    service._cached_equity = None
    service._cached_positions = []
    service._cached_asset_exposure = []
    service._cached_drift_status = None
    service._cached_last_snapshot_ts = None
    service._exchange_reference_balances = {}
    service._exchange_reference_checked_at = None
    service._exchange_reference_equity = None
    service._trade_history_lag_alerted_refs = set()
    service.alert_notifier = None
    service._refresh_cached_views = Mock()

    def _reconcile():
        service._last_balance_reconcile_at = service._now()
        return True

    service._reconcile = Mock(side_effect=_reconcile)
    return service


class _SequenceAlerts:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.events = []

    def send(self, **kwargs):
        self.events.append(kwargs)
        outcome = self.outcomes.pop(0) if self.outcomes else True
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_sync_in_progress_preserves_last_completed_state_during_attempt():
    store = Mock()
    portfolio = Mock()
    api_client = Mock()
    service = _build_service(store, portfolio, api_client)
    previous_sync_at = datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)
    service._last_sync_ok = True
    service._last_sync_reason = None
    service._last_sync_at = previous_sync_at

    store.get_trades.return_value = []
    service._sync_ledgers = Mock(
        return_value=SimpleNamespace(
            cash_flow_count=0, trade_refids=set(), failed=False
        )
    )

    def _trade_probe(_since_ts):
        assert service.sync_in_progress is True
        assert service.last_sync_ok is True
        assert service.last_sync_reason is None
        assert service.last_sync_at is previous_sync_at
        return SimpleNamespace(count=0, trade_ids=set(), failed=False)

    service._sync_trades_history = Mock(side_effect=_trade_probe)

    result = service.sync()

    assert result == {"new_trades": 0, "new_cash_flows": 0}
    assert service.sync_in_progress is False
    assert service.last_sync_ok is True
    assert service.last_sync_reason is None
    assert service.last_sync_at is not previous_sync_at


def test_account_truth_snapshot_uses_injected_clock():
    store = Mock()
    portfolio = Mock()
    api_client = Mock()
    service = _build_service(store, portfolio, api_client)
    now = datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)
    service._clock = lambda: now
    service._last_sync_ok = True
    service._last_sync_reason = None
    service._last_sync_at = now - timedelta(seconds=30)
    service.get_drift_status = Mock(return_value=SimpleNamespace(drift_flag=False))

    snapshot = service.get_account_truth_snapshot()

    assert snapshot.generated_at == now
    assert snapshot.age_seconds == 30


def test_account_truth_snapshot_uses_locked_cached_drift_without_live_probe():
    store = Mock()
    portfolio = Mock()
    api_client = Mock()
    service = _build_service(store, portfolio, api_client)
    now = datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)
    service._clock = lambda: now
    service._last_sync_ok = True
    service._last_sync_reason = None
    service._last_sync_at = now - timedelta(seconds=30)
    service._cached_drift_status = SimpleNamespace(drift_flag=True)
    service.get_drift_status = Mock(
        side_effect=AssertionError("unexpected live drift read")
    )

    snapshot = service.get_account_truth_snapshot()

    assert snapshot.drift_flag is True
    service.get_drift_status.assert_not_called()


def test_account_truth_snapshot_reports_unknown_when_cached_drift_is_cold():
    store = Mock()
    portfolio = Mock()
    api_client = Mock()
    service = _build_service(store, portfolio, api_client)
    now = datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)
    service._clock = lambda: now
    service._last_sync_ok = True
    service._last_sync_reason = None
    service._last_sync_at = now - timedelta(seconds=30)
    service._cached_drift_status = None
    service.get_drift_status = Mock(
        side_effect=AssertionError("unexpected live drift read")
    )

    snapshot = service.get_account_truth_snapshot()

    assert snapshot.drift_flag is False
    assert snapshot.drift_info == {
        "status": "unknown",
        "source": "cached",
        "reason": "cached_drift_status_unavailable",
    }
    service.get_drift_status.assert_not_called()


def test_account_truth_force_fresh_reuses_recent_balance_reconcile():
    store = Mock()
    portfolio = Mock()
    api_client = Mock()
    service = _build_service(store, portfolio, api_client)
    service._sync_run_lock = Lock()
    now = datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)
    service._clock = lambda: now
    service._last_sync_ok = True
    service._last_sync_at = now - timedelta(seconds=30)
    service._last_balance_reconcile_at = now - timedelta(seconds=1)

    def _drift_probe():
        acquired = service._sync_run_lock.acquire(blocking=False)
        if acquired:
            service._sync_run_lock.release()
        assert acquired is False
        return SimpleNamespace(drift_flag=False)

    service.get_drift_status = Mock(side_effect=_drift_probe)

    snapshot = service.get_account_truth_snapshot(force_fresh_drift=True)

    assert snapshot.portfolio_sync_ok is True
    service._reconcile.assert_not_called()
    service._refresh_cached_views.assert_not_called()


def test_account_truth_force_fresh_reconciles_once_when_budget_expired():
    store = Mock()
    portfolio = Mock()
    api_client = Mock()
    service = _build_service(store, portfolio, api_client)
    service._sync_run_lock = Lock()
    now = datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)
    service._clock = lambda: now
    service._last_sync_ok = True
    service._last_sync_at = now - timedelta(seconds=30)
    service._last_balance_reconcile_at = now - timedelta(seconds=10)
    service.get_drift_status = Mock(return_value=SimpleNamespace(drift_flag=False))

    snapshot = service.get_account_truth_snapshot(force_fresh_drift=True)

    assert snapshot.portfolio_sync_ok is True
    service._reconcile.assert_called_once_with()
    service._refresh_cached_views.assert_called_once_with()


def test_account_truth_force_fresh_reads_drift_under_sync_lock():
    store = Mock()
    portfolio = Mock()
    api_client = Mock()
    service = _build_service(store, portfolio, api_client)
    service._sync_run_lock = Lock()
    now = datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)
    service._clock = lambda: now
    service._last_sync_ok = True
    service._last_sync_at = now - timedelta(seconds=30)
    service._last_balance_reconcile_at = now - timedelta(seconds=10)

    def _drift_probe():
        acquired = service._sync_run_lock.acquire(blocking=False)
        if acquired:
            service._sync_run_lock.release()
        assert acquired is False
        return SimpleNamespace(drift_flag=True)

    service.get_drift_status = Mock(side_effect=_drift_probe)

    snapshot = service.get_account_truth_snapshot(force_fresh_drift=True)

    assert snapshot.drift_flag is True
    service._reconcile.assert_called_once_with()


def test_account_truth_force_fresh_times_out_on_sync_lock(monkeypatch):
    store = Mock()
    portfolio = Mock()
    api_client = Mock()
    service = _build_service(store, portfolio, api_client)
    service._sync_run_lock = Lock()
    service._sync_run_lock.acquire()
    monkeypatch.setattr(manager_module, "LIVE_ACCOUNT_TRUTH_LOCK_TIMEOUT_SECONDS", 0.01)
    now = datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)
    service._clock = lambda: now
    service._last_sync_ok = True
    service._last_sync_at = now - timedelta(seconds=30)
    service.get_drift_status = Mock(return_value=SimpleNamespace(drift_flag=False))

    try:
        snapshot = service.get_account_truth_snapshot(force_fresh_drift=True)
    finally:
        service._sync_run_lock.release()

    assert snapshot.portfolio_sync_ok is False
    assert service.last_sync_ok is False
    assert service._reconcile.call_count == 0


def test_account_truth_force_fresh_timeout_reports_stuck_sync_after_deadline(
    monkeypatch,
):
    store = Mock()
    portfolio = Mock()
    api_client = Mock()
    service = _build_service(store, portfolio, api_client)
    service._sync_run_lock = Lock()
    service._sync_run_lock.acquire()
    monkeypatch.setattr(manager_module, "LIVE_ACCOUNT_TRUTH_LOCK_TIMEOUT_SECONDS", 0.01)
    now = datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)
    service._clock = lambda: now
    service._last_sync_ok = True
    service._last_sync_at = now - timedelta(seconds=30)
    service._sync_started_at = now - timedelta(
        seconds=manager_module.LIVE_FULL_SYNC_DEADLINE_SECONDS + 1
    )
    service._sync_run_kind = manager_module._SYNC_RUN_KIND_FULL
    service.get_drift_status = Mock(return_value=SimpleNamespace(drift_flag=False))

    try:
        snapshot = service.get_account_truth_snapshot(force_fresh_drift=True)
    finally:
        service._sync_run_lock.release()

    assert snapshot.portfolio_sync_ok is False
    assert snapshot.portfolio_sync_reason == LIVE_SYNC_STUCK_REASON
    assert service.last_sync_ok is False
    assert service.last_sync_reason == LIVE_SYNC_STUCK_REASON
    assert service._reconcile.call_count == 0


def test_account_truth_timeout_recovers_after_successful_forced_reconcile(
    monkeypatch,
):
    store = Mock()
    portfolio = Mock()
    api_client = Mock()
    service = _build_service(store, portfolio, api_client)
    service._sync_run_lock = Lock()
    service._sync_run_lock.acquire()
    monkeypatch.setattr(manager_module, "LIVE_ACCOUNT_TRUTH_LOCK_TIMEOUT_SECONDS", 0.01)
    now = datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)
    service._clock = lambda: now
    service._last_sync_ok = True
    service._last_sync_at = now - timedelta(seconds=30)
    service.get_drift_status = Mock(return_value=SimpleNamespace(drift_flag=False))

    try:
        timed_out = service.get_account_truth_snapshot(force_fresh_drift=True)
    finally:
        service._sync_run_lock.release()

    recovered = service.get_account_truth_snapshot(force_fresh_drift=True)

    assert timed_out.portfolio_sync_ok is False
    assert service._reconcile.call_count == 1
    assert recovered.portfolio_sync_ok is True
    assert service.last_sync_ok is True
    assert service.last_sync_reason is None


def test_account_truth_failed_forced_reconcile_keeps_account_truth_degraded():
    store = Mock()
    portfolio = Mock()
    api_client = Mock()
    service = _build_service(store, portfolio, api_client)
    service._sync_run_lock = Lock()
    now = datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)
    service._clock = lambda: now
    service._last_sync_ok = False
    service._last_sync_reason = LIVE_ACCOUNT_TRUTH_REFRESH_TIMEOUT_REASON
    service._last_sync_at = now - timedelta(seconds=30)
    service._last_balance_reconcile_at = now - timedelta(seconds=10)
    service.get_drift_status = Mock(return_value=SimpleNamespace(drift_flag=False))

    def _fail_reconcile():
        service._set_last_sync_state(ok=False, reason=LIVE_SYNC_DEGRADED_REASON)
        return False

    service._reconcile = Mock(side_effect=_fail_reconcile)

    snapshot = service.get_account_truth_snapshot(force_fresh_drift=True)

    assert snapshot.portfolio_sync_ok is False
    assert snapshot.portfolio_sync_reason == LIVE_SYNC_DEGRADED_REASON
    assert service.last_sync_ok is False
    assert service.last_sync_reason == LIVE_SYNC_DEGRADED_REASON


@pytest.mark.parametrize(
    "reason,last_sync_age_seconds",
    [
        (LIVE_ACCOUNT_TRUTH_REFRESH_TIMEOUT_REASON, 601),
        (LIVE_SYNC_TRADE_HISTORY_LAGGING_REASON, 30),
        (LIVE_SYNC_TRADES_UNAVAILABLE_REASON, 30),
        (LIVE_SYNC_LEDGERS_UNAVAILABLE_REASON, 30),
        (PORTFOLIO_SYNC_FAILED_REASON, 30),
        (LIVE_SYNC_DEGRADED_REASON, 30),
        (LIVE_SYNC_STUCK_REASON, 30),
    ],
)
def test_successful_forced_reconcile_does_not_clear_non_timeout_or_stale_blockers(
    reason,
    last_sync_age_seconds,
):
    store = Mock()
    portfolio = Mock()
    api_client = Mock()
    service = _build_service(store, portfolio, api_client)
    service._sync_run_lock = Lock()
    now = datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)
    service._clock = lambda: now
    service._last_sync_ok = False
    service._last_sync_reason = reason
    service._last_sync_at = now - timedelta(seconds=last_sync_age_seconds)
    service._last_balance_reconcile_at = now - timedelta(seconds=10)
    service.get_drift_status = Mock(return_value=SimpleNamespace(drift_flag=False))

    snapshot = service.get_account_truth_snapshot(force_fresh_drift=True)

    assert snapshot.portfolio_sync_ok is False
    assert service.last_sync_ok is False
    assert service.last_sync_reason == reason


def test_sync_singleflight_coalesces_concurrent_attempts():
    store = Mock()
    portfolio = Mock()
    api_client = Mock()
    service = _build_service(store, portfolio, api_client)
    service._sync_run_lock = Lock()
    store.get_trades.return_value = []
    service._sync_ledgers = Mock(
        return_value=SimpleNamespace(
            cash_flow_count=0, trade_refids=set(), failed=False
        )
    )
    first_started = Event()
    release_first = Event()
    second_started = Event()
    calls = []

    def _sync_trades(_since_ts):
        calls.append(len(calls) + 1)
        if len(calls) == 1:
            first_started.set()
            assert release_first.wait(2)
        else:
            second_started.set()
        return SimpleNamespace(count=0, trade_ids=set(), failed=False)

    service._sync_trades_history = Mock(side_effect=_sync_trades)
    results = []

    def _run_sync():
        results.append(service.sync())

    first_thread = Thread(target=_run_sync)
    second_thread = Thread(target=_run_sync)
    first_thread.start()
    assert first_started.wait(2)
    second_thread.start()
    assert not second_started.wait(0.1)
    release_first.set()
    first_thread.join(2)
    second_thread.join(2)

    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert calls == [1]
    assert results == [
        {"new_trades": 0, "new_cash_flows": 0},
        {"new_trades": 0, "new_cash_flows": 0},
    ]


def test_sync_waiting_behind_fresh_account_truth_runs_full_sync():
    store = Mock()
    portfolio = Mock()
    api_client = Mock()
    service = _build_service(store, portfolio, api_client)
    now = datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)
    service._clock = lambda: now
    service._last_sync_ok = True
    service._last_sync_reason = None
    service._last_sync_at = now - timedelta(seconds=30)
    service._last_balance_reconcile_at = now - timedelta(seconds=10)
    service._last_sync_result = {"new_trades": 99, "new_cash_flows": 88}
    service._last_sync_result_generation = 0
    store.get_trades.return_value = []
    service._sync_trades_history = Mock(
        return_value=SimpleNamespace(count=3, trade_ids=set(), failed=False)
    )
    service._sync_ledgers = Mock(
        return_value=SimpleNamespace(
            cash_flow_count=2, trade_refids=set(), failed=False
        )
    )
    service.get_drift_status = Mock(return_value=SimpleNamespace(drift_flag=False))
    first_reconcile_started = Event()
    release_first_reconcile = Event()
    reconcile_calls = 0

    def _reconcile():
        nonlocal reconcile_calls
        reconcile_calls += 1
        if reconcile_calls == 1:
            first_reconcile_started.set()
            assert release_first_reconcile.wait(2)
        service._last_balance_reconcile_at = service._now()
        return True

    service._reconcile = Mock(side_effect=_reconcile)
    truth_results = []
    sync_results = []

    truth_thread = Thread(
        target=lambda: truth_results.append(
            service.get_account_truth_snapshot(force_fresh_drift=True)
        )
    )
    truth_thread.start()
    assert first_reconcile_started.wait(2)

    sync_thread = Thread(target=lambda: sync_results.append(service.sync()))
    sync_thread.start()
    assert service._sync_trades_history.call_count == 0

    release_first_reconcile.set()
    truth_thread.join(2)
    sync_thread.join(2)

    assert not truth_thread.is_alive()
    assert not sync_thread.is_alive()
    assert len(truth_results) == 1
    assert sync_results == [{"new_trades": 3, "new_cash_flows": 2}]
    assert service._sync_trades_history.call_count == 1
    assert service._sync_ledgers.call_count == 1
    assert service._reconcile.call_count == 2


def test_sync_coalesced_waiter_does_not_return_stale_counts_after_failed_full_sync():
    store = Mock()
    portfolio = Mock()
    api_client = Mock()
    service = _build_service(store, portfolio, api_client)
    service._last_sync_result = {"new_trades": 9, "new_cash_flows": 8}
    service._last_sync_result_generation = 0
    store.get_trades.return_value = []
    first_started = Event()
    release_failure = Event()

    def _sync_trades(_since_ts):
        first_started.set()
        assert release_failure.wait(2)
        raise RuntimeError("boom")

    service._sync_trades_history = Mock(side_effect=_sync_trades)
    first_errors = []
    second_results = []

    def _run_first():
        try:
            service.sync()
        except RuntimeError as exc:
            first_errors.append(str(exc))

    first_thread = Thread(target=_run_first)
    second_thread = Thread(target=lambda: second_results.append(service.sync()))
    first_thread.start()
    assert first_started.wait(2)
    second_thread.start()
    release_failure.set()
    first_thread.join(2)
    second_thread.join(2)

    assert first_errors == ["boom"]
    assert second_results == [{"new_trades": 0, "new_cash_flows": 0}]


def test_sync_lock_timeout_marks_stuck_sync_when_full_sync_exceeds_deadline(
    monkeypatch,
):
    store = Mock()
    portfolio = Mock()
    api_client = Mock()
    service = _build_service(store, portfolio, api_client)
    service._sync_run_lock = Lock()
    service._sync_run_lock.acquire()
    now = datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)
    service._clock = lambda: now
    service._sync_started_at = now - timedelta(
        seconds=manager_module.LIVE_FULL_SYNC_DEADLINE_SECONDS + 1
    )
    service._sync_run_kind = manager_module._SYNC_RUN_KIND_FULL
    service._sync_trades_history = Mock()
    monkeypatch.setattr(manager_module, "LIVE_ACCOUNT_TRUTH_LOCK_TIMEOUT_SECONDS", 0.01)

    try:
        result = service.sync()
    finally:
        service._sync_run_lock.release()

    assert result == {"new_trades": 0, "new_cash_flows": 0}
    assert service.last_sync_ok is False
    assert service.last_sync_reason == LIVE_SYNC_STUCK_REASON
    service._sync_trades_history.assert_not_called()


def test_sync_lock_timeout_does_not_mark_within_deadline_full_sync_stuck(
    monkeypatch,
):
    store = Mock()
    portfolio = Mock()
    api_client = Mock()
    service = _build_service(store, portfolio, api_client)
    service._sync_run_lock = Lock()
    service._sync_run_lock.acquire()
    now = datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)
    service._clock = lambda: now
    service._last_sync_ok = True
    service._last_sync_reason = None
    service._last_sync_at = now - timedelta(seconds=30)
    service._sync_started_at = now - timedelta(
        seconds=max_live_sync_age_seconds(service.config) + 1
    )
    service._sync_run_kind = manager_module._SYNC_RUN_KIND_FULL
    service._sync_trades_history = Mock()
    monkeypatch.setattr(manager_module, "LIVE_ACCOUNT_TRUTH_LOCK_TIMEOUT_SECONDS", 0.01)

    try:
        result = service.sync()
    finally:
        service._sync_run_lock.release()

    assert result == {"new_trades": 0, "new_cash_flows": 0}
    assert service.last_sync_ok is True
    assert service.last_sync_reason is None
    service._sync_trades_history.assert_not_called()


def test_sync_lock_timeout_marks_real_full_sync_stuck_after_deadline(monkeypatch):
    store = Mock()
    portfolio = Mock()
    api_client = Mock()
    service = _build_service(store, portfolio, api_client)
    initial_now = datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)
    clock_value = {"now": initial_now}
    service._clock = lambda: clock_value["now"]
    service._last_sync_ok = True
    service._last_sync_reason = None
    service._last_sync_at = initial_now - timedelta(seconds=30)
    store.get_trades.return_value = []
    first_started = Event()
    release_first = Event()

    def _sync_trades(_since_ts):
        first_started.set()
        assert release_first.wait(2)
        return SimpleNamespace(count=0, trade_ids=set(), failed=False)

    service._sync_trades_history = Mock(side_effect=_sync_trades)
    service._sync_ledgers = Mock(
        return_value=SimpleNamespace(
            cash_flow_count=0, trade_refids=set(), failed=False
        )
    )
    monkeypatch.setattr(manager_module, "LIVE_ACCOUNT_TRUTH_LOCK_TIMEOUT_SECONDS", 0.01)

    first_result = []
    second_result = []
    first_thread = Thread(target=lambda: first_result.append(service.sync()))
    first_thread.start()
    assert first_started.wait(2)
    clock_value["now"] = initial_now + timedelta(
        seconds=manager_module.LIVE_FULL_SYNC_DEADLINE_SECONDS + 1
    )

    second_thread = Thread(target=lambda: second_result.append(service.sync()))
    second_thread.start()
    second_thread.join(2)

    release_first.set()
    first_thread.join(2)

    assert not second_thread.is_alive()
    assert not first_thread.is_alive()
    assert second_result == [{"new_trades": 0, "new_cash_flows": 0}]
    assert service.last_sync_ok is False
    assert service.last_sync_reason == LIVE_SYNC_STUCK_REASON
    assert service._sync_trades_history.call_count == 1


def test_slow_trades_history_pagination_past_live_max_age_does_not_mark_stuck():
    store = Mock()
    portfolio = Mock()
    api_client = Mock()
    service = _build_service(store, portfolio, api_client)
    service.config = PortfolioConfig(sync_interval_seconds=60)
    start = datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)
    clock_value = {"now": start}
    service._clock = lambda: clock_value["now"]
    service._sync_started_at = start
    service._sync_run_kind = manager_module._SYNC_RUN_KIND_FULL
    store.get_order_by_reference.return_value = None
    portfolio._normalize_trade_payload.side_effect = lambda trade: trade
    max_age = max_live_sync_age_seconds(service.config)
    page_calls = {"count": 0}

    def _trades_history(_endpoint, params=None):
        page_calls["count"] += 1
        clock_value["now"] = start + timedelta(seconds=max_age + page_calls["count"])
        if page_calls["count"] > 3:
            return {"trades": {}, "last": None}
        trade_id = f"T-SLOW-{page_calls['count']}"
        return {
            "trades": {
                trade_id: {
                    "time": float(page_calls["count"]),
                    "pair": "XBTUSD",
                    "type": "buy",
                    "price": "100",
                    "cost": "100",
                    "fee": "0",
                    "vol": "1",
                }
            },
            "last": float(page_calls["count"]),
        }

    api_client.get_private.side_effect = _trades_history

    result = service._sync_trades_history(None)

    assert result.failed is False
    assert result.count == 3
    assert service.last_sync_ok is True
    assert service.last_sync_reason is None
    assert page_calls["count"] == 4


def test_successful_full_sync_longer_than_live_max_age_is_fresh_at_completion():
    store = Mock()
    portfolio = Mock()
    api_client = Mock()
    service = _build_service(store, portfolio, api_client)
    service.config = PortfolioConfig(sync_interval_seconds=60)
    start = datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)
    clock_value = {"now": start}
    service._clock = lambda: clock_value["now"]
    store.get_trades.return_value = []
    service._sync_ledgers = Mock(
        return_value=SimpleNamespace(
            cash_flow_count=0,
            trade_refids=set(),
            failed=False,
        )
    )
    service.get_drift_status = Mock(return_value=SimpleNamespace(drift_flag=False))
    max_age = max_live_sync_age_seconds(service.config)

    def _slow_trades(_since_ts):
        clock_value["now"] = start + timedelta(seconds=max_age + 1)
        return SimpleNamespace(count=0, trade_ids=set(), failed=False)

    service._sync_trades_history = Mock(side_effect=_slow_trades)

    result = service.sync()
    status = read_portfolio_sync_status(
        service,
        execution_mode="live",
        now=clock_value["now"],
    )

    assert result == {"new_trades": 0, "new_cash_flows": 0}
    assert service.last_sync_at == clock_value["now"]
    assert status.ok is True
    assert status.age_seconds == 0


def test_sync_unmatched_trade_refs_ignore_last_sync_at_cutoff():
    store = Mock()
    portfolio = Mock()
    api_client = Mock()
    service = _build_service(store, portfolio, api_client)
    previous_sync_at = datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)
    now = previous_sync_at + timedelta(seconds=2)
    service._clock = lambda: now
    service._last_sync_ok = True
    service._last_sync_reason = None
    service._last_sync_at = previous_sync_at

    store.get_trades.return_value = []
    service._sync_trades_history = Mock(
        return_value=SimpleNamespace(count=0, trade_ids=set(), failed=False)
    )
    service._sync_ledgers = Mock(
        return_value=SimpleNamespace(
            cash_flow_count=0, trade_refids={"T-LATE"}, failed=False
        )
    )
    store.get_unmatched_trade_ledger_ref_times.return_value = {
        "T-LATE": (previous_sync_at - timedelta(seconds=1)).timestamp()
    }

    result = service.sync()

    assert result == {"new_trades": 0, "new_cash_flows": 0}
    assert service.last_sync_ok is False
    assert service.last_sync_reason == LIVE_SYNC_TRADE_HISTORY_LAGGING_REASON
    assert service.last_sync_at is previous_sync_at
    store.get_unmatched_trade_ledger_ref_times.assert_called_once_with(
        include_refids={"T-LATE"}
    )
    service._reconcile.assert_not_called()


def test_missing_trade_history_refs_use_injected_clock_for_escalation():
    store = Mock()
    portfolio = Mock()
    api_client = Mock()
    service = _build_service(store, portfolio, api_client)
    now = datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)
    service._clock = lambda: now
    max_age = max_live_sync_age_seconds(service.config)
    store.get_unmatched_trade_ledger_ref_times.return_value = {
        "T-FRESH": now.timestamp() - max_age,
        "T-OLD": now.timestamp() - max_age - 1,
        "T-FETCHED": now.timestamp() - max_age - 1,
    }

    status = service._missing_trade_history_refs({"T-FETCHED"}, {"T-FRESH"})

    assert status.missing_refids == {"T-FRESH", "T-OLD"}
    assert status.escalated_refids == {"T-OLD"}
    store.get_unmatched_trade_ledger_ref_times.assert_called_once_with(
        include_refids={"T-FRESH"}
    )


def test_trade_history_lag_alert_retries_until_delivery_succeeds():
    store = Mock()
    portfolio = Mock()
    api_client = Mock()
    service = _build_service(store, portfolio, api_client)
    alerts = _SequenceAlerts([False, True])
    service.alert_notifier = alerts
    status = _TradeHistoryLagStatus(
        ref_times={"T-1": 1.0},
        escalated_refids={"T-1"},
        max_age_seconds=600,
    )

    service._send_trade_history_lag_alert(status)

    assert alerts.events
    assert service._trade_history_lag_alerted_refs == set()

    service._send_trade_history_lag_alert(status)

    assert len(alerts.events) == 2
    assert service._trade_history_lag_alerted_refs == {"T-1"}

    service._send_trade_history_lag_alert(status)

    assert len(alerts.events) == 2


def test_trade_history_lag_alert_exception_remains_retryable():
    store = Mock()
    portfolio = Mock()
    api_client = Mock()
    service = _build_service(store, portfolio, api_client)
    alerts = _SequenceAlerts([RuntimeError("webhook down"), True])
    service.alert_notifier = alerts
    status = _TradeHistoryLagStatus(
        ref_times={"T-1": 1.0},
        escalated_refids={"T-1"},
        max_age_seconds=600,
    )

    service._send_trade_history_lag_alert(status)

    assert service._trade_history_lag_alerted_refs == set()

    service._send_trade_history_lag_alert(status)

    assert len(alerts.events) == 2
    assert service._trade_history_lag_alerted_refs == {"T-1"}


def test_sync_outer_exception_stores_sanitized_reason_and_logs_raw_detail(caplog):
    store = Mock()
    portfolio = Mock()
    api_client = Mock()
    service = _build_service(store, portfolio, api_client)
    previous_sync_at = datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)
    service._last_sync_ok = True
    service._last_sync_reason = None
    service._last_sync_at = previous_sync_at

    store.get_trades.return_value = []
    service._sync_trades_history = Mock(side_effect=RuntimeError("raw Kraken detail"))

    with caplog.at_level(logging.ERROR):
        with pytest.raises(RuntimeError):
            service.sync()

    assert service.sync_in_progress is False
    assert service.last_sync_ok is False
    assert service.last_sync_reason == PORTFOLIO_SYNC_FAILED_REASON
    assert "raw Kraken detail" not in service.last_sync_reason
    assert service.last_sync_at is previous_sync_at
    assert "raw Kraken detail" in caplog.text


def test_sync_ingests_before_saving():
    store = Mock()
    portfolio = Mock()
    api_client = Mock()

    store.get_trades.return_value = []
    # Mock get_latest_ledger_entry for start time
    store.get_latest_ledger_entry.return_value = None
    store.get_cash_flows.return_value = []
    portfolio._normalize_trade_payload.side_effect = lambda t: t
    # Ensure portfolio has a balances dict for BalanceEngine
    portfolio.balances = {}
    # Ensure portfolio has _normalize_asset for LedgerEntry creation
    portfolio._normalize_asset.side_effect = lambda a: a

    api_client.get_private.side_effect = [
        {
            "trades": {
                "T1": {
                    "time": 1,
                    "pair": "BTC/USD",
                    "type": "buy",
                    "price": 10,
                    "cost": 10,
                    "fee": 0,
                    "vol": 1,
                }
            },
            "last": None,
        },
        {"trades": {}},
    ]
    api_client.get_ledgers.return_value = {"ledger": {}}

    service = _build_service(store, portfolio, api_client)

    result = service.sync()

    portfolio.ingest_trades.assert_called_once()
    store.save_trades.assert_called_once()
    assert result["new_trades"] == 1
    assert service.last_sync_ok is True


def test_sync_does_not_save_when_ingest_fails():
    store = Mock()
    portfolio = Mock()
    api_client = Mock()

    store.get_trades.return_value = []
    store.get_latest_ledger_entry.return_value = None
    store.get_cash_flows.return_value = []
    portfolio._normalize_trade_payload.side_effect = lambda t: t
    portfolio.ingest_trades.side_effect = RuntimeError("boom")
    portfolio.balances = {}
    portfolio._normalize_asset.side_effect = lambda a: a

    api_client.get_private.return_value = {
        "trades": {
            "T1": {
                "time": 1,
                "pair": "BTC/USD",
                "type": "buy",
                "price": 10,
                "cost": 10,
                "fee": 0,
                "vol": 1,
            }
        },
        "last": None,
    }
    api_client.get_ledgers.return_value = {"ledger": {}}

    service = _build_service(store, portfolio, api_client)

    service.sync()

    store.save_trades.assert_not_called()
    assert service.last_sync_ok is False


def test_sync_keeps_degraded_when_live_reconcile_unavailable():
    store = Mock()
    portfolio = Mock()
    api_client = Mock()

    store.get_trades.return_value = []
    store.get_latest_ledger_entry.return_value = None
    store.get_cash_flows.return_value = []
    portfolio.balances = {}
    portfolio._normalize_asset.side_effect = lambda a: a
    api_client.get_private.return_value = {"trades": {}}
    api_client.get_ledgers.return_value = {"ledger": {}}

    service = _build_service(store, portfolio, api_client)
    previous_sync_at = object()
    service._last_sync_at = previous_sync_at

    def _reconcile_unavailable():
        service._set_last_sync_state(ok=False, reason=LIVE_SYNC_DEGRADED_REASON)
        return False

    service._reconcile.side_effect = _reconcile_unavailable

    result = service.sync()

    assert result == {"new_trades": 0, "new_cash_flows": 0}
    assert service.last_sync_ok is False
    assert service.last_sync_reason == LIVE_SYNC_DEGRADED_REASON
    assert service.last_sync_at is previous_sync_at
    service._refresh_cached_views.assert_called_once()


def test_sync_does_not_persist_cash_flows_on_failure():
    # Since we moved logic to manager.py and removed portfolio.ingest_cashflows,
    # we need to simulate a failure during processing in manager.py
    # But manager.py uses BalanceEngine and classify_cashflow which are hard to mock failure for here without patching imports.
    # However, if save_ledger_entry fails, we might want to ensure we don't save cash flows?
    # Or if we just want to verify the old test intent: "if ingestion fails, don't save".
    # With the new code, we iterate and save ledgers individually.
    # Cash flows are collected and saved in batch at the end.
    # If an exception occurs in the loop (e.g. save_ledger_entry fails), we shouldn't save cash flows.

    store = Mock()
    portfolio = Mock()
    api_client = Mock()

    store.get_trades.return_value = []
    store.get_latest_ledger_entry.return_value = None
    store.get_cash_flows.return_value = []
    portfolio._normalize_trade_payload.side_effect = lambda t: t
    portfolio.balances = {}
    portfolio._normalize_asset.side_effect = lambda a: a

    # Simulate DB failure on saving ledger
    store.save_ledger_entry.side_effect = RuntimeError("db failed")

    api_client.get_private.return_value = {"trades": {}, "last": None}
    api_client.get_ledgers.return_value = {
        "ledger": {
            "L1": {
                "time": 2,
                "asset": "USD",
                "amount": 5,
                "type": "deposit",
            }
        }
    }

    service = _build_service(store, portfolio, api_client)

    try:
        service.sync()
    except RuntimeError:
        pass

    store.save_cash_flows.assert_not_called()
    # last_sync_ok should be False?
    # manager.py doesn't wrap the ledger loop in try/except!
    # It catches exception in TRADES ingestion, but not LEDGER ingestion?
    # Let's check manager.py.
    # If it crashes, last_sync_ok remains False (set at start).
    assert service.last_sync_ok is False


def test_paper_sync_keeps_local_wallet_and_caches_exchange_reference():
    store = Mock()
    portfolio = Mock()
    api_client = Mock()

    portfolio.balances = {
        "USD": SimpleNamespace(asset="USD", free=10000.0, reserved=0.0, total=10000.0)
    }
    portfolio.positions = {}
    portfolio.realized_pnl_history = []
    portfolio.realized_pnl_base_by_pair = {}
    portfolio.fees_paid_base_by_pair = {}
    portfolio.maybe_snapshot = Mock()
    portfolio.get_positions.return_value = []
    portfolio.equity_view.return_value = SimpleNamespace(
        equity_base=10000.0,
        cash_base=10000.0,
        realized_pnl_base_total=0.0,
        unrealized_pnl_base_total=0.0,
        drift_flag=False,
    )
    portfolio.get_asset_exposure.return_value = []
    portfolio.get_drift_status.return_value = SimpleNamespace(
        drift_flag=False,
        expected_position_value_base=0.0,
        actual_balance_value_base=0.0,
        tolerance_base=0.0,
        mismatched_assets=[],
    )
    portfolio._convert_to_base_currency.side_effect = (
        lambda amount, asset: SimpleNamespace(
            value_base=float(amount) if asset == "USD" else float(amount) * 50000.0,
            status="valued",
        )
    )

    api_client.get_private.return_value = {
        "ZUSD": "125.50",
        "XXBT": "0.0100000000",
    }

    service = _build_service(store, portfolio, api_client)
    service.app_config = SimpleNamespace(execution=SimpleNamespace(mode="paper"))
    service.market_data = Mock()
    service.market_data.normalize_asset.side_effect = lambda asset: {
        "ZUSD": "USD",
        "XXBT": "XBT",
    }.get(asset, asset)

    result = service.sync()

    assert result == {"new_trades": 0, "new_cash_flows": 0}
    assert service.last_sync_ok is True
    assert portfolio.balances["USD"].total == 10000.0
    portfolio.maybe_snapshot.assert_called_once()
    store.save_balance_snapshot.assert_called_once()
    assert service.get_exchange_reference_summary()["cash_usd"] == 125.50


def test_portfolio_ingest_trades_skips_pairs_outside_active_universe():
    market_data = Mock()
    market_data.get_pair_metadata.side_effect = PairNotFoundError("GALAUSD")
    store = Mock()
    portfolio = Portfolio(PortfolioConfig(), market_data, store)

    portfolio.ingest_trades(
        [
            {
                "id": "T1",
                "pair": "GALAUSD",
                "type": "sell",
                "price": "0.02",
                "cost": "2.00",
                "fee": "0.01",
                "vol": "100.0",
                "time": 1,
            }
        ],
        persist=False,
    )

    assert portfolio.get_positions() == []
