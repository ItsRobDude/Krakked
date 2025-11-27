# tests/test_portfolio_store.py

import pytest
import os
from kraken_bot.portfolio.store import SQLitePortfolioStore
from kraken_bot.portfolio.models import CashFlowRecord, PortfolioSnapshot, AssetValuation

@pytest.fixture
def store(tmp_path):
    db_path = tmp_path / "test_portfolio.db"
    return SQLitePortfolioStore(str(db_path))

def test_save_and_get_trades(store):
    trades = [
        {"id": "T1", "pair": "XBTUSD", "time": 1000, "price": 50000, "vol": 1, "cost": 50000, "type": "buy"},
        {"id": "T2", "pair": "XBTUSD", "time": 1001, "price": 51000, "vol": 0.5, "cost": 25500, "type": "sell"}
    ]
    store.save_trades(trades)

    fetched = store.get_trades()
    assert len(fetched) == 2
    assert fetched[0]['id'] == "T2" # Descending order
    assert fetched[1]['id'] == "T1"

    # Test filtering
    fetched_since = store.get_trades(since=1001)
    assert len(fetched_since) == 1
    assert fetched_since[0]['id'] == "T2"

def test_save_and_get_cash_flows(store):
    flows = [
        CashFlowRecord("C1", 1000, "USD", 1000.0, "deposit", "Initial"),
        CashFlowRecord("C2", 1002, "USD", -50.0, "withdrawal", "Test")
    ]
    store.save_cash_flows(flows)

    fetched = store.get_cash_flows()
    assert len(fetched) == 2
    assert fetched[0].id == "C2" # Descending

    fetched_since = store.get_cash_flows(since=1001)
    assert len(fetched_since) == 1
    assert fetched_since[0].id == "C2"

def test_save_and_get_snapshots(store):
    s1 = PortfolioSnapshot(
        timestamp=1000,
        equity_base=10000.0,
        cash_base=5000.0,
        asset_valuations=[AssetValuation("XBT", 0.1, 5000.0, "XBTUSD")],
        realized_pnl_base_total=100.0,
        unrealized_pnl_base_total=200.0,
        realized_pnl_base_by_pair={"XBTUSD": 100.0},
        unrealized_pnl_base_by_pair={"XBTUSD": 200.0}
    )
    store.save_snapshot(s1)

    snapshots = store.get_snapshots()
    assert len(snapshots) == 1
    assert snapshots[0].equity_base == 10000.0
    assert snapshots[0].asset_valuations[0].asset == "XBT"

    # Test update
    s2 = PortfolioSnapshot(
        timestamp=1000, # Same timestamp
        equity_base=11000.0, # Updated value
        cash_base=5000.0,
        asset_valuations=[],
        realized_pnl_base_total=0,
        unrealized_pnl_base_total=0,
        realized_pnl_base_by_pair={},
        unrealized_pnl_base_by_pair={}
    )
    store.save_snapshot(s2)
    snapshots = store.get_snapshots()
    assert len(snapshots) == 1
    assert snapshots[0].equity_base == 11000.0

def test_prune_snapshots(store):
    store.save_snapshot(PortfolioSnapshot(100, 0, 0, [], 0, 0, {}, {}))
    store.save_snapshot(PortfolioSnapshot(200, 0, 0, [], 0, 0, {}, {}))

    store.prune_snapshots(150) # Remove older than 150 (i.e. 100)

    snapshots = store.get_snapshots()
    assert len(snapshots) == 1
    assert snapshots[0].timestamp == 200
