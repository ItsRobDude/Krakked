from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from src.kraken_bot.config import AppConfig
from src.kraken_bot.portfolio.manager import PortfolioService
from src.kraken_bot.portfolio.models import AssetBalance, LedgerEntry


@pytest.fixture
def mock_store():
    store = MagicMock()
    # Default mocks
    store.get_ledger_entries.return_value = []
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
    config.strategies = MagicMock()
    config.strategies.configs = {}

    market_data = MagicMock()

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
    mock_rest_client.get_private.side_effect = lambda ep, params=None: (
        {"trades": {}} if ep == "TradesHistory" else {}
    )

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

    # Pre-seed balance
    service.portfolio.balances["USD"] = AssetBalance("USD", 500.0, 500.0, 0.0)

    # Run reconcile (via sync or directly)
    # We call _reconcile directly to test the fallback logic
    service._reconcile()

    # Should log warning but not crash, and drift check should be skipped
    pass
