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
    def get_order(self, order_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves a single order by ID."""
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
             cursor.execute("INSERT INTO schema_version (id, version) VALUES (1, 3)")
             current_version = 0
        else:
             current_version = row[0]

        # Schema Evolution:
        # V1: trades table with trades_csv
        # V2: (conceptual) trades table without trades_csv, plus orders/order_trades tables?
        # V3: Explicit orders table with userref, status, etc.

        # We ensure tables exist first
        self._create_tables(cursor)

        # If upgrading from V1/V2 to V3, we might need specific migrations?
        # For now, create IF NOT EXISTS is idempotent.
        # But if we want to migrate data from V1 (trades_csv) to V2/V3 structure?
        # Let's keep it simple: Ensure tables exist.

        if current_version < 3:
            cursor.execute("UPDATE schema_version SET version = 3 WHERE id = 1")

        conn.commit()
        conn.close()

    def _create_tables(self, cursor):
        # Trades Table (V3 version: keeps trades_csv for robustness/compat, or strict?)
        # User requested: "Keep using TradesHistory as the canonical trade source and store each trade in the trades table as we currently do (with the existing list/raw_json handling)."
        # So we keep `trades` as is.
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

        # Orders Table (New for Phase 3 "Full")
        # order_id, pair, time, type, status, userref, raw_json
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                order_id TEXT PRIMARY KEY,
                pair TEXT,
                time REAL,
                type TEXT,
                status TEXT,
                userref INTEGER,
                raw_json TEXT NOT NULL
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_orders_time ON orders(time)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_orders_userref ON orders(userref)")

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
                trades_csv,
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
            # Kraken ClosedOrders: key is txid, val is dict with status, userref etc.
            # We assume input is a list of dicts where 'id' is the txid.

            order_id = order.get("id")
            if not order_id:
                continue

            raw_json = json.dumps(order)

            # Fields: pair, time, type, status, userref
            # 'descr' usually holds pair, type.
            descr = order.get("descr", {})
            pair = descr.get("pair")
            otype = descr.get("type") # buy/sell

            # time: opentm or closetm? Usually index by opentm or closetm.
            # Let's use closetm as primary time for closed orders? Or opentm?
            # Schema says just 'time'. Let's use 'closetm' if available, else 'opentm'.
            # Actually, for tagging, we just need to look it up.
            ts = order.get("closetm") or order.get("opentm")

            userref = order.get("userref")

            cursor.execute("""
                INSERT OR REPLACE INTO orders (
                    order_id, pair, time, type, status, userref, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                order_id,
                pair,
                float(ts) if ts else 0.0,
                otype,
                order.get("status"),
                int(userref) if userref else None,
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

        query += " ORDER BY time DESC"

        if limit:
            query += " LIMIT ?"
            params.append(limit)

        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()

        return [json.loads(row[0]) for row in rows]

    def get_order(self, order_id: str) -> Optional[Dict[str, Any]]:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT raw_json FROM orders WHERE order_id = ?", (order_id,))
        row = cursor.fetchone()
        conn.close()

        if row:
            return json.loads(row[0])
        return None

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
