# src/kraken_bot/portfolio/store.py

import abc
import sqlite3
import json
import logging
from dataclasses import asdict
from datetime import datetime
from typing import List, Optional, Dict, Any, TYPE_CHECKING
from pathlib import Path
from .models import RealizedPnLRecord, CashFlowRecord, PortfolioSnapshot, AssetValuation

if TYPE_CHECKING:
    from kraken_bot.execution.models import ExecutionResult, LocalOrder
    from kraken_bot.strategy.models import DecisionRecord, ExecutionPlan, RiskAdjustedAction
    from kraken_bot.execution.models import LocalOrder, ExecutionResult

logger = logging.getLogger(__name__)

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
    def get_snapshots(self, since: Optional[int] = None, limit: Optional[int] = None) -> List[PortfolioSnapshot]:
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
        self, plan_id: Optional[str] = None, since: Optional[int] = None, limit: Optional[int] = None, strategy_name: Optional[str] = None
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
        self, plan_id: Optional[str] = None, since: Optional[int] = None, limit: Optional[int] = None
    ) -> List["ExecutionPlan"]:
        """Fetch stored execution plans."""
        pass

    @abc.abstractmethod
    def get_execution_plan(self, plan_id: str) -> Optional["ExecutionPlan"]:
        """Fetch a specific execution plan by id."""
        pass

    @abc.abstractmethod
    def save_local_order(self, order: "LocalOrder"):
        """Persist or update a LocalOrder snapshot."""
        pass

    @abc.abstractmethod
    def update_local_order(self, order: "LocalOrder"):
        """Alias for saving a LocalOrder update."""
        pass

    @abc.abstractmethod
    def save_execution_result(self, result: "ExecutionResult"):
        """Persist an execution result and its associated orders."""
        pass

    @abc.abstractmethod
    def get_open_orders(self) -> List["LocalOrder"]:
        """Fetch currently open or pending LocalOrders."""
        pass

    @abc.abstractmethod
    def get_recent_executions(self, limit: int = 10) -> List["ExecutionResult"]:
        """Fetch recent execution results ordered by start time."""
        pass


class SQLitePortfolioStore(PortfolioStore):
    def __init__(self, db_path: str = "portfolio.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Schema Version
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS schema_version (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                version INTEGER NOT NULL
            )
        """)

        cursor.execute("SELECT version FROM schema_version WHERE id = 1")
        row = cursor.fetchone()

        # Check current version and upgrade if necessary
        current_version = 0
        if row is not None:
            current_version = row[0]
        else:
            # New DB
            cursor.execute("INSERT INTO schema_version (id, version) VALUES (1, 5)")
            current_version = 5

        # Trades Table
        cursor.execute("""
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
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_time ON trades(time)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_pair ON trades(pair)")

        # Cash Flows Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS cash_flows (
                id TEXT PRIMARY KEY,
                time REAL,
                asset TEXT,
                amount REAL,
                type TEXT,
                note TEXT
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_cash_flows_time ON cash_flows(time)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_cash_flows_asset ON cash_flows(asset)")

        # Snapshots Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS snapshots (
                timestamp REAL PRIMARY KEY,
                equity_base REAL,
                cash_base REAL,
                data_json TEXT
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_timestamp ON snapshots(timestamp)")

        # Upgrade for V4: Decisions Table
        if current_version < 4:
            cursor.execute("""
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
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_decisions_time ON decisions(time)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_decisions_plan_id ON decisions(plan_id)")

            current_version = 4
            cursor.execute("UPDATE schema_version SET version = 4 WHERE id = 1")
            current_version = 4
        else:
            # Ensure table exists even if version is current (for fresh installs)
            cursor.execute("""
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
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_decisions_time ON decisions(time)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_decisions_plan_id ON decisions(plan_id)")

        # Upgrade for V5: Execution order/result tables
        if current_version < 5:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS execution_orders (
                    local_id TEXT PRIMARY KEY,
                    plan_id TEXT,
                    strategy_id TEXT,
                    pair TEXT NOT NULL,
                    side TEXT NOT NULL,
                    order_type TEXT NOT NULL,
                    kraken_order_id TEXT,
                    userref INTEGER,
                    requested_base_size REAL NOT NULL,
                    requested_price REAL,
                    status TEXT NOT NULL,
                    cumulative_base_filled REAL NOT NULL DEFAULT 0.0,
                    avg_fill_price REAL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    last_error TEXT,
                    raw_request TEXT,
                    raw_response TEXT
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS execution_results (
                    plan_id TEXT PRIMARY KEY,
                    started_at INTEGER NOT NULL,
                    completed_at INTEGER,
                    success INTEGER NOT NULL,
                    error_summary TEXT,
                    raw_json TEXT
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_execution_orders_plan_id ON execution_orders(plan_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_execution_orders_status ON execution_orders(status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_execution_orders_updated_at ON execution_orders(updated_at)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_execution_results_started_at ON execution_results(started_at)")

            current_version = 5
            cursor.execute("UPDATE schema_version SET version = 5 WHERE id = 1")
        else:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS execution_orders (
                    local_id TEXT PRIMARY KEY,
                    plan_id TEXT,
                    strategy_id TEXT,
                    pair TEXT NOT NULL,
                    side TEXT NOT NULL,
                    order_type TEXT NOT NULL,
                    kraken_order_id TEXT,
                    userref INTEGER,
                    requested_base_size REAL NOT NULL,
                    requested_price REAL,
                    status TEXT NOT NULL,
                    cumulative_base_filled REAL NOT NULL DEFAULT 0.0,
                    avg_fill_price REAL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    last_error TEXT,
                    raw_request TEXT,
                    raw_response TEXT
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS execution_results (
                    plan_id TEXT PRIMARY KEY,
                    started_at INTEGER NOT NULL,
                    completed_at INTEGER,
                    success INTEGER NOT NULL,
                    error_summary TEXT,
                    raw_json TEXT
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_execution_orders_plan_id ON execution_orders(plan_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_execution_orders_status ON execution_orders(status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_execution_orders_updated_at ON execution_orders(updated_at)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_execution_results_started_at ON execution_results(started_at)")

        # Execution Plans Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS execution_plans (
                plan_id TEXT PRIMARY KEY,
                generated_at REAL NOT NULL,
                action_count INTEGER NOT NULL,
                blocked_actions INTEGER NOT NULL,
                metadata_json TEXT,
                plan_json TEXT
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_execution_plans_generated_at ON execution_plans(generated_at)")

        # Upgrade for V5: Execution orders and results
        if current_version < 5:
            cursor.execute("""
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
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_execution_orders_plan_id ON execution_orders(plan_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_execution_orders_kraken_id ON execution_orders(kraken_order_id)")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS execution_order_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    local_order_id TEXT NOT NULL,
                    plan_id TEXT,
                    event_time REAL NOT NULL,
                    status TEXT,
                    message TEXT,
                    raw_json TEXT
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_execution_order_events_order ON execution_order_events(local_order_id)")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS execution_results (
                    plan_id TEXT PRIMARY KEY,
                    started_at REAL,
                    completed_at REAL,
                    success INTEGER,
                    errors_json TEXT
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_execution_results_started_at ON execution_results(started_at)")

            cursor.execute("UPDATE schema_version SET version = 5 WHERE id = 1")
            current_version = 5
        else:
            cursor.execute("""
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
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_execution_orders_plan_id ON execution_orders(plan_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_execution_orders_kraken_id ON execution_orders(kraken_order_id)")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS execution_order_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    local_order_id TEXT NOT NULL,
                    plan_id TEXT,
                    event_time REAL NOT NULL,
                    status TEXT,
                    message TEXT,
                    raw_json TEXT
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_execution_order_events_order ON execution_order_events(local_order_id)")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS execution_results (
                    plan_id TEXT PRIMARY KEY,
                    started_at REAL,
                    completed_at REAL,
                    success INTEGER,
                    errors_json TEXT
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_execution_results_started_at ON execution_results(started_at)")

        conn.commit()
        conn.close()

    def _get_conn(self):
        return sqlite3.connect(self.db_path)

    @staticmethod
    def _ts(dt: Optional[datetime]) -> Optional[int]:
        return int(dt.timestamp()) if dt else None

    @staticmethod
    def _local_order_from_row(row) -> "LocalOrder":
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
            cumulative_base_filled,
            avg_fill_price,
            created_at,
            updated_at,
            last_error,
            raw_request,
            raw_response,
        ) = row

        return LocalOrder(
            local_id=local_id,
            plan_id=plan_id,
            strategy_id=strategy_id,
            pair=pair,
            side=side,
            order_type=order_type,
            kraken_order_id=kraken_order_id,
            userref=userref,
            requested_base_size=requested_base_size,
            requested_price=requested_price,
            status=status,
            cumulative_base_filled=cumulative_base_filled,
            avg_fill_price=avg_fill_price,
            created_at=datetime.fromtimestamp(created_at),
            updated_at=datetime.fromtimestamp(updated_at),
            last_error=last_error,
            raw_request=json.loads(raw_request) if raw_request else {},
            raw_response=json.loads(raw_response) if raw_response else None,
        )

    @staticmethod
    def _serialize_order(order: "LocalOrder") -> Dict[str, Any]:
        return {
            "local_id": order.local_id,
            "plan_id": order.plan_id,
            "strategy_id": order.strategy_id,
            "pair": order.pair,
            "side": order.side,
            "order_type": order.order_type,
            "kraken_order_id": order.kraken_order_id,
            "userref": order.userref,
            "requested_base_size": order.requested_base_size,
            "requested_price": order.requested_price,
            "status": order.status,
            "cumulative_base_filled": order.cumulative_base_filled,
            "avg_fill_price": order.avg_fill_price,
            "created_at": order.created_at.isoformat(),
            "updated_at": order.updated_at.isoformat(),
            "last_error": order.last_error,
            "raw_request": order.raw_request,
            "raw_response": order.raw_response,
        }

    def save_trades(self, trades: List[Dict[str, Any]]):
        if not trades:
            return

        conn = self._get_conn()
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

            cursor.execute("""
                INSERT OR IGNORE INTO trades (
                    id, ordertxid, pair, time, type, ordertype, price, cost, fee, vol,
                    margin, misc, posstatus, cprice, ccost, cfee, cvol, cmargin, net,
                    trades_csv, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
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
                float(trade.get("cprice", 0)) if trade.get("cprice") else None,
                float(trade.get("ccost", 0)) if trade.get("ccost") else None,
                float(trade.get("cfee", 0)) if trade.get("cfee") else None,
                float(trade.get("cvol", 0)) if trade.get("cvol") else None,
                float(trade.get("cmargin", 0)) if trade.get("cmargin") else None,
                float(trade.get("net", 0)) if trade.get("net") else None,
                trades_csv, # trades list CSV
                raw_json
            ))
        conn.commit()
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

        conn = self._get_conn()
        cursor = conn.cursor()

        for record in records:
            cursor.execute("""
                INSERT OR IGNORE INTO cash_flows (
                    id, time, asset, amount, type, note
                ) VALUES (?, ?, ?, ?, ?, ?)
            """, (
                record.id,
                record.time,
                record.asset,
                record.amount,
                record.type,
                record.note
            ))
        conn.commit()
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
                note=row[5]
            ) for row in rows
        ]

    def save_snapshot(self, snapshot: PortfolioSnapshot):
        conn = self._get_conn()
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
            "unrealized_pnl_base_by_pair": snapshot.unrealized_pnl_base_by_pair
        }

        cursor.execute("""
            INSERT OR REPLACE INTO snapshots (
                timestamp, equity_base, cash_base, data_json
            ) VALUES (?, ?, ?, ?)
        """, (
            snapshot.timestamp,
            snapshot.equity_base,
            snapshot.cash_base,
            json.dumps(data)
        ))
        conn.commit()
        conn.close()

    def get_snapshots(self, since: Optional[int] = None, limit: Optional[int] = None) -> List[PortfolioSnapshot]:
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
            snapshots.append(PortfolioSnapshot(
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
                unrealized_pnl_base_by_pair=data["unrealized_pnl_base_by_pair"]
            ))
        return snapshots

    def prune_snapshots(self, older_than_ts: int):
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM snapshots WHERE timestamp < ?", (older_than_ts,))
        conn.commit()
        conn.close()

    def add_decision(self, record: "DecisionRecord"):
        from kraken_bot.strategy.models import DecisionRecord

        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO decisions (
                time, plan_id, strategy_name, pair, action_type,
                target_position_usd, blocked, block_reason, kill_switch_active, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            record.time,
            record.plan_id,
            record.strategy_name,
            record.pair,
            record.action_type,
            record.target_position_usd,
            1 if record.blocked else 0,
            record.block_reason,
            1 if record.kill_switch_active else 0,
            record.raw_json
        ))

        conn.commit()
        conn.close()

    def save_execution_plan(self, plan: "ExecutionPlan"):
        from kraken_bot.strategy.models import ExecutionPlan

        conn = self._get_conn()
        cursor = conn.cursor()

        plan_json = json.dumps({
            "plan_id": plan.plan_id,
            "generated_at": plan.generated_at,
            "actions": [asdict(a) for a in plan.actions],
            "metadata": plan.metadata,
        }, default=str)

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
        conn.close()

    def save_order(self, order: "LocalOrder"):
        from kraken_bot.execution.models import LocalOrder

        conn = self._get_conn()
        cursor = conn.cursor()

        created_ts = order.created_at.timestamp() if isinstance(order.created_at, datetime) else None
        updated_ts = order.updated_at.timestamp() if isinstance(order.updated_at, datetime) else None

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
                json.dumps(order.raw_request, default=str) if order.raw_request else None,
                json.dumps(order.raw_response, default=str) if order.raw_response else None,
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
                updated_ts or created_ts or datetime.utcnow().timestamp(),
                order.status,
                order.last_error,
                json.dumps(order.raw_response, default=str) if order.raw_response else None,
            ),
        )

        conn.commit()
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
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("SELECT plan_id FROM execution_orders WHERE local_id = ?", (local_id,))
        order_row = cursor.fetchone()

        now_ts = datetime.utcnow().timestamp()
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
        cursor.execute(f"UPDATE execution_orders SET {set_clause} WHERE local_id = ?", params)

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
                json.dumps(raw_response, default=str) if raw_response is not None else None,
            ),
        )

        conn.commit()
        conn.close()

    def save_execution_result(self, result: "ExecutionResult"):
        from kraken_bot.execution.models import ExecutionResult

        conn = self._get_conn()
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
                result.completed_at.timestamp() if result.completed_at else None,
                1 if result.success else 0,
                json.dumps(result.errors, default=str) if result.errors else json.dumps([]),
            ),
        )

        conn.commit()
        conn.close()

    def get_order_by_reference(
        self,
        kraken_order_id: Optional[str] = None,
        userref: Optional[int] = None,
    ) -> Optional["LocalOrder"]:
        from kraken_bot.execution.models import LocalOrder

        if not kraken_order_id and userref is None:
            return None

        conn = self._get_conn()
        cursor = conn.cursor()

        conditions = []
        params: List[Any] = []

        if kraken_order_id:
            conditions.append("kraken_order_id = ?")
            params.append(kraken_order_id)
        if userref is not None:
            conditions.append("userref = ?")
            params.append(userref)

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
        conn.close()

        if not row:
            return None

        created_at = datetime.fromtimestamp(row[11]) if row[11] else datetime.utcnow()
        updated_at = datetime.fromtimestamp(row[12]) if row[12] else created_at

        raw_request = json.loads(row[16]) if row[16] else {}
        raw_response = json.loads(row[17]) if row[17] else None

        return LocalOrder(
            local_id=row[0],
            plan_id=row[1],
            strategy_id=row[2],
            pair=row[3],
            side=row[4],
            order_type=row[5],
            kraken_order_id=row[6],
            userref=row[7],
            requested_base_size=row[8] or 0.0,
            requested_price=row[9],
            status=row[10] or "pending",
            created_at=created_at,
            updated_at=updated_at,
            cumulative_base_filled=row[13] or 0.0,
            avg_fill_price=row[14],
            last_error=row[15],
            raw_request=raw_request,
            raw_response=raw_response,
        )

    def get_decisions(
        self, plan_id: Optional[str] = None, since: Optional[int] = None, limit: Optional[int] = None, strategy_name: Optional[str] = None
    ) -> List["DecisionRecord"]:
        from kraken_bot.strategy.models import DecisionRecord

        conn = self._get_conn()
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

        if since is not None:
            query += " AND time >= ?"
            params.append(since)

        query += " ORDER BY time DESC"

        if limit:
            query += " LIMIT ?"
            params.append(limit)

        cursor.execute(query, params)
        rows = cursor.fetchall()
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

    def _row_to_execution_plan(self, row) -> "ExecutionPlan":
        from kraken_bot.strategy.models import ExecutionPlan, RiskAdjustedAction

        plan_id, generated_at_ts, _, _, metadata_json, plan_json = row
        plan_data = json.loads(plan_json) if plan_json else {}
        actions_payload = plan_data.get("actions", [])
        actions = [RiskAdjustedAction(**action) for action in actions_payload]

        generated_at = (
            datetime.fromtimestamp(generated_at_ts) if isinstance(generated_at_ts, (int, float)) else datetime.fromisoformat(str(generated_at_ts))
        )
        metadata = json.loads(metadata_json) if metadata_json else {}

        return ExecutionPlan(
            plan_id=plan_id,
            generated_at=generated_at,
            actions=actions,
            metadata=metadata,
        )

    def _execution_result_from_row(self, row, orders: List["LocalOrder"]) -> "ExecutionResult":
        from kraken_bot.execution.models import ExecutionResult

        plan_id, started_at, completed_at, success, error_summary, raw_json = row
        errors: List[str] = []
        if raw_json:
            try:
                payload = json.loads(raw_json)
                errors = payload.get("errors", []) or errors
            except json.JSONDecodeError:
                logger.warning("Failed to decode execution result raw_json for plan %s", plan_id)
        if not errors and error_summary:
            errors = [error_summary]

        return ExecutionResult(
            plan_id=plan_id,
            started_at=datetime.fromtimestamp(started_at),
            completed_at=datetime.fromtimestamp(completed_at) if completed_at is not None else None,
            success=bool(success),
            orders=orders,
            errors=errors,
        )

    def get_execution_plans(
        self, plan_id: Optional[str] = None, since: Optional[int] = None, limit: Optional[int] = None
    ) -> List["ExecutionPlan"]:
        conn = self._get_conn()
        cursor = conn.cursor()

        query = "SELECT plan_id, generated_at, action_count, blocked_actions, metadata_json, plan_json FROM execution_plans WHERE 1=1"
        params: List[Any] = []

        if plan_id:
            query += " AND plan_id = ?"
            params.append(plan_id)

        if since is not None:
            query += " AND generated_at >= ?"
            params.append(since)

        query += " ORDER BY generated_at DESC"

        if limit:
            query += " LIMIT ?"
            params.append(limit)

        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()

        return [self._row_to_execution_plan(row) for row in rows]

    def get_execution_plan(self, plan_id: str) -> Optional["ExecutionPlan"]:
        plans = self.get_execution_plans(plan_id=plan_id, limit=1)
        return plans[0] if plans else None

    def save_local_order(self, order: "LocalOrder"):
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT OR REPLACE INTO execution_orders (
                local_id, plan_id, strategy_id, pair, side, order_type, kraken_order_id, userref,
                requested_base_size, requested_price, status, cumulative_base_filled, avg_fill_price,
                created_at, updated_at, last_error, raw_request, raw_response
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
                order.cumulative_base_filled,
                order.avg_fill_price,
                self._ts(order.created_at),
                self._ts(order.updated_at),
                order.last_error,
                json.dumps(order.raw_request, default=str) if order.raw_request is not None else None,
                json.dumps(order.raw_response, default=str) if order.raw_response is not None else None,
            ),
        )

        conn.commit()
        conn.close()

    def update_local_order(self, order: "LocalOrder"):
        # For now, treat updates as an upsert to keep the latest snapshot.
        self.save_local_order(order)

    def save_execution_result(self, result: "ExecutionResult"):
        serialized_orders = [self._serialize_order(o) for o in result.orders]
        raw_json = json.dumps(
            {
                "plan_id": result.plan_id,
                "started_at": result.started_at.isoformat(),
                "completed_at": result.completed_at.isoformat() if result.completed_at else None,
                "success": result.success,
                "orders": serialized_orders,
                "errors": result.errors,
            },
            default=str,
        )

        for order in result.orders:
            self.save_local_order(order)

        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT OR REPLACE INTO execution_results (
                plan_id, started_at, completed_at, success, error_summary, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                result.plan_id,
                self._ts(result.started_at),
                self._ts(result.completed_at),
                1 if result.success else 0,
                "; ".join(result.errors) if result.errors else None,
                raw_json,
            ),
        )

        conn.commit()
        conn.close()

    def get_open_orders(self) -> List["LocalOrder"]:
        final_statuses = ["filled", "canceled", "rejected", "error"]
        placeholders = ",".join(["?"] * len(final_statuses))

        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute(
            f"SELECT local_id, plan_id, strategy_id, pair, side, order_type, kraken_order_id, userref, "
            f"requested_base_size, requested_price, status, cumulative_base_filled, avg_fill_price, "
            f"created_at, updated_at, last_error, raw_request, raw_response "
            f"FROM execution_orders WHERE status NOT IN ({placeholders}) "
            f"ORDER BY updated_at DESC",
            final_statuses,
        )

        rows = cursor.fetchall()
        conn.close()

        return [self._local_order_from_row(row) for row in rows]

    def get_recent_executions(self, limit: int = 10) -> List["ExecutionResult"]:
        if limit <= 0:
            return []

        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute(
            "SELECT plan_id, started_at, completed_at, success, error_summary, raw_json "
            "FROM execution_results ORDER BY started_at DESC LIMIT ?",
            (limit,),
        )
        result_rows = cursor.fetchall()

        executions: List["ExecutionResult"] = []
        for row in result_rows:
            plan_id = row[0]
            cursor.execute(
                "SELECT local_id, plan_id, strategy_id, pair, side, order_type, kraken_order_id, userref, "
                "requested_base_size, requested_price, status, cumulative_base_filled, avg_fill_price, "
                "created_at, updated_at, last_error, raw_request, raw_response "
                "FROM execution_orders WHERE plan_id = ? ORDER BY created_at ASC",
                (plan_id,),
            )
            order_rows = cursor.fetchall()
            orders = [self._local_order_from_row(o_row) for o_row in order_rows]
            executions.append(self._execution_result_from_row(row, orders))

        conn.close()
        return executions
