# src/kraken_bot/portfolio/store.py

import abc
import sqlite3
import json
import logging
from typing import List, Optional, Dict, Any
from pathlib import Path
from .models import RealizedPnLRecord, CashFlowRecord, PortfolioSnapshot, AssetValuation

logger = logging.getLogger(__name__)

class PortfolioStore(abc.ABC):
    @abc.abstractmethod
    def save_trades(self, trades: List[Dict[str, Any]]):
        """Saves raw trade data."""
        pass

    @abc.abstractmethod
    def save_orders(self, orders: List[Dict[str, Any]]):
        """Saves raw order data and trade mappings."""
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


class SQLitePortfolioStore(PortfolioStore):
    def __init__(self, db_path: str = "portfolio.db"):
        self.db_path = db_path
        self._init_db()

    def _get_conn(self):
        return sqlite3.connect(self.db_path)

    def _init_db(self):
        conn = self._get_conn()
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
        current_version = 0
        if row is None:
             cursor.execute("INSERT INTO schema_version (id, version) VALUES (1, 2)")
             current_version = 0 # New DB, but we'll run create table logic which is idempotent
        else:
             current_version = row[0]

        # Schema V1 definition (Conceptual): 'trades' had 'trades_csv' column.
        # Schema V2 definition: 'trades' has NO 'trades_csv'. New 'orders' and 'order_trades'.

        if current_version < 2:
            self._migrate_to_v2(conn, cursor, current_version)
            # Update version
            cursor.execute("UPDATE schema_version SET version = 2 WHERE id = 1")

        conn.commit()
        conn.close()

    def _migrate_to_v2(self, conn, cursor, current_version):
        logger.info(f"Migrating database from version {current_version} to 2...")

        # 1. Create new normalized tables
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                order_id TEXT PRIMARY KEY,
                pair TEXT,
                status TEXT,
                opened REAL,
                closed REAL,
                raw_json TEXT NOT NULL
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS order_trades (
                order_id TEXT NOT NULL,
                trade_id TEXT NOT NULL,
                PRIMARY KEY (order_id, trade_id),
                FOREIGN KEY (order_id) REFERENCES orders(order_id),
                FOREIGN KEY (trade_id) REFERENCES trades(id)
            )
        """)

        # 2. Update 'trades' table.
        # If 'trades' exists and has 'trades_csv', we should migrate.
        # Check if table exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='trades'")
        if cursor.fetchone():
            # Table exists. Check columns.
            cursor.execute("PRAGMA table_info(trades)")
            columns = [info[1] for info in cursor.fetchall()]

            if "trades_csv" in columns:
                # Need to migrate
                logger.info("Migrating 'trades' table to remove 'trades_csv'...")

                # Rename old
                cursor.execute("ALTER TABLE trades RENAME TO trades_legacy")

                # Create new
                self._create_trades_table_v2(cursor)

                # Copy data
                # All columns except trades_csv
                # We need to list common columns explicitly to be safe
                common_cols = [
                    "id", "ordertxid", "pair", "time", "type", "ordertype", "price",
                    "cost", "fee", "vol", "margin", "misc", "posstatus", "cprice",
                    "ccost", "cfee", "cvol", "cmargin", "net", "raw_json"
                ]
                cols_str = ", ".join(common_cols)
                cursor.execute(f"INSERT INTO trades ({cols_str}) SELECT {cols_str} FROM trades_legacy")

                # Drop old
                cursor.execute("DROP TABLE trades_legacy")
            else:
                # Table exists but 'trades_csv' missing? Maybe already V2 format but version wasn't bumped?
                # Or a clean run. Ensure schema matches V2.
                pass
        else:
            # Table doesn't exist, create it (V2)
            self._create_trades_table_v2(cursor)

        # 3. Create other tables if not exist (cash_flows, snapshots)
        self._create_other_tables(cursor)

    def _create_trades_table_v2(self, cursor):
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
                raw_json TEXT NOT NULL
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_time ON trades(time)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_pair ON trades(pair)")

    def _create_other_tables(self, cursor):
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

    def save_trades(self, trades: List[Dict[str, Any]]):
        if not trades:
            return

        conn = self._get_conn()
        cursor = conn.cursor()

        for trade in trades:
            raw_json = json.dumps(trade)

            # We no longer handle 'trades' list field in the 'trades' table.
            # If the object is actually an Order (with 'trades' list), it should be passed to save_orders.
            # But just in case, we ignore that field here.

            cursor.execute("""
                INSERT OR IGNORE INTO trades (
                    id, ordertxid, pair, time, type, ordertype, price, cost, fee, vol,
                    margin, misc, posstatus, cprice, ccost, cfee, cvol, cmargin, net,
                    raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                raw_json
            ))
        conn.commit()
        conn.close()

    def save_orders(self, orders: List[Dict[str, Any]]):
        if not orders:
            return

        conn = self._get_conn()
        cursor = conn.cursor()

        for order in orders:
            # Assuming 'order' has 'id' or we use keys from dict if it's a map?
            # Kraken ClosedOrders returns {txid: {order info...}}
            # We assume flattened here.

            order_id = order.get("id")
            if not order_id:
                # Should not happen if pre-processed
                continue

            raw_json = json.dumps(order)

            cursor.execute("""
                INSERT OR IGNORE INTO orders (
                    order_id, pair, status, opened, closed, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?)
            """, (
                order_id,
                order.get("pair"), # Actually Kraken calls it 'descr' -> 'pair', need deep parsing?
                # Kraken ClosedOrders: {id: { status:..., opentm:..., closetm:..., descr: {pair: ...} }}
                # We assume simple fields for now or rely on raw_json for details.
                # If parsed:
                order.get("status"),
                order.get("opentm"),
                order.get("closetm"),
                raw_json
            ))

            # Handle trades list
            trade_ids = order.get("trades")
            if trade_ids and isinstance(trade_ids, list):
                for trade_id in trade_ids:
                    cursor.execute("""
                        INSERT OR IGNORE INTO order_trades (order_id, trade_id)
                        VALUES (?, ?)
                    """, (order_id, str(trade_id)))

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

        query += " ORDER BY time DESC"

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
                {"asset": av.asset, "amount": av.amount, "value_base": av.value_base, "source_pair": av.source_pair}
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
                    AssetValuation(**av) for av in data["asset_valuations"]
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
