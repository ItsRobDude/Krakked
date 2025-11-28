# src/kraken_bot/portfolio/store.py

import abc
import sqlite3
import json
import logging
from dataclasses import asdict
from typing import List, Optional, Dict, Any
from pathlib import Path
from .models import RealizedPnLRecord, CashFlowRecord, PortfolioSnapshot, AssetValuation
from kraken_bot.strategy.models import DecisionRecord, ExecutionPlan

logger = logging.getLogger(__name__)

class PortfolioStore(abc.ABC):
    @abc.abstractmethod
    def save_trades(self, trades: List[Dict[str, Any]]):
        """Saves raw trade data."""
        pass

    @abc.abstractmethod
    def get_trades(self, pair: Optional[str] = None, limit: Optional[int] = None, since: Optional[int] = None) -> List[Dict[str, Any]]:
        """Retrieves raw trade data."""
        pass

    @abc.abstractmethod
    def save_cash_flows(self, records: List[CashFlowRecord]):
        """Saves cash flow records."""
        pass

    @abc.abstractmethod
    def get_cash_flows(self, asset: Optional[str] = None, limit: Optional[int] = None, since: Optional[int] = None) -> List[CashFlowRecord]:
        """Retrieves cash flow records."""
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
    def add_decision(self, record: DecisionRecord):
        """Saves a strategy decision record."""
        pass

    @abc.abstractmethod
    def save_execution_plan(self, plan: ExecutionPlan):
        """Persist an execution plan for downstream consumption."""
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
            cursor.execute("INSERT INTO schema_version (id, version) VALUES (1, 4)")
            current_version = 4

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

            # Update version
            cursor.execute("UPDATE schema_version SET version = 4 WHERE id = 1")
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

        conn.commit()
        conn.close()

    def _get_conn(self):
        return sqlite3.connect(self.db_path)

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

    def get_trades(self, pair: Optional[str] = None, limit: Optional[int] = None, since: Optional[int] = None) -> List[Dict[str, Any]]:
        conn = self._get_conn()
        cursor = conn.cursor()

        query = "SELECT raw_json FROM trades WHERE 1=1"
        params = []

        if pair:
            query += " AND pair = ?"
            params.append(pair)

        if since:
            query += " AND time >= ?"
            params.append(since)

        query += " ORDER BY time DESC" # Default newest first? Or oldest? Spec says "get_trades", usually for history.
        # But 'sync' might want newest first to find last.
        # Actually standard for history is often reverse chronological.

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

    def get_cash_flows(self, asset: Optional[str] = None, limit: Optional[int] = None, since: Optional[int] = None) -> List[CashFlowRecord]:
        conn = self._get_conn()
        cursor = conn.cursor()

        query = "SELECT id, time, asset, amount, type, note FROM cash_flows WHERE 1=1"
        params = []

        if asset:
            query += " AND asset = ?"
            params.append(asset)

        if since:
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

    def add_decision(self, record: DecisionRecord):
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

    def save_execution_plan(self, plan: ExecutionPlan):
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
