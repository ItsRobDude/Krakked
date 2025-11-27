# tests/test_portfolio_store.py

import pytest
import sqlite3
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

def test_save_orders_with_list_field(store):
    # Test new save_orders method which normalizes 'trades' list
    order_with_list = {
        "id": "O3", "pair": "XBTUSD", "status": "closed",
        "opentm": 1000, "closetm": 1002,
        "trades": ["TX1", "TX2"]
    }
    store.save_orders([order_with_list])

    # Verify DB content directly
    conn = sqlite3.connect(store.db_path)
    cursor = conn.cursor()

    # Check Order
    cursor.execute("SELECT order_id, pair FROM orders WHERE order_id='O3'")
    row = cursor.fetchone()
    assert row is not None
    assert row[0] == "O3"

    # Check Relations
    cursor.execute("SELECT trade_id FROM order_trades WHERE order_id='O3' ORDER BY trade_id")
    rows = cursor.fetchall()
    assert len(rows) == 2
    assert rows[0][0] == "TX1"
    assert rows[1][0] == "TX2"

    conn.close()

def test_save_trades_legacy_compat(store):
    # Even if we use save_trades with a list field (legacy usage?), it shouldn't crash
    # Though V2 store ignores 'trades' list field in 'trades' table now.
    trade_with_list = {
        "id": "T3", "pair": "XBTUSD", "time": 1002, "type": "buy",
        "price": 50000, "vol": 1, "cost": 50000,
        "trades": ["TX1", "TX2"] # This field should be ignored by V2 save_trades SQL
    }
    store.save_trades([trade_with_list])

    fetched = store.get_trades(since=1002)
    assert len(fetched) == 1
    # raw_json should still have it
    assert fetched[0]['trades'] == ["TX1", "TX2"]

def test_schema_migration(tmp_path):
    db_path = tmp_path / "migration_test.db"

    # 1. Setup V1 DB manually
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    # Create Schema Version 1
    cursor.execute("CREATE TABLE schema_version (id INTEGER PRIMARY KEY, version INTEGER)")
    cursor.execute("INSERT INTO schema_version (id, version) VALUES (1, 1)")

    # Create V1 Trades table with trades_csv
    cursor.execute("""
        CREATE TABLE trades (
            id TEXT PRIMARY KEY,
            ordertxid TEXT,
            pair TEXT,
            time REAL,
            type TEXT,
            ordertype TEXT,
            price REAL,
            cost REAL,
            fee REAL,
            vol REAL,
            margin REAL,
            misc TEXT,
            posstatus TEXT,
            cprice REAL,
            ccost REAL,
            cfee REAL,
            cvol REAL,
            cmargin REAL,
            net REAL,
            trades_csv TEXT,
            raw_json TEXT
        )
    """)

    # Insert some data
    cursor.execute("INSERT INTO trades (id, pair, time, trades_csv, raw_json) VALUES (?, ?, ?, ?, ?)",
                   ("T1", "XBTUSD", 1000.0, "TXA,TXB", '{"id":"T1", "trades":["TXA","TXB"]}'))
    conn.commit()
    conn.close()

    # 2. Init Store which should trigger migration
    store = SQLitePortfolioStore(str(db_path))

    # 3. Verify Migration
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    # Version should be 2
    cursor.execute("SELECT version FROM schema_version")
    assert cursor.fetchone()[0] == 2

    # Trades table should NOT have trades_csv
    cursor.execute("PRAGMA table_info(trades)")
    columns = [r[1] for r in cursor.fetchall()]
    assert "trades_csv" not in columns
    assert "id" in columns

    # Data should be preserved
    cursor.execute("SELECT id, pair, raw_json FROM trades WHERE id='T1'")
    row = cursor.fetchone()
    assert row[0] == "T1"
    assert row[1] == "XBTUSD"
    # New tables should exist
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='orders'")
    assert cursor.fetchone() is not None
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='order_trades'")
    assert cursor.fetchone() is not None

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
