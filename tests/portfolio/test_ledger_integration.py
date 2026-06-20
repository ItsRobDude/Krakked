from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from krakked.config import AppConfig
from krakked.portfolio.manager import PortfolioService
from krakked.portfolio.models import AssetBalance, LedgerEntry
from krakked.portfolio.sync_status import LIVE_SYNC_DEGRADED_REASON


@pytest.fixture
def mock_store():
    store = MagicMock()
    # Default mocks
    store.get_ledger_entries.return_value = []
    store.get_latest_ledger_entry.return_value = None
    store.get_latest_balance_snapshot.return_value = None
    store.get_trades.return_value = []
    store.get_all_ledger_entries.return_value = []
    return store


@pytest.fixture
def mock_rest_client():
    client = MagicMock()
    return client


@pytest.fixture
def service(mock_store, mock_rest_client):
    config = MagicMock(spec=AppConfig)
    config.portfolio = MagicMock()
    config.portfolio.reconciliation_tolerance = 0.0001
    config.portfolio.base_currency = "USD"
    config.execution = SimpleNamespace(mode="live")
    config.session = SimpleNamespace(profile_name=None)
    config.strategies = MagicMock()
    config.strategies.configs = {}

    market_data = MagicMock()

    def _norm(asset):
        asset = str(asset)
        return {"XXBT": "XBT", "XBT": "XBT", "ZUSD": "USD", "USD": "USD"}.get(
            asset, asset
        )

    market_data.normalize_asset.side_effect = _norm
    market_data.get_valuation_pair.side_effect = lambda asset: (
        "XBTUSD" if _norm(asset) == "XBT" else None
    )

    svc = PortfolioService(
        config=config, market_data=market_data, rest_client=mock_rest_client
    )
    svc.store = mock_store
    return svc


def test_offline_bootstrap(service, mock_store):
    # Setup store with ledgers (normalized asset names as expected in DB)
    ledgers = [
        LedgerEntry(
            id="1",
            time=100,
            type="deposit",
            subtype="",
            aclass="",
            asset="USD",
            amount=Decimal("1000"),
            fee=Decimal("0"),
            balance=None,
            refid=None,
            misc=None,
            raw={},
        ),
    ]
    mock_store.get_ledger_entries.return_value = ledgers
    mock_store.get_all_ledger_entries.return_value = ledgers

    # Bootstrap
    service._bootstrap_from_store()

    # Assert balances rebuilt
    assert "USD" in service.portfolio.balances
    assert service.portfolio.balances["USD"].total == 1000.0


def test_sync_ingestion(service, mock_store, mock_rest_client):
    # Setup
    mock_store.get_ledger_entries.return_value = []
    mock_store.get_trades.return_value = []

    # Mock API response for ledgers (raw assets from API)
    mock_rest_client.get_ledgers.return_value = {
        "ledger": {
            "L1": {
                "refid": "R1",
                "time": 200,
                "type": "deposit",
                "aclass": "currency",
                "asset": "XXBT",
                "amount": "1.5",
                "fee": "0.0",
                "balance": "1.5",
            }
        }
    }

    # Mock API response for TradesHistory (empty to skip that part)
    def _private_response(ep, params=None):  # noqa: ARG001
        if ep == "TradesHistory":
            return {"trades": {}}
        if ep == "Balance":
            return {"XXBT": "1.5"}
        return {}

    mock_rest_client.get_private.side_effect = _private_response

    # Run sync
    service.sync()

    # Verify save_ledger_entry called
    assert mock_store.save_ledger_entry.call_count == 1
    args, _ = mock_store.save_ledger_entry.call_args
    entry = args[0]
    assert entry.id == "L1"
    assert entry.amount == Decimal("1.5")

    # Verify cash flows saved
    assert mock_store.save_cash_flows.call_count == 1

    # Verify balance updated in memory (normalized)
    assert "XBT" in service.portfolio.balances
    assert service.portfolio.balances["XBT"].total == 1.5


def test_offline_reconcile_fallback(service, mock_rest_client):
    # Setup: Balance API fails
    mock_rest_client.get_private.side_effect = Exception("API Down")
    service._last_sync_ok = True
    service._last_sync_reason = None

    # Pre-seed balance
    service.portfolio.balances["USD"] = AssetBalance("USD", 500.0, 500.0, 0.0)

    # Run reconcile (via sync or directly)
    # We call _reconcile directly to test the degraded truth-unavailable state.
    result = service._reconcile()

    assert result is False
    assert service.last_sync_ok is False
    assert service.last_sync_reason == LIVE_SYNC_DEGRADED_REASON


def test_sync_marks_degraded_when_live_balance_read_fails(service, mock_rest_client):
    """Real sync() fails closed end-to-end when the live Balance read fails."""
    mock_rest_client.get_ledgers.return_value = {"ledger": {}}

    def _private_response(ep, params=None):  # noqa: ARG001
        if ep == "TradesHistory":
            return {"trades": {}}
        if ep == "Balance":
            raise Exception("API Down")
        return {}

    mock_rest_client.get_private.side_effect = _private_response

    sentinel = object()
    service._last_sync_at = sentinel
    service._last_sync_ok = True
    service._last_sync_reason = None

    service.sync()

    assert service.last_sync_ok is False
    assert service.last_sync_reason == LIVE_SYNC_DEGRADED_REASON
    # last_sync_at must NOT advance on a degraded sync; the last good time is preserved.
    assert service.last_sync_at is sentinel
