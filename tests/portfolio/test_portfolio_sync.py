import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from krakked.config import PortfolioConfig
from krakked.market_data.exceptions import PairNotFoundError
from krakked.portfolio.manager import PortfolioService
from krakked.portfolio.portfolio import Portfolio
from krakked.portfolio.sync_status import (
    LIVE_SYNC_DEGRADED_REASON,
    PORTFOLIO_SYNC_FAILED_REASON,
)


def _build_service(store, portfolio, api_client):
    store.get_trade_ledger_ref_times.return_value = {}
    store.get_trade_ids_by_ids.side_effect = lambda trade_ids: set()
    service = PortfolioService.__new__(PortfolioService)
    service.config = PortfolioConfig()
    service.app_config = SimpleNamespace(execution=SimpleNamespace(mode="live"))
    service.store = store
    service.portfolio = portfolio
    service.rest_client = api_client
    service._bootstrapped = True
    service._last_sync_ok = True
    service._last_sync_reason = None
    service._last_sync_at = None
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
    service._reconcile = Mock(return_value=True)
    return service


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
