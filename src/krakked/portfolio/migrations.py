"""Database migrations for the portfolio SQLite backend."""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Callable, Dict

Migration = Callable[[sqlite3.Connection], None]


def _ensure_meta_table(conn: sqlite3.Connection) -> None:
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )
    conn.commit()


def _ensure_schema_version_row(conn: sqlite3.Connection, version: int) -> None:
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', ?)",
        (str(version),),
    )
    conn.commit()


def _set_schema_version(conn: sqlite3.Connection, version: int) -> None:
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO meta (key, value)
        VALUES ('schema_version', ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """,
        (str(version),),
    )
    conn.commit()


def migrate_1_to_2(conn: sqlite3.Connection) -> None:
    """Initial trade storage setup."""
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS trades (
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
        """
    )
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_time ON trades(time)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_pair ON trades(pair)")


def migrate_2_to_3(conn: sqlite3.Connection) -> None:
    """Add cash flow and snapshot tracking tables."""
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS cash_flows (
            id TEXT PRIMARY KEY,
            time REAL,
            asset TEXT,
            amount REAL,
            type TEXT,
            note TEXT
        )
        """
    )
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_cash_flows_time ON cash_flows(time)")
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_cash_flows_asset ON cash_flows(asset)"
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS snapshots (
            timestamp REAL PRIMARY KEY,
            equity_base REAL,
            cash_base REAL,
            data_json TEXT
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_snapshots_timestamp ON snapshots(timestamp)"
    )


def migrate_3_to_4(conn: sqlite3.Connection) -> None:
    """Add decision logging table."""
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            time INTEGER NOT NULL,
            plan_id TEXT,
            strategy_name TEXT,
            pair TEXT,
            action_type TEXT,
            target_position_usd REAL,
            blocked INTEGER NOT NULL,
            block_reason TEXT,
            kill_switch_active INTEGER NOT NULL,
            raw_json TEXT
        )
        """
    )
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_decisions_time ON decisions(time)")
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_decisions_plan_id ON decisions(plan_id)"
    )


def migrate_4_to_5(conn: sqlite3.Connection) -> None:
    """Add execution plan, order, event, and result tracking tables."""
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS execution_plans (
            plan_id TEXT PRIMARY KEY,
            generated_at REAL NOT NULL,
            action_count INTEGER NOT NULL,
            blocked_actions INTEGER NOT NULL,
            metadata_json TEXT,
            plan_json TEXT
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_execution_plans_generated_at ON execution_plans(generated_at)"
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS execution_orders (
            local_id TEXT PRIMARY KEY,
            plan_id TEXT,
            strategy_id TEXT,
            pair TEXT NOT NULL,
            side TEXT NOT NULL,
            order_type TEXT,
            kraken_order_id TEXT,
            userref TEXT,
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
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_execution_orders_plan_id ON execution_orders(plan_id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_execution_orders_kraken_id ON execution_orders(kraken_order_id)"
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS execution_order_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            local_order_id TEXT NOT NULL,
            plan_id TEXT,
            event_time REAL NOT NULL,
            status TEXT,
            message TEXT,
            raw_json TEXT
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_execution_order_events_order ON execution_order_events(local_order_id)"
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS execution_results (
            plan_id TEXT PRIMARY KEY,
            started_at REAL,
            completed_at REAL,
            success INTEGER,
            errors_json TEXT
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_execution_results_started_at ON execution_results(started_at)"
    )


def migrate_5_to_6(conn: sqlite3.Connection) -> None:
    """Add ML training data and persisted model tables."""
    cursor = conn.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS ml_training_examples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_id TEXT NOT NULL,
            model_key   TEXT NOT NULL,
            created_at  TEXT NOT NULL,
            source_mode TEXT NOT NULL,
            label_type  TEXT NOT NULL,
            features    TEXT NOT NULL,
            label       REAL NOT NULL,
            sample_weight REAL NOT NULL DEFAULT 1.0
        )
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_ml_training_key
            ON ml_training_examples(strategy_id, model_key, created_at)
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS ml_models (
            strategy_id TEXT NOT NULL,
            model_key   TEXT NOT NULL,
            label_type  TEXT NOT NULL,
            framework   TEXT NOT NULL,
            version     INTEGER NOT NULL,
            updated_at  TEXT NOT NULL,
            model_blob  BLOB NOT NULL,
            PRIMARY KEY (strategy_id, model_key)
        )
        """
    )


def migrate_6_to_7(conn: sqlite3.Connection) -> None:
    """Add warnings_json column to execution_results table."""
    cursor = conn.cursor()
    # Check if column exists first to be safe, though not strictly required if versioning is correct.
    # SQLite doesn't support IF NOT EXISTS for ADD COLUMN easily, so we rely on versioning.
    try:
        cursor.execute("ALTER TABLE execution_results ADD COLUMN warnings_json TEXT")
    except sqlite3.OperationalError:
        # If column already exists, ignore
        pass


def run_migrations(
    conn: sqlite3.Connection, from_version: int, to_version: int
) -> None:
    """Run portfolio schema migrations sequentially."""
    if from_version == to_version:
        return

    if from_version > to_version:
        raise ValueError("from_version cannot be greater than to_version")

    _ensure_meta_table(conn)
    _ensure_schema_version_row(conn, from_version)

    migrations: Dict[int, Migration] = {
        1: migrate_1_to_2,
        2: migrate_2_to_3,
        3: migrate_3_to_4,
        4: migrate_4_to_5,
        5: migrate_5_to_6,
        6: migrate_6_to_7,
        7: migrate_7_to_8,
        8: migrate_8_to_9,
        9: migrate_9_to_10,
    }

    for version in range(from_version, to_version):
        migrate = migrations.get(version)
        if migrate is None:
            raise ValueError(
                f"No migration path from version {version} to {version + 1}"
            )

        migrate(conn)
        _set_schema_version(conn, version + 1)


def migrate_7_to_8(conn: sqlite3.Connection) -> None:
    """Add ledger entries and balance snapshots tables."""
    cursor = conn.cursor()

    # Ledger Entries Table
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS ledger_entries (
            id TEXT PRIMARY KEY,
            time REAL,
            type TEXT,
            subtype TEXT,
            aclass TEXT,
            asset TEXT,
            amount REAL,
            fee REAL,
            balance REAL,
            refid TEXT,
            misc TEXT,
            raw_json TEXT
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_ledger_entries_time ON ledger_entries(time)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_ledger_entries_refid ON ledger_entries(refid)"
    )

    # Balance Snapshots Table
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS balance_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            time REAL,
            last_ledger_id TEXT,
            balances_json TEXT
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_balance_snapshots_time ON balance_snapshots(time)"
    )


def migrate_8_to_9(conn: sqlite3.Connection) -> None:
    """Migrate ledger_entries numeric columns from REAL to TEXT for exact precision."""
    cursor = conn.cursor()

    # Safety: drop partial table if prior run failed
    cursor.execute("DROP TABLE IF EXISTS ledger_entries_new")

    # If the DB claims schema_version=8 but doesn't actually have ledger_entries yet,
    # create the v9 table directly (tests + defensive real-world behavior).
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='ledger_entries'"
    )
    if cursor.fetchone() is None:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS ledger_entries (
                id TEXT PRIMARY KEY,
                time REAL,
                type TEXT,
                subtype TEXT,
                aclass TEXT,
                asset TEXT,
                amount TEXT,
                fee TEXT,
                balance TEXT,
                refid TEXT,
                misc TEXT,
                raw_json TEXT
            )
            """
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_ledger_entries_time ON ledger_entries(time)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_ledger_entries_refid ON ledger_entries(refid)"
        )
        return

    # 1. Create new table with TEXT columns for amount, fee, balance
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS ledger_entries_new (
            id TEXT PRIMARY KEY,
            time REAL,
            type TEXT,
            subtype TEXT,
            aclass TEXT,
            asset TEXT,
            amount TEXT,   -- Changed from REAL
            fee TEXT,      -- Changed from REAL
            balance TEXT,  -- Changed from REAL
            refid TEXT,
            misc TEXT,
            raw_json TEXT
        )
        """
    )

    # 2. Migrate data
    # Use iterator for memory efficiency on large DBs
    cursor.execute("SELECT * FROM ledger_entries")

    # Schema of old table:
    # 0:id, 1:time, 2:type, 3:subtype, 4:aclass, 5:asset, 6:amount(REAL), 7:fee(REAL), 8:balance(REAL), 9:refid, 10:misc, 11:raw_json

    # Iterating cursor directly fetches rows lazily
    for row in cursor:
        raw_json_str = row[11]

        # Helper to handle None -> None (instead of "None")
        def _safe_str(val: Any) -> str | None:
            return str(val) if val is not None else None

        # Default to existing values cast to string
        amount_str = _safe_str(row[6])
        fee_str = _safe_str(row[7])
        balance_str = _safe_str(row[8])

        # Try to get exact values from raw JSON
        if raw_json_str:
            try:
                raw_data = json.loads(raw_json_str)
                # Kraken API returns these as strings or numbers.
                # If they are strings in JSON, we prefer that.
                if "amount" in raw_data and raw_data["amount"] is not None:
                    amount_str = str(raw_data["amount"])
                if "fee" in raw_data and raw_data["fee"] is not None:
                    fee_str = str(raw_data["fee"])
                if "balance" in raw_data and raw_data["balance"] is not None:
                    balance_str = str(raw_data["balance"])
            except Exception:
                # Fallback to DB values if JSON parsing fails
                pass

        # Use a new cursor for inserts to avoid interfering with the select cursor if driver is picky
        # (Though sqlite3 standard cursor often handles this, safe practice is good)
        conn.execute(
            """
            INSERT INTO ledger_entries_new (
                id, time, type, subtype, aclass, asset, amount, fee, balance, refid, misc, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row[0],
                row[1],
                row[2],
                row[3],
                row[4],
                row[5],
                amount_str,
                fee_str,
                balance_str,
                row[9],
                row[10],
                row[11],
            ),
        )

    # 3. Drop old table and rename new one
    cursor.execute("DROP TABLE IF EXISTS ledger_entries")
    cursor.execute("ALTER TABLE ledger_entries_new RENAME TO ledger_entries")

    # 4. Re-create indexes
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_ledger_entries_time ON ledger_entries(time)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_ledger_entries_refid ON ledger_entries(refid)"
    )


def migrate_9_to_10(conn: sqlite3.Connection) -> None:
    """Add ML checkpoint storage for crash-safe training resume."""
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS ml_model_checkpoints (
            strategy_id      TEXT NOT NULL,
            model_key        TEXT NOT NULL,
            checkpoint_kind  TEXT NOT NULL,
            label_type       TEXT NOT NULL,
            framework        TEXT NOT NULL,
            version          INTEGER NOT NULL,
            updated_at       TEXT NOT NULL,
            checkpoint_state TEXT NOT NULL DEFAULT 'ready',
            metadata_json    TEXT NOT NULL DEFAULT '{}',
            model_blob       BLOB NOT NULL,
            PRIMARY KEY (strategy_id, model_key, checkpoint_kind)
        )
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_ml_model_checkpoints_updated
            ON ml_model_checkpoints(strategy_id, model_key, updated_at)
        """
    )
