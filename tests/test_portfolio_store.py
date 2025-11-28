# tests/test_portfolio_store.py

import pytest
import sqlite3
import os
import json
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

def test_save_trades_with_list_field(store):
    # Regression test for 'InterfaceError' when 'trades' is a list
    trade_with_list = {
        "id": "T3", "pair": "XBTUSD", "time": 1002, "type": "buy",
        "price": 50000, "vol": 1, "cost": 50000,
        "trades": ["TX1", "TX2"]
    }
    store.save_trades([trade_with_list])

    fetched = store.get_trades(since=1002)
    assert len(fetched) == 1
    # raw_json should still have it
    assert fetched[0]['trades'] == ["TX1", "TX2"]

def test_save_and_get_orders(store):
    # Test saving ClosedOrders (which have list fields)
    orders = [
        {
            "id": "O1",
            "status": "closed",
            "opentm": 1000,
            "closetm": 1005,
            "userref": 12345,
            "descr": {"pair": "XBTUSD", "type": "buy"},
            "trades": ["T1", "T2"]
        }
    ]
    store.save_orders(orders)

    # Retrieve via direct SQL or get_order
    order = store.get_order("O1")
    assert order is not None
    assert order["id"] == "O1"
    assert order["userref"] == 12345

    # Verify userref persistence
    conn = sqlite3.connect(store.db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT userref, status FROM orders WHERE order_id='O1'")
    row = cursor.fetchone()
    assert row[0] == 12345
    assert row[1] == "closed"
    conn.close()

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
