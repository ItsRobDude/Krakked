# src/kraken_bot/portfolio/store.py

import abc
import json
import logging
import pickle
import sqlite3
import threading
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence, Tuple, Union, cast

from kraken_bot.logging_config import structured_log_extra

from .exceptions import PortfolioSchemaError
from .migrations import _ensure_meta_table, _set_schema_version, run_migrations
from .models import AssetValuation, CashFlowRecord, PortfolioSnapshot

if TYPE_CHECKING:
    from kraken_bot.execution.models import ExecutionResult, LocalOrder
    from kraken_bot.strategy.models import DecisionRecord, ExecutionPlan

logger = logging.getLogger(__name__)

CURRENT_SCHEMA_VERSION = 6

MAX_ML_TRAINING_EXAMPLES = 5000
MIN_ML_BOOTSTRAP_EXAMPLES = 50


@dataclass
class SchemaStatus:
    version: int
    migrated: bool
    initialized: bool

    @property
    def changed(self) -> bool:
        return self.migrated or self.initialized


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def ensure_portfolio_schema(
    conn: sqlite3.Connection | str,
    target_version: int = CURRENT_SCHEMA_VERSION,
    migrate: bool = True,
) -> SchemaStatus:
    """Ensure the portfolio DB matches the expected schema version.

    Missing schema metadata initializes the DB to ``target_version``.
    If ``migrate`` is True, migrations will be applied when the stored
    version is behind. A schema ahead of ``target_version`` raises
    :class:`PortfolioSchemaError`.
    """

    connection_provided = isinstance(conn, sqlite3.Connection)
    owned_conn = None

    if connection_provided:
        _conn = cast(sqlite3.Connection, conn)
    else:
        owned_conn = sqlite3.connect(str(conn))
        _conn = owned_conn

    initialized = False
    migrated = False
    stored_version = target_version

    try:
        _ensure_meta_table(_conn)
        cursor = _conn.cursor()
        row = cursor.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()

        if row is None:
            _set_schema_version(_conn, target_version)
            initialized = True
        else:
            try:
                stored_version = int(row[0])
            except (TypeError, ValueError) as exc:
                logger.exception(
                    "Invalid schema version stored in portfolio DB",
                    extra=structured_log_extra(event="portfolio_schema_invalid"),
                )
                raise PortfolioSchemaError(
                    found=row[0], expected=target_version
                ) from exc

            if stored_version > target_version:
                raise PortfolioSchemaError(
                    found=stored_version, expected=target_version
                )

            if migrate and stored_version < target_version:
                try:
                    run_migrations(_conn, stored_version, target_version)
                except Exception as exc:  # noqa: BLE001
                    logger.exception(
                        "Failed to run portfolio migrations from v%s to v%s",
                        stored_version,
                        target_version,
                        extra=structured_log_extra(
                            event="portfolio_migration_failed",
                            from_version=stored_version,
                            to_version=target_version,
                        ),
                    )
                    raise PortfolioSchemaError(
                        found=stored_version, expected=target_version
                    ) from exc
                migrated = True
                stored_version = target_version

        status = SchemaStatus(
            version=stored_version, migrated=migrated, initialized=initialized
        )
        return status
    finally:
        if owned_conn is not None:
            try:
                owned_conn.commit()
            except Exception:  # pragma: no cover - best effort
                owned_conn.rollback()
            finally:
                owned_conn.close()


def assert_portfolio_schema(db_path: str) -> SchemaStatus:
    """Guard that the on-disk portfolio schema matches the current version.

    No migrations are performed here; callers should run migrations explicitly via CLI
    tooling before starting the bot. A schema ahead of code or behind the expected
    version raises :class:`PortfolioSchemaError`.
    """

    status = ensure_portfolio_schema(db_path, CURRENT_SCHEMA_VERSION, migrate=False)

    if status.version < CURRENT_SCHEMA_VERSION:
        raise PortfolioSchemaError(
            found=status.version, expected=CURRENT_SCHEMA_VERSION
        )

    return status


def ensure_portfolio_tables(conn: sqlite3.Connection) -> None:
    cursor = conn.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )

    # Trades Table
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

    # Cash Flows Table
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

    # Snapshots Table
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

    # Execution Plans Table
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

    conn.commit()


class PortfolioStore(abc.ABC):
    @abc.abstractmethod
    def save_trades(self, trades: List[Dict[str, Any]]):
        """Saves raw trade data."""
        pass

    @abc.abstractmethod
    def get_trades(
        self,
        pair: Optional[str] = None,
        limit: Optional[int] = None,
        since: Optional[int] = None,
        until: Optional[int] = None,
        ascending: bool = False,
    ) -> List[Dict[str, Any]]:
        """Retrieves raw trade data with optional filtering and ordering."""
        pass

    @abc.abstractmethod
    def save_cash_flows(self, records: List[CashFlowRecord]):
        """Saves cash flow records."""
        pass

    @abc.abstractmethod
    def get_cash_flows(
        self,
        asset: Optional[str] = None,
        limit: Optional[int] = None,
        since: Optional[int] = None,
        until: Optional[int] = None,
        ascending: bool = False,
    ) -> List[CashFlowRecord]:
        """Retrieves cash flow records with optional filtering and ordering."""
        pass

    @abc.abstractmethod
    def save_snapshot(self, snapshot: PortfolioSnapshot):
        """Saves a portfolio snapshot."""
        pass

    @abc.abstractmethod
    def get_snapshots(
        self, since: Optional[int] = None, limit: Optional[int] = None
    ) -> List[PortfolioSnapshot]:
        """Retrieves portfolio snapshots."""
        pass

    @abc.abstractmethod
    def prune_snapshots(self, older_than_ts: int):
        """Removes old snapshots."""
        pass

    @abc.abstractmethod
    def add_decision(self, record: "DecisionRecord"):
        """Saves a strategy decision record."""
        pass

    @abc.abstractmethod
    def get_decisions(
        self,
        plan_id: Optional[str] = None,
        since: Optional[int] = None,
        limit: Optional[int] = None,
        strategy_name: Optional[str] = None,
        pair: Optional[str] = None,
    ) -> List["DecisionRecord"]:
        """Retrieves strategy decision records."""
        pass

    @abc.abstractmethod
    def save_execution_plan(self, plan: "ExecutionPlan"):
        """Persist an execution plan for downstream consumption."""
        pass

    @abc.abstractmethod
    def save_order(self, order: "LocalOrder"):
        """Persist an execution order."""
        pass

    @abc.abstractmethod
    def update_order_status(
        self,
        local_id: str,
        status: str,
        kraken_order_id: Optional[str] = None,
        cumulative_base_filled: Optional[float] = None,
        avg_fill_price: Optional[float] = None,
        last_error: Optional[str] = None,
        raw_response: Optional[Dict[str, Any]] = None,
        event_message: Optional[str] = None,
    ):
        """Update order status and record the change."""
        pass

    @abc.abstractmethod
    def save_execution_result(self, result: "ExecutionResult"):
        """Persist an execution result."""
        pass

    @abc.abstractmethod
    def get_order_by_reference(
        self,
        kraken_order_id: Optional[str] = None,
        userref: Optional[int] = None,
    ) -> Optional["LocalOrder"]:
        """Lookup a stored order by Kraken id or user reference."""
        pass

    @abc.abstractmethod
    def get_execution_plans(
        self,
        plan_id: Optional[str] = None,
        since: Optional[Union[int, float, datetime]] = None,
        limit: Optional[int] = None,
    ) -> List["ExecutionPlan"]:
        """Fetch stored execution plans."""
        pass

    @abc.abstractmethod
    def get_execution_plan(self, plan_id: str) -> Optional["ExecutionPlan"]:
        """Fetch a specific execution plan by id."""
        pass

    @abc.abstractmethod
    def get_open_orders(
        self, plan_id: Optional[str] = None, strategy_id: Optional[str] = None
    ) -> List["LocalOrder"]:
        """Fetch open/pending orders with optional filtering."""
        pass

    @abc.abstractmethod
    def get_execution_results(self, limit: int = 10) -> List["ExecutionResult"]:
        """Return recent execution results."""
        pass

    @abc.abstractmethod
    def record_ml_example(
        self,
        strategy_id: str,
        model_key: str,
        *,
        created_at: datetime,
        source_mode: str,
        label_type: str,
        features: Sequence[float],
        label: float,
        sample_weight: float = 1.0,
    ) -> None:
        """Record a single ML training example."""
        pass

    @abc.abstractmethod
    def load_ml_training_window(
        self,
        strategy_id: str,
        model_key: str,
        *,
        max_examples: int = MAX_ML_TRAINING_EXAMPLES,
        return_weights: bool = False,
    ) -> Tuple[List[List[float]], List[float]] | Tuple[List[List[float]], List[float], List[float]]:
        """Load a rolling window of ML training examples for a model key."""
        pass

    @abc.abstractmethod
    def save_ml_model(
        self,
        strategy_id: str,
        model_key: str,
        *,
        label_type: str,
        framework: str,
        model: object,
        version: int = 1,
    ) -> None:
        """Persist a serialized ML model."""
        pass

    @abc.abstractmethod
    def load_ml_model(self, strategy_id: str, model_key: str) -> Optional[object]:
        """Load a serialized ML model if present."""
        pass

    def get_schema_version(self) -> Optional[int]:
        """Return the stored schema version if available."""

        return None


class SQLitePortfolioStore(PortfolioStore):
    def __init__(self, db_path: str = "portfolio.db", auto_migrate_schema: bool = True):
        self.db_path = db_path
        self.auto_migrate_schema = auto_migrate_schema
        self._lock = threading.RLock()
        self._init_db()

    def _init_db(self) -> None:
        """
        Initialize the portfolio database with strict schema handling.

        Rules:
        - If auto_migrate_schema=True:
            * Run migrations up to CURRENT_SCHEMA_VERSION.
            * Create all required tables.
        - If auto_migrate_schema=False:
            * Do NOT migrate.
            * Require on-disk schema to already be exactly CURRENT_SCHEMA_VERSION.
            * Raise PortfolioSchemaError if the DB is behind or ahead.
        """

        # 1. Guard / migrate schema version via the shared helpers.
        if self.auto_migrate_schema:
            # May create meta table and run migrations.
            ensure_portfolio_schema(self.db_path, CURRENT_SCHEMA_VERSION, migrate=True)
        else:
            # Strict: only accept an exact match; behind/ahead → PortfolioSchemaError.
            assert_portfolio_schema(self.db_path)

        # 2. Ensure all logical portfolio tables exist.
        conn = sqlite3.connect(self.db_path)
        try:
            ensure_portfolio_tables(conn)
            conn.commit()
        finally:
            conn.close()

    def _get_conn(self):
        return sqlite3.connect(self.db_path)

    def get_schema_version(self) -> Optional[int]:
        conn = None
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            row = cursor.execute(
                "SELECT value FROM meta WHERE key = 'schema_version'"
            ).fetchone()
            if row is None:
                return None

            try:
                return int(row[0])
            except (TypeError, ValueError):
                logger.warning(
                    "Non-integer schema version stored in portfolio DB",
                    extra=structured_log_extra(event="schema_unknown"),
                )
                return None
        except sqlite3.Error as exc:  # pragma: no cover - defensive logging
            logger.warning("Unable to read portfolio schema version: %s", exc)
            return None
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # pragma: no cover - best effort cleanup
                    pass

    def save_trades(self, trades: List[Dict[str, Any]]):
        if not trades:
            return

        with self._lock:
            conn = self._get_conn()
            try:
                cursor = conn.cursor()

                for trade in trades:
                    # We assume 'trade' is the raw dictionary from Kraken API or internal representation
                    # The 'id' in our table maps to the trade ID (key in the dictionary usually)
                    # However, Kraken 'TradesHistory' returns a dict where keys are trade IDs.
                    # We need to handle that before calling this, or assume 'trades' here is a list of flattened dicts with 'id' field.
                    # Let's assume flattened dict with 'id'.

                    raw_json = json.dumps(trade)

                    # Handle 'trades' field which can be a list of strings
                    trades_val = trade.get("trades")
                    trades_csv = None
                    if isinstance(trades_val, list):
                        trades_csv = ",".join(str(t) for t in trades_val)
                    elif trades_val is not None:
                        trades_csv = str(trades_val)

                    cursor.execute(
                        """
                        INSERT OR IGNORE INTO trades (
                            id, ordertxid, pair, time, type, ordertype, price, cost, fee, vol,
                            margin, misc, posstatus, cprice, ccost, cfee, cvol, cmargin, net,
                            trades_csv, raw_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                        (
                            trade.get("id"),
                            trade.get("ordertxid"),
                            trade.get("pair"),
                            trade.get("time"),
                            trade.get("type"),
                            trade.get("ordertype"),
                            float(trade.get("price", 0)),
                            float(trade.get("cost", 0)),
                            float(trade.get("fee", 0)),
                            float(trade.get("vol", 0)),
                            float(trade.get("margin", 0)),
                            trade.get("misc"),
                            trade.get("posstatus"),
                            (
                                float(trade.get("cprice", 0))
                                if trade.get("cprice")
                                else None
                            ),
                            (
                                float(trade.get("ccost", 0))
                                if trade.get("ccost")
                                else None
                            ),
                            float(trade.get("cfee", 0)) if trade.get("cfee") else None,
                            float(trade.get("cvol", 0)) if trade.get("cvol") else None,
                            (
                                float(trade.get("cmargin", 0))
                                if trade.get("cmargin")
                                else None
                            ),
                            float(trade.get("net", 0)) if trade.get("net") else None,
                            trades_csv,  # trades list CSV
                            raw_json,
                        ),
                    )
                conn.commit()
            finally:
                conn.close()

    def get_trades(
        self,
        pair: Optional[str] = None,
        limit: Optional[int] = None,
        since: Optional[int] = None,
        until: Optional[int] = None,
        ascending: bool = False,
    ) -> List[Dict[str, Any]]:
        conn = self._get_conn()
        cursor = conn.cursor()

        query = "SELECT raw_json FROM trades WHERE 1=1"
        params: List[Any] = []

        if pair:
            query += " AND pair = ?"
            params.append(pair)

        if since is not None:
            query += " AND time >= ?"
            params.append(since)

        if until is not None:
            query += " AND time <= ?"
            params.append(until)

        order = "ASC" if ascending else "DESC"
        query += f" ORDER BY time {order}"

        if limit:
            query += " LIMIT ?"
            params.append(limit)

        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()

        return [json.loads(row[0]) for row in rows]

    def save_cash_flows(self, records: List[CashFlowRecord]):
        if not records:
            return

        with self._lock:
            conn = self._get_conn()
            try:
                cursor = conn.cursor()

                for record in records:
                    cursor.execute(
                        """
                        INSERT OR IGNORE INTO cash_flows (
                            id, time, asset, amount, type, note
                        ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                        (
                            record.id,
                            record.time,
                            record.asset,
                            record.amount,
                            record.type,
                            record.note,
                        ),
                    )
                conn.commit()
            finally:
                conn.close()

    def get_cash_flows(
        self,
        asset: Optional[str] = None,
        limit: Optional[int] = None,
        since: Optional[int] = None,
        until: Optional[int] = None,
        ascending: bool = False,
    ) -> List[CashFlowRecord]:
        conn = self._get_conn()
        cursor = conn.cursor()

        query = "SELECT id, time, asset, amount, type, note FROM cash_flows WHERE 1=1"
        params: List[Any] = []

        if asset:
            query += " AND asset = ?"
            params.append(asset)

        if since is not None:
            query += " AND time >= ?"
            params.append(since)

        if until is not None:
            query += " AND time <= ?"
            params.append(until)

        order = "ASC" if ascending else "DESC"
        query += f" ORDER BY time {order}"

        if limit:
            query += " LIMIT ?"
            params.append(limit)

        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()

        return [
            CashFlowRecord(
                id=row[0],
                time=row[1],
                asset=row[2],
                amount=row[3],
                type=row[4],
                note=row[5],
            )
            for row in rows
        ]

    def save_snapshot(self, snapshot: PortfolioSnapshot):
        with self._lock:
            conn = self._get_conn()
            try:
                cursor = conn.cursor()

                # We store the heavy lifting in JSON
                data = {
                    "asset_valuations": [
                        {
                            "asset": av.asset,
                            "amount": av.amount,
                            "value_base": av.value_base,
                            "source_pair": av.source_pair,
                            "valuation_status": av.valuation_status,
                        }
                        for av in snapshot.asset_valuations
                    ],
                    "realized_pnl_base_total": snapshot.realized_pnl_base_total,
                    "unrealized_pnl_base_total": snapshot.unrealized_pnl_base_total,
                    "realized_pnl_base_by_pair": snapshot.realized_pnl_base_by_pair,
                    "unrealized_pnl_base_by_pair": snapshot.unrealized_pnl_base_by_pair,
                }

                cursor.execute(
                    """
                    INSERT OR REPLACE INTO snapshots (
                        timestamp, equity_base, cash_base, data_json
                    ) VALUES (?, ?, ?, ?)
                """,
                    (
                        snapshot.timestamp,
                        snapshot.equity_base,
                        snapshot.cash_base,
                        json.dumps(data),
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def get_snapshots(
        self, since: Optional[int] = None, limit: Optional[int] = None
    ) -> List[PortfolioSnapshot]:
        conn = self._get_conn()
        cursor = conn.cursor()

        query = "SELECT timestamp, equity_base, cash_base, data_json FROM snapshots WHERE 1=1"
        params = []

        if since:
            query += " AND timestamp >= ?"
            params.append(since)

        query += " ORDER BY timestamp DESC"

        if limit:
            query += " LIMIT ?"
            params.append(limit)

        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()

        snapshots = []
        for row in rows:
            data = json.loads(row[3])
            snapshots.append(
                PortfolioSnapshot(
                    timestamp=row[0],
                    equity_base=row[1],
                    cash_base=row[2],
                    asset_valuations=[
                        AssetValuation(
                            asset=av.get("asset"),
                            amount=av.get("amount", 0.0),
                            value_base=av.get("value_base", 0.0),
                            source_pair=av.get("source_pair"),
                            valuation_status=av.get("valuation_status", "valued"),
                        )
                        for av in data["asset_valuations"]
                    ],
                    realized_pnl_base_total=data["realized_pnl_base_total"],
                    unrealized_pnl_base_total=data["unrealized_pnl_base_total"],
                    realized_pnl_base_by_pair=data["realized_pnl_base_by_pair"],
                    unrealized_pnl_base_by_pair=data["unrealized_pnl_base_by_pair"],
                )
            )
        return snapshots

    def prune_snapshots(self, older_than_ts: int):
        with self._lock:
            conn = self._get_conn()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    "DELETE FROM snapshots WHERE timestamp < ?", (older_than_ts,)
                )
                conn.commit()
            finally:
                conn.close()

    def add_decision(self, record: "DecisionRecord"):
        with self._lock:
            conn = self._get_conn()
            try:
                cursor = conn.cursor()

                cursor.execute(
                    """
                    INSERT INTO decisions (
                        time, plan_id, strategy_name, pair, action_type,
                        target_position_usd, blocked, block_reason, kill_switch_active, raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        record.time,
                        record.plan_id,
                        record.strategy_name,
                        record.pair,
                        record.action_type,
                        record.target_position_usd,
                        1 if record.blocked else 0,
                        record.block_reason,
                        1 if record.kill_switch_active else 0,
                        record.raw_json,
                    ),
                )

                conn.commit()
            finally:
                conn.close()

    def save_execution_plan(self, plan: "ExecutionPlan"):
        with self._lock:
            conn = self._get_conn()
            try:
                cursor = conn.cursor()

                plan_json = json.dumps(
                    {
                        "plan_id": plan.plan_id,
                        "generated_at": plan.generated_at,
                        "actions": [asdict(a) for a in plan.actions],
                        "metadata": plan.metadata,
                    },
                    default=str,
                )

                cursor.execute(
                    """
                    INSERT OR REPLACE INTO execution_plans (
                        plan_id, generated_at, action_count, blocked_actions, metadata_json, plan_json
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        plan.plan_id,
                        plan.generated_at.timestamp(),
                        len(plan.actions),
                        len([a for a in plan.actions if a.blocked]),
                        json.dumps(plan.metadata, default=str),
                        plan_json,
                    ),
                )

                conn.commit()
            finally:
                conn.close()

    def save_order(self, order: "LocalOrder"):
        with self._lock:
            conn = self._get_conn()
            try:
                cursor = conn.cursor()

                created_ts = (
                    order.created_at.timestamp()
                    if isinstance(order.created_at, datetime)
                    else None
                )
                updated_ts = (
                    order.updated_at.timestamp()
                    if isinstance(order.updated_at, datetime)
                    else None
                )

                cursor.execute(
                    """
                    INSERT OR REPLACE INTO execution_orders (
                        local_id, plan_id, strategy_id, pair, side, order_type, kraken_order_id, userref,
                        requested_base_size, requested_price, status, created_at, updated_at,
                        cumulative_base_filled, avg_fill_price, last_error, raw_request_json, raw_response_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        order.local_id,
                        order.plan_id,
                        order.strategy_id,
                        order.pair,
                        order.side,
                        order.order_type,
                        order.kraken_order_id,
                        order.userref,
                        order.requested_base_size,
                        order.requested_price,
                        order.status,
                        created_ts,
                        updated_ts,
                        order.cumulative_base_filled,
                        order.avg_fill_price,
                        order.last_error,
                        (
                            json.dumps(order.raw_request, default=str)
                            if order.raw_request
                            else None
                        ),
                        (
                            json.dumps(order.raw_response, default=str)
                            if order.raw_response
                            else None
                        ),
                    ),
                )

                cursor.execute(
                    """
                    INSERT INTO execution_order_events (
                        local_order_id, plan_id, event_time, status, message, raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        order.local_id,
                        order.plan_id,
                        updated_ts or created_ts or datetime.now(UTC).timestamp(),
                        order.status,
                        order.last_error,
                        (
                            json.dumps(order.raw_response, default=str)
                            if order.raw_response
                            else None
                        ),
                    ),
                )

                conn.commit()
            finally:
                conn.close()

    def update_order_status(
        self,
        local_id: str,
        status: str,
        kraken_order_id: Optional[str] = None,
        cumulative_base_filled: Optional[float] = None,
        avg_fill_price: Optional[float] = None,
        last_error: Optional[str] = None,
        raw_response: Optional[Dict[str, Any]] = None,
        event_message: Optional[str] = None,
    ):
        with self._lock:
            conn = self._get_conn()
            try:
                cursor = conn.cursor()

                cursor.execute(
                    "SELECT plan_id FROM execution_orders WHERE local_id = ?",
                    (local_id,),
                )
                order_row = cursor.fetchone()

                now_ts = datetime.now(UTC).timestamp()
                updates: Dict[str, Any] = {
                    "status": status,
                    "updated_at": now_ts,
                }

                if kraken_order_id is not None:
                    updates["kraken_order_id"] = kraken_order_id
                if cumulative_base_filled is not None:
                    updates["cumulative_base_filled"] = cumulative_base_filled
                if avg_fill_price is not None:
                    updates["avg_fill_price"] = avg_fill_price
                if last_error is not None:
                    updates["last_error"] = last_error
                if raw_response is not None:
                    updates["raw_response_json"] = json.dumps(raw_response, default=str)

                set_clause = ", ".join(f"{k} = ?" for k in updates)
                params = list(updates.values()) + [local_id]
                cursor.execute(
                    f"UPDATE execution_orders SET {set_clause} WHERE local_id = ?",
                    params,
                )

                cursor.execute(
                    """
                    INSERT INTO execution_order_events (
                        local_order_id, plan_id, event_time, status, message, raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        local_id,
                        order_row[0] if order_row else None,
                        now_ts,
                        status,
                        event_message or last_error,
                        (
                            json.dumps(raw_response, default=str)
                            if raw_response is not None
                            else None
                        ),
                    ),
                )

                conn.commit()
            finally:
                conn.close()

    def save_execution_result(self, result: "ExecutionResult"):
        with self._lock:
            conn = self._get_conn()
            try:
                cursor = conn.cursor()

                cursor.execute(
                    """
                    INSERT OR REPLACE INTO execution_results (
                        plan_id, started_at, completed_at, success, errors_json
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        result.plan_id,
                        result.started_at.timestamp() if result.started_at else None,
                        (
                            result.completed_at.timestamp()
                            if result.completed_at
                            else None
                        ),
                        1 if result.success else 0,
                        (
                            json.dumps(result.errors, default=str)
                            if result.errors
                            else json.dumps([])
                        ),
                    ),
                )

                conn.commit()
            finally:
                conn.close()

    def get_order_by_reference(
        self,
        kraken_order_id: Optional[str] = None,
        userref: Optional[int] = None,
    ) -> Optional["LocalOrder"]:
        with self._lock:
            conn = self._get_conn()
            try:
                cursor = conn.cursor()

                conditions = []
                params: List[Any] = []

                if kraken_order_id:
                    conditions.append("kraken_order_id = ?")
                    params.append(kraken_order_id)
                if userref is not None:
                    conditions.append("userref = ?")
                    params.append(userref)

                if not conditions:
                    return None

                where_clause = " OR ".join(conditions)
                cursor.execute(
                    f"""
                    SELECT
                        local_id, plan_id, strategy_id, pair, side, order_type, kraken_order_id, userref,
                        requested_base_size, requested_price, status, created_at, updated_at,
                        cumulative_base_filled, avg_fill_price, last_error, raw_request_json, raw_response_json
                    FROM execution_orders
                    WHERE {where_clause}
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """,
                    params,
                )

                row = cursor.fetchone()
            finally:
                conn.close()

        if not row:
            return None

        return self._row_to_local_order(row)

    def get_decisions(
        self,
        plan_id: Optional[str] = None,
        since: Optional[int] = None,
        limit: Optional[int] = None,
        strategy_name: Optional[str] = None,
        pair: Optional[str] = None,
    ) -> List["DecisionRecord"]:
        from kraken_bot.strategy.models import DecisionRecord

        with self._lock:
            conn = self._get_conn()
            try:
                cursor = conn.cursor()

                query = """
                    SELECT time, plan_id, strategy_name, pair, action_type, target_position_usd,
                           blocked, block_reason, kill_switch_active, raw_json
                    FROM decisions
                    WHERE 1=1
                """
                params: List[Any] = []

                if plan_id:
                    query += " AND plan_id = ?"
                    params.append(plan_id)

                if strategy_name:
                    query += " AND strategy_name = ?"
                    params.append(strategy_name)

                if pair:
                    query += " AND pair = ?"
                    params.append(pair)

                if since is not None:
                    query += " AND time >= ?"
                    params.append(since)

                query += " ORDER BY time DESC"

                if limit:
                    query += " LIMIT ?"
                    params.append(limit)

                cursor.execute(query, params)
                rows = cursor.fetchall()
            finally:
                conn.close()

        return [
            DecisionRecord(
                time=row[0],
                plan_id=row[1],
                strategy_name=row[2],
                pair=row[3],
                action_type=row[4],
                target_position_usd=row[5],
                blocked=bool(row[6]),
                block_reason=row[7],
                kill_switch_active=bool(row[8]),
                raw_json=row[9],
            )
            for row in rows
        ]

    def _deserialize_execution_plan_row(self, row: Tuple[Any, ...]) -> "ExecutionPlan":
        """Convert a row from execution_plans into an ExecutionPlan object."""
        from kraken_bot.strategy.models import ExecutionPlan, RiskAdjustedAction

        plan_id = row[0]
        generated_ts = row[1]
        plan_json = row[2]
        metadata_json = row[3]

        payload: Dict[str, Any] = {}
        if plan_json:
            try:
                payload = json.loads(plan_json)
            except json.JSONDecodeError:
                payload = {}

        generated_at_raw = payload.get("generated_at")
        if isinstance(generated_at_raw, (int, float)):
            generated_at = datetime.fromtimestamp(float(generated_at_raw), tz=UTC)
        elif isinstance(generated_at_raw, str):
            try:
                generated_at = datetime.fromisoformat(generated_at_raw)
            except ValueError:
                generated_at = datetime.fromtimestamp(float(generated_ts), tz=UTC)
        else:
            generated_at = datetime.fromtimestamp(float(generated_ts), tz=UTC)

        actions_data = payload.get("actions") or []
        actions: List[RiskAdjustedAction] = [
            RiskAdjustedAction(**action_dict) for action_dict in actions_data
        ]

        metadata = payload.get("metadata") or {}
        if not metadata and metadata_json:
            try:
                metadata = json.loads(metadata_json)
            except json.JSONDecodeError:
                metadata = {}

        return ExecutionPlan(
            plan_id=payload.get("plan_id", plan_id),
            generated_at=generated_at,
            actions=actions,
            metadata=metadata,
        )

    def _row_to_local_order(self, row: Tuple[Any, ...]) -> "LocalOrder":
        """Convert an execution_orders row into a LocalOrder instance."""
        from kraken_bot.execution.models import LocalOrder

        (
            local_id,
            plan_id,
            strategy_id,
            pair,
            side,
            order_type,
            kraken_order_id,
            userref,
            requested_base_size,
            requested_price,
            status,
            created_ts,
            updated_ts,
            cumulative_base_filled,
            avg_fill_price,
            last_error,
            raw_request_json,
            raw_response_json,
        ) = row

        created_at = (
            datetime.fromtimestamp(created_ts, tz=UTC)
            if created_ts is not None
            else datetime.now(tz=UTC)
        )
        updated_at = (
            datetime.fromtimestamp(updated_ts, tz=UTC)
            if updated_ts is not None
            else created_at
        )

        try:
            normalized_userref = int(userref) if userref is not None else None
        except (TypeError, ValueError):
            normalized_userref = None

        raw_request: Dict[str, Any] = {}
        raw_response: Optional[Dict[str, Any]] = None

        if raw_request_json:
            try:
                raw_request = json.loads(raw_request_json)
            except json.JSONDecodeError:
                raw_request = {}

        if raw_response_json:
            try:
                raw_response = json.loads(raw_response_json)
            except json.JSONDecodeError:
                raw_response = None

        return LocalOrder(
            local_id=local_id,
            plan_id=plan_id,
            strategy_id=strategy_id,
            pair=pair,
            side=side,
            order_type=order_type,
            kraken_order_id=kraken_order_id,
            userref=normalized_userref,
            requested_base_size=float(requested_base_size or 0.0),
            requested_price=(
                float(requested_price) if requested_price is not None else None
            ),
            status=status or "pending",
            created_at=created_at,
            updated_at=updated_at,
            cumulative_base_filled=float(cumulative_base_filled or 0.0),
            avg_fill_price=(
                float(avg_fill_price) if avg_fill_price is not None else None
            ),
            last_error=last_error,
            raw_request=raw_request,
            raw_response=raw_response,
        )

    def _deserialize_order_row(self, row: Tuple[Any, ...]) -> "LocalOrder":
        """Backwards-compatible wrapper to convert rows to LocalOrder."""

        return self._row_to_local_order(row)

    def _deserialize_execution_result_row(
        self, row: Tuple[Any, ...]
    ) -> "ExecutionResult":
        """Convert a row from execution_results into an ExecutionResult."""
        from kraken_bot.execution.models import ExecutionResult

        plan_id = row[0]
        started_ts = row[1]
        completed_ts = row[2]
        success_int = row[3]
        errors_json = row[4] if len(row) > 4 else None
        warnings_json = row[5] if len(row) > 5 else None

        started_at = (
            datetime.fromtimestamp(started_ts, tz=UTC)
            if started_ts is not None
            else datetime.fromtimestamp(0, tz=UTC)
        )
        completed_at = (
            datetime.fromtimestamp(completed_ts, tz=UTC)
            if completed_ts is not None
            else None
        )

        def _loads_list(blob: Any) -> List[str]:
            if not blob:
                return []
            try:
                parsed = json.loads(blob)
                if isinstance(parsed, list):
                    return [str(item) for item in parsed]
            except json.JSONDecodeError:
                return []
            return []

        errors = _loads_list(errors_json)
        warnings = _loads_list(warnings_json)

        return ExecutionResult(
            plan_id=plan_id,
            started_at=started_at,
            completed_at=completed_at,
            success=bool(success_int),
            orders=[],
            errors=errors,
            warnings=warnings,
        )

    def get_execution_plans(
        self,
        plan_id: Optional[str] = None,
        since: Optional[Union[int, float, datetime]] = None,
        limit: Optional[int] = None,
    ) -> List["ExecutionPlan"]:
        with self._lock:
            conn = self._get_conn()
            try:
                cursor = conn.cursor()

                query = """
                    SELECT plan_id, generated_at, plan_json, metadata_json
                    FROM execution_plans
                    WHERE 1=1
                """
                params: List[Any] = []

                if plan_id is not None:
                    query += " AND plan_id = ?"
                    params.append(plan_id)

                if since is not None:
                    cutoff = (
                        since.timestamp() if isinstance(since, datetime) else float(since)
                    )
                    query += " AND generated_at >= ?"
                    params.append(cutoff)

                query += " ORDER BY generated_at DESC"

                if limit is not None:
                    query += " LIMIT ?"
                    params.append(limit)

                cursor.execute(query, params)
                rows = cursor.fetchall()
            finally:
                conn.close()

        return [self._deserialize_execution_plan_row(row) for row in rows]

    def get_execution_plan(self, plan_id: str) -> Optional["ExecutionPlan"]:
        with self._lock:
            conn = self._get_conn()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT plan_id, generated_at, plan_json, metadata_json
                    FROM execution_plans
                    WHERE plan_id = ?
                    """,
                    (plan_id,),
                )
                row = cursor.fetchone()
            finally:
                conn.close()

        if row is None:
            return None

        return self._deserialize_execution_plan_row(row)

    def get_open_orders(
        self,
        plan_id: Optional[str] = None,
        strategy_id: Optional[str] = None,
    ) -> List["LocalOrder"]:
        open_statuses = {
            "pending",
            "submitted",
            "open",
            "partially_filled",
            "validated",
            "pending_cancel",
            "pending_cancellation",
            "canceling",
        }

        with self._lock:
            conn = self._get_conn()
            try:
                cursor = conn.cursor()

                query = """
                    SELECT
                        local_id,
                        plan_id,
                        strategy_id,
                        pair,
                        side,
                        order_type,
                        kraken_order_id,
                        userref,
                        requested_base_size,
                        requested_price,
                        status,
                        created_at,
                        updated_at,
                        cumulative_base_filled,
                        avg_fill_price,
                        last_error,
                        raw_request_json,
                        raw_response_json
                    FROM execution_orders
                    WHERE status IS NULL OR status IN ({statuses})
                """.format(
                    statuses=", ".join("?" for _ in open_statuses)
                )

                params: List[Any] = list(open_statuses)

                if plan_id is not None:
                    query += " AND plan_id = ?"
                    params.append(plan_id)

                if strategy_id is not None:
                    query += " AND strategy_id = ?"
                    params.append(strategy_id)

                query += " ORDER BY created_at DESC"

                cursor.execute(query, params)
                rows = cursor.fetchall()
            finally:
                conn.close()

        return [self._row_to_local_order(row) for row in rows]

    def get_execution_results(self, limit: int = 10) -> List["ExecutionResult"]:
        with self._lock:
            conn = self._get_conn()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT
                        plan_id,
                        started_at,
                        completed_at,
                        success,
                        errors_json
                    FROM execution_results
                    ORDER BY COALESCE(completed_at, started_at) DESC, started_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                )
                rows = cursor.fetchall()
            finally:
                conn.close()
        return [self._deserialize_execution_result_row(row) for row in rows]

    # --- ML persistence -------------------------------------------------

    def record_ml_example(
        self,
        strategy_id: str,
        model_key: str,
        *,
        created_at: datetime,
        source_mode: str,
        label_type: str,
        features: Sequence[float],
        label: float,
        sample_weight: float = 1.0,
    ) -> None:
        """Record a single ML training example in the SQLite backend."""

        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)

        created_at_iso = created_at.astimezone(UTC).isoformat()

        features_json = json.dumps(list(features))

        with self._lock:
            conn = self._get_conn()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO ml_training_examples (
                        strategy_id,
                        model_key,
                        created_at,
                        source_mode,
                        label_type,
                        features,
                        label,
                        sample_weight
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        strategy_id,
                        model_key,
                        created_at_iso,
                        str(source_mode),
                        str(label_type),
                        features_json,
                        float(label),
                        float(sample_weight),
                    ),
                )

                cursor.execute(
                    """
                    SELECT COUNT(*)
                    FROM ml_training_examples
                    WHERE strategy_id = ? AND model_key = ?
                    """,
                    (strategy_id, model_key),
                )
                (count,) = cursor.fetchone()

                excess = max((count or 0) - MAX_ML_TRAINING_EXAMPLES, 0)

                if excess > 0:
                    cursor.execute(
                        """
                        DELETE FROM ml_training_examples
                        WHERE id IN (
                            SELECT id
                            FROM ml_training_examples
                            WHERE strategy_id = ?
                              AND model_key   = ?
                            ORDER BY created_at ASC, id ASC
                            LIMIT ?
                        )
                        """,
                        (strategy_id, model_key, excess),
                    )

                conn.commit()
            finally:
                conn.close()

    def load_ml_training_window(
        self,
        strategy_id: str,
        model_key: str,
        *,
        max_examples: int = MAX_ML_TRAINING_EXAMPLES,
        return_weights: bool = False,
    ) -> Tuple[List[List[float]], List[float]] | Tuple[List[List[float]], List[float], List[float]]:
        """Load a rolling window of ML training examples for a model key.

        Returns features and labels in chronological order (oldest → newest).
        """

        if max_examples <= 0:
            return [], []

        with self._lock:
            conn = self._get_conn()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT features, label, sample_weight
                    FROM ml_training_examples
                    WHERE strategy_id = ?
                      AND model_key   = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (strategy_id, model_key, max_examples),
                )
                rows = cursor.fetchall()
            finally:
                conn.close()

        X: List[List[float]] = []
        y: List[float] = []
        weights: List[float] = []

        for features_json, label, sample_weight in rows:
            X.append(json.loads(features_json))
            y.append(float(label))
            weights.append(float(sample_weight))

        X.reverse()
        y.reverse()
        weights.reverse()

        if return_weights:
            return X, y, weights
        return X, y

    def save_ml_model(
        self,
        strategy_id: str,
        model_key: str,
        *,
        label_type: str,
        framework: str,
        model: object,
        version: int = 1,
    ) -> None:
        """Persist a serialized ML model for a given strategy/model key."""

        blob = pickle.dumps(model, protocol=pickle.HIGHEST_PROTOCOL)
        updated_at = _utc_now_iso()

        with self._lock:
            conn = self._get_conn()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO ml_models (
                        strategy_id,
                        model_key,
                        label_type,
                        framework,
                        version,
                        updated_at,
                        model_blob
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(strategy_id, model_key) DO UPDATE SET
                        label_type = excluded.label_type,
                        framework  = excluded.framework,
                        version    = excluded.version,
                        updated_at = excluded.updated_at,
                        model_blob = excluded.model_blob
                    """,
                    (
                        strategy_id,
                        model_key,
                        str(label_type),
                        str(framework),
                        int(version),
                        updated_at,
                        blob,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def load_ml_model(self, strategy_id: str, model_key: str) -> Optional[object]:
        """Load a persisted ML model if present, otherwise return None."""

        with self._lock:
            conn = self._get_conn()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT model_blob
                    FROM ml_models
                    WHERE strategy_id = ?
                      AND model_key   = ?
                    """,
                    (strategy_id, model_key),
                )
                row = cursor.fetchone()
            finally:
                conn.close()

        if not row or row[0] is None:
            return None

        try:
            return pickle.loads(row[0])
        except Exception:
            # If the pickled model is corrupt or incompatible, we fail soft:
            # callers go on to bootstrap from raw examples instead.
            return None
