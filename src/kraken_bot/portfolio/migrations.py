"""Database migrations for the portfolio SQLite backend."""

from __future__ import annotations

import sqlite3
from typing import Callable, Dict


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
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_cash_flows_asset ON cash_flows(asset)")

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
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_timestamp ON snapshots(timestamp)")


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
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_decisions_plan_id ON decisions(plan_id)")


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


def run_migrations(conn: sqlite3.Connection, from_version: int, to_version: int) -> None:
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
    }

    for version in range(from_version, to_version):
        migrate = migrations.get(version)
        if migrate is None:
            raise ValueError(f"No migration path from version {version} to {version + 1}")

        migrate(conn)
        _set_schema_version(conn, version + 1)
