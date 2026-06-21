# tests/test_portfolio_store.py

import json
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal

import pytest

import krakked.portfolio.store as store_module
from krakked.execution.models import ExecutionResult, LocalOrder
from krakked.portfolio import migrations
from krakked.portfolio.exceptions import PortfolioSchemaError
from krakked.portfolio.models import (
    AssetValuation,
    CashFlowRecord,
    LedgerEntry,
    PortfolioSnapshot,
)
from krakked.portfolio.store import CURRENT_SCHEMA_VERSION, SQLitePortfolioStore
from krakked.strategy.models import DecisionRecord, ExecutionPlan, RiskAdjustedAction


@pytest.fixture
def store(tmp_path):
    db_path = tmp_path / "test_portfolio.db"
    return SQLitePortfolioStore(str(db_path))


def test_sqlite_store_is_concrete(tmp_path):
    db_path = tmp_path / "concrete.db"

    # If any @abstractmethod is missing, this will raise TypeError
    store = SQLitePortfolioStore(str(db_path))

    # Sanity: it’s an instance of the ABC
    from krakked.portfolio.store import PortfolioStore

    assert isinstance(store, PortfolioStore)
    assert SQLitePortfolioStore.__abstractmethods__ == set()


def seed_schema_version(db_path, version: int) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('schema_version', ?)",
            (str(version),),
        )
        conn.commit()


def test_schema_version_initialized(tmp_path):
    db_path = tmp_path / "schema_init.db"
    SQLitePortfolioStore(str(db_path))

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()

    assert row is not None
    assert int(row[0]) == CURRENT_SCHEMA_VERSION


def test_schema_version_mismatch_triggers_migration(tmp_path, monkeypatch):
    db_path = tmp_path / "schema_mismatch.db"
    outdated_version = CURRENT_SCHEMA_VERSION - 1
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('schema_version', ?)",
            (str(outdated_version),),
        )
        conn.commit()

    called = {}

    def fake_run_migrations(conn, from_version, to_version):
        called["args"] = (from_version, to_version)
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
            (str(to_version),),
        )
        conn.commit()

    monkeypatch.setattr(store_module, "run_migrations", fake_run_migrations)

    SQLitePortfolioStore(str(db_path))

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()

    assert row is not None
    assert int(row[0]) == CURRENT_SCHEMA_VERSION
    assert called.get("args") == (outdated_version, CURRENT_SCHEMA_VERSION)


def test_schema_version_mismatch_without_migration_raises(tmp_path):
    db_path = tmp_path / "schema_no_migrate.db"
    seed_schema_version(db_path, CURRENT_SCHEMA_VERSION - 1)

    with pytest.raises(PortfolioSchemaError):
        SQLitePortfolioStore(str(db_path), auto_migrate_schema=False)


def test_schema_version_initialized_without_migration(tmp_path):
    db_path = tmp_path / "schema_init_no_migrate.db"
    SQLitePortfolioStore(str(db_path), auto_migrate_schema=False)

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()

    assert row is not None
    assert int(row[0]) == CURRENT_SCHEMA_VERSION


def test_schema_version_ahead_raises(tmp_path):
    db_path = tmp_path / "schema_ahead.db"
    seed_schema_version(db_path, CURRENT_SCHEMA_VERSION + 1)

    with pytest.raises(PortfolioSchemaError):
        SQLitePortfolioStore(str(db_path))


def test_fresh_schema_has_indexed_client_order_id(tmp_path):
    db_path = tmp_path / "client_order_id_fresh.db"
    SQLitePortfolioStore(str(db_path))

    with sqlite3.connect(db_path) as conn:
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(execution_orders)")
        }
        indexes = {
            row[1] for row in conn.execute("PRAGMA index_list(execution_orders)")
        }

    assert "client_order_id" in columns
    assert "idx_execution_orders_client_order_id" in indexes


def test_fresh_schema_has_indexed_trade_ledger_lag_lookup(tmp_path):
    db_path = tmp_path / "trade_ledger_lag_index.db"
    SQLitePortfolioStore(str(db_path))

    with sqlite3.connect(db_path) as conn:
        indexes = {row[1] for row in conn.execute("PRAGMA index_list(ledger_entries)")}

    assert "idx_ledger_entries_type_refid_time" in indexes


def test_fresh_schema_has_reviewed_trade_ledger_refs_table(tmp_path):
    db_path = tmp_path / "reviewed_trade_refs_fresh.db"
    SQLitePortfolioStore(str(db_path))

    with sqlite3.connect(db_path) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master")}
        indexes = {
            row[1]
            for row in conn.execute("PRAGMA index_list(reviewed_trade_ledger_refs)")
        }

    assert "reviewed_trade_ledger_refs" in tables
    assert "reviewed_trade_ledger_ref_entries" in tables
    assert "trade_ledger_ref_review_events" in tables
    assert "idx_reviewed_trade_ledger_refs_reviewed_at" in indexes


def test_v11_to_latest_migration_adds_trade_ref_review_tables(tmp_path):
    db_path = tmp_path / "reviewed_trade_refs_migrate.db"
    with sqlite3.connect(db_path) as conn:
        migrations._ensure_meta_table(conn)
        migrations._set_schema_version(conn, 11)
        migrations.run_migrations(conn, 11, CURRENT_SCHEMA_VERSION)
        row = conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()
        tables = {item[0] for item in conn.execute("SELECT name FROM sqlite_master")}

    assert row == (str(CURRENT_SCHEMA_VERSION),)
    assert "reviewed_trade_ledger_refs" in tables
    assert "reviewed_trade_ledger_ref_entries" in tables
    assert "trade_ledger_ref_review_events" in tables


def test_v12_to_v13_migration_backfills_review_entries_and_audit(tmp_path):
    db_path = tmp_path / "reviewed_trade_refs_v12_migrate.db"
    with sqlite3.connect(db_path) as conn:
        migrations._ensure_meta_table(conn)
        migrations._set_schema_version(conn, 12)
        conn.execute(
            """
            CREATE TABLE reviewed_trade_ledger_refs (
                refid TEXT PRIMARY KEY,
                reviewed_at TEXT NOT NULL,
                reviewed_by TEXT NOT NULL,
                reason TEXT NOT NULL,
                ledger_entry_ids_json TEXT NOT NULL,
                context_json TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO reviewed_trade_ledger_refs (
                refid, reviewed_at, reviewed_by, reason, ledger_entry_ids_json, context_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "T-MISSING",
                "2026-01-02T03:04:05+00:00",
                "ops",
                "verified",
                '["L-1", "L-2"]',
                '{"source": "test"}',
            ),
        )
        migrations.run_migrations(conn, 12, CURRENT_SCHEMA_VERSION)
        entries = conn.execute(
            """
            SELECT refid, ledger_entry_id
            FROM reviewed_trade_ledger_ref_entries
            ORDER BY ledger_entry_id
            """
        ).fetchall()
        events = conn.execute(
            """
            SELECT refid, event_type, actor, ledger_entry_ids_json
            FROM trade_ledger_ref_review_events
            """
        ).fetchall()

    assert entries == [("T-MISSING", "L-1"), ("T-MISSING", "L-2")]
    assert events == [("T-MISSING", "review", "ops", '["L-1", "L-2"]')]


def test_unmatched_trade_ledger_ref_times_excludes_stored_trades(tmp_path):
    db_path = tmp_path / "unmatched_trade_refs.db"
    store = SQLitePortfolioStore(str(db_path))

    def ledger_entry(entry_id: str, refid: str, time: float) -> LedgerEntry:
        return LedgerEntry(
            id=entry_id,
            time=time,
            type="trade",
            subtype="",
            aclass="currency",
            asset="USD",
            amount=Decimal("1"),
            fee=Decimal("0"),
            balance=None,
            refid=refid,
            misc=None,
            raw={},
        )

    store.save_ledger_entry(ledger_entry("L-missing-newer", "T-MISSING", 20.0))
    store.save_ledger_entry(ledger_entry("L-missing-older", "T-MISSING", 10.0))
    store.save_ledger_entry(ledger_entry("L-matched", "T-MATCHED", 5.0))
    store.save_ledger_entry(
        LedgerEntry(
            id="L-deposit",
            time=1.0,
            type="deposit",
            subtype="",
            aclass="currency",
            asset="USD",
            amount=Decimal("1"),
            fee=Decimal("0"),
            balance=None,
            refid="T-DEPOSIT",
            misc=None,
            raw={},
        )
    )
    store.save_trades(
        [
            {
                "id": "T-MATCHED",
                "pair": "XBTUSD",
                "time": 5.0,
                "type": "buy",
                "price": "100",
                "cost": "100",
                "fee": "0",
                "vol": "1",
            }
        ]
    )

    assert store.get_unmatched_trade_ledger_ref_times(
        include_refids={"T-MISSING", "T-MATCHED"}
    ) == {"T-MISSING": 10.0}


def test_reviewed_trade_ledger_ref_only_excludes_reviewed_ledger_ids(tmp_path):
    db_path = tmp_path / "reviewed_trade_ref_excluded.db"
    store = SQLitePortfolioStore(str(db_path))

    def trade_ledger(entry_id: str, timestamp: float) -> LedgerEntry:
        return LedgerEntry(
            id=entry_id,
            time=timestamp,
            type="trade",
            subtype="",
            aclass="currency",
            asset="USD",
            amount=Decimal("1"),
            fee=Decimal("0"),
            balance=None,
            refid="T-MISSING",
            misc=None,
            raw={},
        )

    store.save_ledger_entry(trade_ledger("L-missing", 10.0))

    before = store.get_unmatched_trade_ledger_refs()

    assert [item.refid for item in before] == ["T-MISSING"]

    review = store.mark_trade_ledger_ref_reviewed(
        refid="T-MISSING",
        reviewed_by="ops",
        reason="Verified manually in Kraken",
        ledger_entry_ids=["L-missing"],
        context={"source": "test"},
        reviewed_at="2026-01-02T03:04:05+00:00",
    )

    assert review.refid == "T-MISSING"
    assert review.ledger_entry_ids == ["L-missing"]
    assert store.get_unmatched_trade_ledger_ref_times() == {}
    assert store.get_unmatched_trade_ledger_refs() == []

    reviewed = store.get_unmatched_trade_ledger_refs(include_reviewed=True)

    assert len(reviewed) == 1
    assert reviewed[0].reviewed is True
    assert reviewed[0].reviewed_by == "ops"
    assert reviewed[0].reason == "Verified manually in Kraken"
    assert reviewed[0].ledger_entries[0]["reviewed"] is True

    with pytest.raises(ValueError):
        store.mark_trade_ledger_ref_reviewed(
            refid="T-MISSING",
            reviewed_by="ops",
            reason="duplicate",
            ledger_entry_ids=["L-missing"],
            context={},
        )

    store.save_ledger_entry(trade_ledger("L-missing-2", 20.0))

    assert store.get_unmatched_trade_ledger_ref_times() == {"T-MISSING": 20.0}
    unreviewed = store.get_unmatched_trade_ledger_refs()
    assert [entry["id"] for entry in unreviewed[0].ledger_entries] == ["L-missing-2"]
    assert unreviewed[0].reviewed is True

    event = store.revoke_trade_ledger_ref_review(
        refid="T-MISSING",
        revoked_by="ops",
        reason="mistaken review",
        context={"source": "test"},
        revoked_at="2026-01-02T04:04:05+00:00",
    )

    assert event.event_type == "revoke"
    assert set(event.ledger_entry_ids) == {"L-missing"}
    assert store.get_unmatched_trade_ledger_ref_times() == {"T-MISSING": 10.0}

    with pytest.raises(ValueError):
        store.revoke_trade_ledger_ref_review(
            refid="T-MISSING",
            revoked_by="ops",
            reason="duplicate revoke",
            context={},
        )


def test_save_order_populates_indexed_client_order_id(tmp_path):
    db_path = tmp_path / "client_order_id_save.db"
    store = SQLitePortfolioStore(str(db_path))
    order = LocalOrder(
        local_id="local-1",
        plan_id="plan",
        strategy_id="strategy",
        pair="XBTUSD",
        side="buy",
        order_type="limit",
        status="submit_unknown",
        raw_request={"cl_ord_id": "client-1"},
    )

    store.save_order(order)

    fetched = store.get_order_by_client_order_id("client-1")
    assert fetched is not None
    assert fetched.local_id == "local-1"
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT client_order_id FROM execution_orders WHERE local_id = 'local-1'"
        ).fetchone()
    assert row == ("client-1",)


def test_v11_migration_backfills_client_order_id(tmp_path):
    db_path = tmp_path / "client_order_id_migrate.db"
    with sqlite3.connect(db_path) as conn:
        migrations._ensure_meta_table(conn)
        migrations._set_schema_version(conn, 10)
        conn.execute(
            """
            CREATE TABLE execution_orders (
                local_id TEXT PRIMARY KEY,
                plan_id TEXT,
                strategy_id TEXT,
                pair TEXT NOT NULL,
                side TEXT NOT NULL,
                order_type TEXT,
                kraken_order_id TEXT,
                userref INTEGER,
                requested_base_size REAL,
                requested_price REAL,
                status TEXT,
                created_at REAL,
                updated_at REAL,
                cumulative_base_filled REAL,
                avg_fill_price REAL,
                last_error TEXT,
                raw_request_json TEXT,
                raw_response_json TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO execution_orders (
                local_id, pair, side, status, raw_request_json
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                "local-1",
                "XBTUSD",
                "buy",
                "submit_unknown",
                json.dumps({"cl_ord_id": "client-1"}),
            ),
        )
        migrations.run_migrations(conn, 10, CURRENT_SCHEMA_VERSION)
        row = conn.execute(
            "SELECT client_order_id FROM execution_orders WHERE local_id = 'local-1'"
        ).fetchone()
        indexes = {
            item[1] for item in conn.execute("PRAGMA index_list(execution_orders)")
        }

    assert row == ("client-1",)
    assert "idx_execution_orders_client_order_id" in indexes


def test_save_and_get_trades(store):
    trades = [
        {
            "id": "T1",
            "pair": "XBTUSD",
            "time": 1000,
            "price": 50000,
            "vol": 1,
            "cost": 50000,
            "type": "buy",
        },
        {
            "id": "T2",
            "pair": "XBTUSD",
            "time": 1001,
            "price": 51000,
            "vol": 0.5,
            "cost": 25500,
            "type": "sell",
        },
    ]
    store.save_trades(trades)

    fetched = store.get_trades()
    assert len(fetched) == 2
    assert fetched[0]["id"] == "T2"  # Descending order
    assert fetched[1]["id"] == "T1"

    # Test filtering
    fetched_since = store.get_trades(since=1001)
    assert len(fetched_since) == 1
    assert fetched_since[0]["id"] == "T2"


def test_save_trades_with_list_field(store):
    # Regression test for 'InterfaceError' when 'trades' is a list
    trade_with_list = {
        "id": "T3",
        "pair": "XBTUSD",
        "time": 1002,
        "type": "buy",
        "price": 50000,
        "vol": 1,
        "cost": 50000,
        "trades": ["TX1", "TX2"],
    }
    store.save_trades([trade_with_list])

    fetched = store.get_trades(since=1002)
    assert len(fetched) == 1
    assert fetched[0]["id"] == "T3"
    # Verify raw_json preserved the list
    assert fetched[0]["trades"] == ["TX1", "TX2"]


def test_save_and_get_cash_flows(store):
    flows = [
        CashFlowRecord("C1", 1000, "USD", 1000.0, "deposit", "Initial"),
        CashFlowRecord("C2", 1002, "USD", -50.0, "withdrawal", "Test"),
    ]
    store.save_cash_flows(flows)

    fetched = store.get_cash_flows()
    assert len(fetched) == 2
    assert fetched[0].id == "C2"  # Descending

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
        unrealized_pnl_base_by_pair={"XBTUSD": 200.0},
    )
    store.save_snapshot(s1)

    snapshots = store.get_snapshots()
    assert len(snapshots) == 1
    assert snapshots[0].equity_base == 10000.0
    assert snapshots[0].asset_valuations[0].asset == "XBT"

    # Test update
    s2 = PortfolioSnapshot(
        timestamp=1000,  # Same timestamp
        equity_base=11000.0,  # Updated value
        cash_base=5000.0,
        asset_valuations=[],
        realized_pnl_base_total=0,
        unrealized_pnl_base_total=0,
        realized_pnl_base_by_pair={},
        unrealized_pnl_base_by_pair={},
    )
    store.save_snapshot(s2)
    snapshots = store.get_snapshots()
    assert len(snapshots) == 1
    assert snapshots[0].equity_base == 11000.0


def test_prune_snapshots(store):
    store.save_snapshot(PortfolioSnapshot(100, 0, 0, [], 0, 0, {}, {}))
    store.save_snapshot(PortfolioSnapshot(200, 0, 0, [], 0, 0, {}, {}))

    store.prune_snapshots(150)  # Remove older than 150 (i.e. 100)

    snapshots = store.get_snapshots()
    assert len(snapshots) == 1
    assert snapshots[0].timestamp == 200


def test_get_trades_with_until_and_ordering(store):
    trades = [
        {
            "id": "T1",
            "pair": "XBTUSD",
            "time": 1000,
            "price": 50000,
            "vol": 1,
            "cost": 50000,
            "type": "buy",
        },
        {
            "id": "T2",
            "pair": "XBTUSD",
            "time": 1005,
            "price": 52000,
            "vol": 1,
            "cost": 52000,
            "type": "buy",
        },
        {
            "id": "T3",
            "pair": "ETHUSD",
            "time": 1010,
            "price": 3000,
            "vol": 2,
            "cost": 6000,
            "type": "sell",
        },
    ]
    store.save_trades(trades)

    limited = store.get_trades(until=1005, ascending=True)
    assert [t["id"] for t in limited] == ["T1", "T2"]

    eth_only = store.get_trades(pair="ETHUSD")
    assert len(eth_only) == 1
    assert eth_only[0]["id"] == "T3"


def test_get_cash_flows_with_until_and_ordering(store):
    flows = [
        CashFlowRecord("C1", 1000, "USD", 1000.0, "deposit", "Initial"),
        CashFlowRecord("C2", 1010, "USD", -50.0, "withdrawal", "Test"),
        CashFlowRecord("C3", 1020, "ETH", 1.0, "deposit", None),
    ]
    store.save_cash_flows(flows)

    usd_flows = store.get_cash_flows(asset="USD", ascending=True)
    assert [f.id for f in usd_flows] == ["C1", "C2"]

    bounded = store.get_cash_flows(until=1015)
    assert len(bounded) == 2
    assert bounded[0].id == "C2"


def test_decision_and_execution_plan_retrieval(store):
    decision = DecisionRecord(
        time=1700000000,
        plan_id="PLAN-1",
        strategy_name="trend",
        pair="XBTUSD",
        action_type="open",
        target_position_usd=1000.0,
        blocked=False,
        block_reason=None,
        kill_switch_active=False,
        raw_json="{}",
    )
    store.add_decision(decision)

    decisions = store.get_decisions(plan_id="PLAN-1")
    assert len(decisions) == 1
    assert decisions[0].strategy_name == "trend"

    plan = ExecutionPlan(
        plan_id="PLAN-1",
        generated_at=datetime.fromtimestamp(1700000000, tz=timezone.utc),
        actions=[
            RiskAdjustedAction(
                pair="XBTUSD",
                strategy_id="trend",
                strategy_tag="trend",
                userref=123,
                action_type="open",
                target_base_size=0.01,
                target_notional_usd=1000.0,
                current_base_size=0.0,
                reason="entry",
                blocked=False,
                blocked_reasons=[],
                risk_limits_snapshot={},
            )
        ],
        metadata={"note": "test"},
    )

    store.save_execution_plan(plan)

    fetched = store.get_execution_plan("PLAN-1")
    assert fetched is not None
    assert fetched.plan_id == "PLAN-1"
    assert fetched.actions[0].pair == "XBTUSD"
    assert fetched.actions[0].userref == 123
    assert fetched.actions[0].strategy_tag == "trend"
    assert fetched.metadata["note"] == "test"

    recent = store.get_execution_plans(since=1699999999, limit=1)
    assert len(recent) == 1


def test_save_and_load_local_order(store):
    created_at = datetime(2024, 1, 1, 12, 0, 0)
    order = LocalOrder(
        local_id="LOCAL-1",
        plan_id="PLAN-LOCAL",
        strategy_id="strat-1",
        pair="XBTUSD",
        side="buy",
        order_type="limit",
        kraken_order_id="KRAKEN-1",
        userref=42,
        requested_base_size=0.25,
        requested_price=25000.5,
        status="submitted",
        created_at=created_at,
        updated_at=created_at,
        cumulative_base_filled=0.05,
        avg_fill_price=24950.1,
        last_error="temporary glitch",
        raw_request={"price": "25000.5", "volume": "0.25", "validate": True},
        raw_response={
            "descr": {"order": "buy 0.25 XBTUSD @ limit 25000.5"},
            "txid": ["KRAKEN-1"],
        },
    )

    store.save_order(order)

    open_orders = store.get_open_orders()
    assert len(open_orders) == 1

    loaded = open_orders[0]
    assert loaded.status == "submitted"
    assert loaded.requested_price == 25000.5
    assert loaded.avg_fill_price == 24950.1
    assert loaded.raw_request == {
        "price": "25000.5",
        "volume": "0.25",
        "validate": True,
    }
    assert loaded.raw_response == {
        "descr": {"order": "buy 0.25 XBTUSD @ limit 25000.5"},
        "txid": ["KRAKEN-1"],
    }
    assert loaded.last_error == "temporary glitch"

    by_reference = store.get_order_by_reference(kraken_order_id="KRAKEN-1")
    assert by_reference is not None
    assert by_reference.userref == 42
    assert by_reference.raw_request == loaded.raw_request
    assert by_reference.raw_response == loaded.raw_response


def test_save_and_load_execution_result(store):
    started_at = datetime(2024, 1, 2, 15, 30, 0, tzinfo=timezone.utc)
    completed_at = datetime(2024, 1, 2, 15, 45, 0, tzinfo=timezone.utc)
    result = ExecutionResult(
        plan_id="PLAN-RESULT",
        started_at=started_at,
        completed_at=completed_at,
        success=False,
        orders=[],
        errors=["order failed", "insufficient funds"],
        warnings=["retry later"],
    )

    store.save_execution_result(result)

    loaded_results = store.get_execution_results(limit=1)
    assert len(loaded_results) == 1

    loaded = loaded_results[0]
    assert loaded.plan_id == "PLAN-RESULT"
    assert loaded.success is False
    assert loaded.started_at == started_at
    assert loaded.completed_at == completed_at
    assert loaded.errors == ["order failed", "insufficient funds"]
