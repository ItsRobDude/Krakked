"""Database migrations for the portfolio SQLite backend."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
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
            clamped INTEGER NOT NULL DEFAULT 0,
            clamp_reason TEXT,
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
            client_order_id TEXT,
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
        "CREATE INDEX IF NOT EXISTS idx_execution_orders_client_order_id ON execution_orders(client_order_id)"
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
        10: migrate_10_to_11,
        11: migrate_11_to_12,
        12: migrate_12_to_13,
        13: migrate_13_to_14,
        14: migrate_14_to_15,
        15: migrate_15_to_16,
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


def migrate_10_to_11(conn: sqlite3.Connection) -> None:
    """Add indexed client order id storage for live order reconciliation."""
    cursor = conn.cursor()
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='execution_orders'"
    )
    if cursor.fetchone() is None:
        return

    columns = {
        row[1]
        for row in cursor.execute("PRAGMA table_info(execution_orders)").fetchall()
    }

    if "client_order_id" not in columns:
        cursor.execute("ALTER TABLE execution_orders ADD COLUMN client_order_id TEXT")

    rows = cursor.execute(
        """
        SELECT local_id, raw_request_json
        FROM execution_orders
        WHERE client_order_id IS NULL
          AND raw_request_json IS NOT NULL
        """
    ).fetchall()
    for local_id, raw_request_json in rows:
        client_order_id = None
        try:
            raw_request = json.loads(raw_request_json)
        except (TypeError, json.JSONDecodeError):
            raw_request = {}
        if isinstance(raw_request, dict):
            raw_value = raw_request.get("cl_ord_id")
            if isinstance(raw_value, str) and raw_value.strip():
                client_order_id = raw_value.strip()
        if client_order_id:
            cursor.execute(
                """
                UPDATE execution_orders
                SET client_order_id = ?
                WHERE local_id = ?
                """,
                (client_order_id, local_id),
            )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_execution_orders_client_order_id
            ON execution_orders(client_order_id)
        """
    )


def migrate_11_to_12(conn: sqlite3.Connection) -> None:
    """Add audited operator reviews for unmatched trade ledger refs."""
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS reviewed_trade_ledger_refs (
            refid TEXT PRIMARY KEY,
            reviewed_at TEXT NOT NULL,
            reviewed_by TEXT NOT NULL,
            reason TEXT NOT NULL,
            ledger_entry_ids_json TEXT NOT NULL,
            context_json TEXT NOT NULL
        )
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_reviewed_trade_ledger_refs_reviewed_at
            ON reviewed_trade_ledger_refs(reviewed_at)
        """
    )


def migrate_12_to_13(conn: sqlite3.Connection) -> None:
    """Scope reviewed trade refs to ledger entry IDs and add review audit events."""
    cursor = conn.cursor()
    migrate_11_to_12(conn)
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS reviewed_trade_ledger_ref_entries (
            refid TEXT NOT NULL,
            ledger_entry_id TEXT NOT NULL,
            reviewed_at TEXT NOT NULL,
            PRIMARY KEY (refid, ledger_entry_id)
        )
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_reviewed_trade_ledger_ref_entries_ledger_id
            ON reviewed_trade_ledger_ref_entries(ledger_entry_id)
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS trade_ledger_ref_review_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            refid TEXT NOT NULL,
            event_type TEXT NOT NULL,
            event_at TEXT NOT NULL,
            actor TEXT NOT NULL,
            reason TEXT NOT NULL,
            ledger_entry_ids_json TEXT NOT NULL,
            context_json TEXT NOT NULL
        )
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_trade_ledger_ref_review_events_refid_time
            ON trade_ledger_ref_review_events(refid, event_at)
        """
    )

    rows = cursor.execute(
        """
        SELECT refid, reviewed_at, reviewed_by, reason, ledger_entry_ids_json, context_json
        FROM reviewed_trade_ledger_refs
        """
    ).fetchall()
    for (
        refid,
        reviewed_at,
        reviewed_by,
        reason,
        ledger_entry_ids_json,
        context_json,
    ) in rows:
        try:
            parsed_ids = (
                json.loads(ledger_entry_ids_json) if ledger_entry_ids_json else []
            )
        except (TypeError, json.JSONDecodeError):
            parsed_ids = []
        ledger_ids = (
            [str(item) for item in parsed_ids] if isinstance(parsed_ids, list) else []
        )
        for ledger_id in ledger_ids:
            cursor.execute(
                """
                INSERT OR IGNORE INTO reviewed_trade_ledger_ref_entries (
                    refid, ledger_entry_id, reviewed_at
                ) VALUES (?, ?, ?)
                """,
                (refid, ledger_id, reviewed_at),
            )
        existing_event = cursor.execute(
            """
            SELECT 1
            FROM trade_ledger_ref_review_events
            WHERE refid = ?
              AND event_type = 'review'
              AND event_at = ?
            LIMIT 1
            """,
            (refid, reviewed_at),
        ).fetchone()
        if existing_event is None:
            cursor.execute(
                """
                INSERT INTO trade_ledger_ref_review_events (
                    refid,
                    event_type,
                    event_at,
                    actor,
                    reason,
                    ledger_entry_ids_json,
                    context_json
                ) VALUES (?, 'review', ?, ?, ?, ?, ?)
                """,
                (
                    refid,
                    reviewed_at,
                    reviewed_by,
                    reason,
                    json.dumps(ledger_ids, sort_keys=True),
                    context_json or "{}",
                ),
            )


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table'
          AND name = ?
        LIMIT 1
        """,
        (table_name,),
    ).fetchone()
    return row is not None


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    if not _table_exists(conn, table_name):
        return set()
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table_name})")}


def _json_str_list_or_empty(value: Any) -> list[str]:
    try:
        parsed = json.loads(value) if value else []
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if str(item).strip()]


def _json_dict_or_empty(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(value) if value else {}
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _cleanup_ambiguous_trade_ref_reviews(
    conn: sqlite3.Connection, *, migration_name: str
) -> None:
    """Fail closed on active review rows that cannot be proven current/unmatched."""

    required_tables = {
        "reviewed_trade_ledger_ref_entries",
        "trade_ledger_ref_review_events",
    }
    if not all(_table_exists(conn, table) for table in required_tables):
        return

    cursor = conn.cursor()
    validation_tables_present = all(
        _table_exists(conn, table) for table in {"ledger_entries", "trades"}
    )
    if not validation_tables_present:
        active_review_count = int(
            cursor.execute(
                "SELECT COUNT(*) FROM reviewed_trade_ledger_ref_entries"
            ).fetchone()[0]
            or 0
        )
        if active_review_count:
            preserved_detail = (
                "; active review rows remain preserved in "
                "reviewed_trade_ledger_ref_entries_v13"
                if migration_name == "v13_to_v14"
                else ""
            )
            raise sqlite3.OperationalError(
                "Cannot validate active trade-ledger review suppressions during "
                f"{migration_name}: ledger_entries and trades tables are required"
                f"{preserved_detail}"
            )
        return

    rows = cursor.execute(
        """
        SELECT
            re.refid,
            re.ledger_entry_id,
            re.reviewed_at,
            re.reviewed_by,
            re.reason,
            re.context_json,
            re.review_event_id,
            CASE
                WHEN le.id IS NULL THEN 'ledger_entry_missing_or_mismatched'
                WHEN t.id IS NOT NULL THEN 'trade_history_now_matched'
                ELSE 'unknown'
            END AS cleanup_reason
        FROM reviewed_trade_ledger_ref_entries AS re
        LEFT JOIN ledger_entries AS le
          ON le.id = re.ledger_entry_id
         AND le.refid = re.refid
         AND le.type = 'trade'
         AND le.refid IS NOT NULL
         AND TRIM(le.refid) != ''
        LEFT JOIN trades AS t ON t.id = re.refid
        WHERE le.id IS NULL
           OR t.id IS NOT NULL
        ORDER BY re.refid ASC, re.ledger_entry_id ASC
        """
    ).fetchall()

    if not rows:
        return

    grouped: dict[str, list[tuple[Any, ...]]] = {}
    for row in rows:
        grouped.setdefault(str(row[0]), []).append(row)

    event_at = datetime.now(timezone.utc).isoformat()
    reason = "Removed ambiguous active trade-ledger review suppression during migration"
    for refid, ref_rows in grouped.items():
        ledger_ids = [str(row[1]) for row in ref_rows]
        removed_reviews = [
            {
                "refid": refid,
                "ledger_entry_id": str(row[1]),
                "reviewed_at": row[2],
                "reviewed_by": row[3],
                "reason": row[4],
                "context": _json_dict_or_empty(row[5]),
                "review_event_id": row[6],
                "cleanup_reason": row[7],
            }
            for row in ref_rows
        ]
        cursor.execute(
            """
            INSERT INTO trade_ledger_ref_review_events (
                refid,
                event_type,
                event_at,
                actor,
                reason,
                ledger_entry_ids_json,
                context_json
            ) VALUES (?, 'migration_cleanup', ?, 'migration', ?, ?, ?)
            """,
            (
                refid,
                event_at,
                reason,
                json.dumps(ledger_ids, sort_keys=True),
                json.dumps(
                    {
                        "migration": migration_name,
                        "removed_active_reviews": removed_reviews,
                    },
                    sort_keys=True,
                ),
            ),
        )
        cursor.executemany(
            """
            DELETE FROM reviewed_trade_ledger_ref_entries
            WHERE refid = ?
              AND ledger_entry_id = ?
            """,
            [(refid, ledger_id) for ledger_id in ledger_ids],
        )


def migrate_13_to_14(conn: sqlite3.Connection) -> None:
    """Make reviewed trade-ledger suppression per-entry and drop refid state."""

    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS trade_ledger_ref_review_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            refid TEXT NOT NULL,
            event_type TEXT NOT NULL,
            event_at TEXT NOT NULL,
            actor TEXT NOT NULL,
            reason TEXT NOT NULL,
            ledger_entry_ids_json TEXT NOT NULL,
            context_json TEXT NOT NULL
        )
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_trade_ledger_ref_review_events_refid_time
            ON trade_ledger_ref_review_events(refid, event_at)
        """
    )

    legacy_reviews: dict[str, dict[str, Any]] = {}
    if _table_exists(conn, "reviewed_trade_ledger_refs"):
        for row in cursor.execute(
            """
            SELECT refid, reviewed_at, reviewed_by, reason, ledger_entry_ids_json, context_json
            FROM reviewed_trade_ledger_refs
            """
        ).fetchall():
            refid = str(row[0])
            legacy_reviews[refid] = {
                "reviewed_at": row[1],
                "reviewed_by": row[2],
                "reason": row[3],
                "ledger_ids": _json_str_list_or_empty(row[4]),
                "context_json": row[5] or "{}",
            }

    event_reviews: dict[tuple[str, str], dict[str, Any]] = {}
    for row in cursor.execute(
        """
        SELECT id, refid, event_at, actor, reason, ledger_entry_ids_json, context_json
        FROM trade_ledger_ref_review_events
        WHERE event_type = 'review'
        ORDER BY id ASC
        """
    ).fetchall():
        event_id = int(row[0])
        refid = str(row[1])
        for ledger_id in _json_str_list_or_empty(row[5]):
            event_reviews[(refid, ledger_id)] = {
                "review_event_id": event_id,
                "reviewed_at": row[2],
                "reviewed_by": row[3],
                "reason": row[4],
                "context_json": row[6] or "{}",
            }

    existing_entries: list[dict[str, Any]] = []

    def collect_existing_entries(table_name: str) -> None:
        columns = _table_columns(conn, table_name)
        reviewed_by_expr = (
            "reviewed_by" if "reviewed_by" in columns else "NULL AS reviewed_by"
        )
        reason_expr = "reason" if "reason" in columns else "NULL AS reason"
        context_expr = (
            "context_json" if "context_json" in columns else "NULL AS context_json"
        )
        event_expr = (
            "review_event_id"
            if "review_event_id" in columns
            else "NULL AS review_event_id"
        )
        for row in cursor.execute(
            f"""
            SELECT
                refid,
                ledger_entry_id,
                reviewed_at,
                {reviewed_by_expr},
                {reason_expr},
                {context_expr},
                {event_expr}
            FROM {table_name}
            """
        ).fetchall():
            existing_entries.append(
                {
                    "refid": str(row[0]),
                    "ledger_entry_id": str(row[1]),
                    "reviewed_at": row[2],
                    "reviewed_by": row[3],
                    "reason": row[4],
                    "context_json": row[5],
                    "review_event_id": row[6],
                }
            )

    target_table = "reviewed_trade_ledger_ref_entries"
    staging_table = "reviewed_trade_ledger_ref_entries_v13"
    target_exists = _table_exists(conn, target_table)
    staging_exists = _table_exists(conn, staging_table)
    if staging_exists:
        collect_existing_entries(staging_table)
    if target_exists:
        collect_existing_entries(target_table)

    if target_exists and staging_exists:
        cursor.execute(f"DROP TABLE {target_table}")
    elif target_exists:
        cursor.execute(
            "ALTER TABLE reviewed_trade_ledger_ref_entries RENAME TO reviewed_trade_ledger_ref_entries_v13"
        )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS reviewed_trade_ledger_ref_entries (
            refid TEXT NOT NULL,
            ledger_entry_id TEXT NOT NULL,
            reviewed_at TEXT NOT NULL,
            reviewed_by TEXT NOT NULL,
            reason TEXT NOT NULL,
            context_json TEXT NOT NULL,
            review_event_id INTEGER,
            PRIMARY KEY (refid, ledger_entry_id)
        )
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_reviewed_trade_ledger_ref_entries_ledger_id
            ON reviewed_trade_ledger_ref_entries(ledger_entry_id)
        """
    )

    active_entries: dict[tuple[str, str], dict[str, Any]] = {}
    for refid, legacy in legacy_reviews.items():
        for ledger_id in legacy["ledger_ids"]:
            metadata = event_reviews.get((refid, ledger_id), {})
            active_entries[(refid, ledger_id)] = {
                "refid": refid,
                "ledger_entry_id": ledger_id,
                "reviewed_at": legacy["reviewed_at"],
                "reviewed_by": legacy["reviewed_by"],
                "reason": legacy["reason"],
                "context_json": legacy["context_json"],
                "review_event_id": metadata.get("review_event_id"),
            }

    for entry in existing_entries:
        key = (entry["refid"], entry["ledger_entry_id"])
        metadata = event_reviews.get(key, {})
        legacy = legacy_reviews.get(entry["refid"], {})
        active_entries[key] = {
            "refid": entry["refid"],
            "ledger_entry_id": entry["ledger_entry_id"],
            "reviewed_at": (
                entry.get("reviewed_at")
                or legacy.get("reviewed_at")
                or metadata.get("reviewed_at")
                or "1970-01-01T00:00:00+00:00"
            ),
            "reviewed_by": (
                entry.get("reviewed_by")
                or legacy.get("reviewed_by")
                or metadata.get("reviewed_by")
                or "migration"
            ),
            "reason": (
                entry.get("reason")
                or legacy.get("reason")
                or metadata.get("reason")
                or "Migrated active review metadata unavailable"
            ),
            "context_json": (
                entry.get("context_json")
                or legacy.get("context_json")
                or metadata.get("context_json")
                or json.dumps({"migration_warning": "metadata_unavailable"})
            ),
            "review_event_id": entry.get("review_event_id")
            or metadata.get("review_event_id"),
        }

    cursor.executemany(
        """
        INSERT OR REPLACE INTO reviewed_trade_ledger_ref_entries (
            refid,
            ledger_entry_id,
            reviewed_at,
            reviewed_by,
            reason,
            context_json,
            review_event_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                entry["refid"],
                entry["ledger_entry_id"],
                entry["reviewed_at"],
                entry["reviewed_by"],
                entry["reason"],
                entry["context_json"],
                entry["review_event_id"],
            )
            for entry in active_entries.values()
        ],
    )

    _cleanup_ambiguous_trade_ref_reviews(conn, migration_name="v13_to_v14")

    cursor.execute("DROP TABLE IF EXISTS reviewed_trade_ledger_ref_entries_v13")
    cursor.execute("DROP TABLE IF EXISTS reviewed_trade_ledger_refs")


def migrate_14_to_15(conn: sqlite3.Connection) -> None:
    """Remove ambiguous active trade-ledger review suppressions."""

    _cleanup_ambiguous_trade_ref_reviews(conn, migration_name="v14_to_v15")


def migrate_15_to_16(conn: sqlite3.Connection) -> None:
    """Persist clamped decision reasons separately from blocked reasons."""

    if not _table_exists(conn, "decisions"):
        return

    cursor = conn.cursor()
    columns = _table_columns(conn, "decisions")
    if "clamped" not in columns:
        cursor.execute(
            "ALTER TABLE decisions ADD COLUMN clamped INTEGER NOT NULL DEFAULT 0"
        )
    if "clamp_reason" not in columns:
        cursor.execute("ALTER TABLE decisions ADD COLUMN clamp_reason TEXT")

    cursor.execute(
        """
        UPDATE decisions
        SET clamped = 1,
            clamp_reason = COALESCE(clamp_reason, block_reason),
            block_reason = NULL
        WHERE blocked = 0
          AND block_reason IS NOT NULL
          AND TRIM(block_reason) != ''
        """
    )
