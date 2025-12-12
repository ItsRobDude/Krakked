# src/kraken_bot/portfolio/manager.py

import logging
from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union

from kraken_bot.config import AppConfig
from kraken_bot.connection.rate_limiter import RateLimiter
from kraken_bot.connection.rest_client import KrakenRESTClient
from kraken_bot.logging_config import structured_log_extra
from kraken_bot.market_data.api import MarketDataAPI
from kraken_bot.strategy.performance import compute_strategy_performance

from .balance_engine import BalanceEngine, classify_cashflow, rebuild_balances
from .models import CashFlowRecord, LedgerEntry, PortfolioSnapshot
from .portfolio import Portfolio
from .store import PortfolioStore, SQLitePortfolioStore

if TYPE_CHECKING:  # pragma: no cover
    from kraken_bot.strategy.models import DecisionRecord

logger = logging.getLogger(__name__)


class PortfolioService:
    def __init__(
        self,
        config: AppConfig,
        market_data: MarketDataAPI,
        db_path: str = "portfolio.db",
        rest_client: Optional[KrakenRESTClient] = None,
        rate_limiter: Optional[RateLimiter] = None,
    ):
        self.config = config.portfolio
        self.app_config = config  # Keep full config if needed
        self.market_data = market_data
        resolved_db_path = db_path or getattr(
            config.portfolio, "db_path", "portfolio.db"
        )
        self.store: PortfolioStore = SQLitePortfolioStore(
            db_path=resolved_db_path,
            auto_migrate_schema=self.config.auto_migrate_schema,
        )
        self.rest_client = rest_client or KrakenRESTClient(
            rate_limiter=rate_limiter
        )  # Used for balance checks and ledger/trades fetching if not passed in

        strategy_tags = {
            cfg.name: cfg.name for cfg in config.strategies.configs.values()
        }
        userref_to_strategy = {
            str(cfg.userref): cfg.name
            for cfg in config.strategies.configs.values()
            if cfg.userref is not None
        }

        self.portfolio = Portfolio(
            self.config,
            self.market_data,
            self.store,
            strategy_tags=strategy_tags,
            userref_to_strategy=userref_to_strategy,
        )
        self._bootstrapped = False
        self._last_sync_ok: bool = True  # pessimistic until first failure

    @property
    def last_sync_ok(self) -> bool:
        """True if the last sync completed successfully."""

        return self._last_sync_ok

    def initialize(self):
        """
        Loads config, performs initial sync, builds internal state.
        """
        logger.info(
            "Initializing PortfolioService...",
            extra=structured_log_extra(event="portfolio_init"),
        )
        self._bootstrap_from_store()
        self.sync()
        logger.info(
            "PortfolioService initialized.",
            extra=structured_log_extra(event="portfolio_ready"),
        )

    def _bootstrap_from_store(self):
        if self._bootstrapped:
            return

        # 1. Load latest BalanceSnapshot
        snapshot = self.store.get_latest_balance_snapshot()
        start_id = snapshot.last_ledger_id if snapshot else None

        # 2. Load ledger entries after snapshot
        ledgers = self.store.get_ledger_entries(after_id=start_id)

        # 3. Rebuild balances
        balances = rebuild_balances(ledgers, snapshot)
        # We need to manually inject these balances into the portfolio
        self.portfolio.balances = balances

        # 4. Ingest trades for positions
        trades = self.store.get_trades(limit=None)
        trades.sort(key=lambda t: t.get("time", 0))
        if trades:
            self.portfolio.ingest_trades(trades, persist=False)

        self._bootstrapped = True

    def sync(self) -> Dict[str, int]:
        """
        Fetches new trades/ledgers, updates state, reconciles.
        """

        logger.info(
            "Syncing portfolio...",
            extra=structured_log_extra(event="portfolio_sync_start"),
        )

        self._bootstrap_from_store()
        self._last_sync_ok = False

        # 1. Fetch and Save Trades
        latest_trades = self.store.get_trades(limit=1)  # ordered by time desc
        since_ts = latest_trades[0]["time"] if latest_trades else None

        params = {}
        if since_ts:
            params["start"] = since_ts

        new_trades: List[Dict] = []
        safety_counter = 0
        last_cursor = since_ts

        while True:
            resp = self.rest_client.get_private("TradesHistory", params=params)
            trades_dict = resp.get("trades", {})
            if not trades_dict:
                break

            batch = []
            for txid, trade_data in trades_dict.items():
                trade_data["id"] = txid
                batch.append(trade_data)

            batch.sort(key=lambda x: x["time"])

            if not batch:
                break

            new_trades.extend(batch)

            resp_last = resp.get("last")
            try:
                resp_last = float(resp_last) if resp_last is not None else None
            except (TypeError, ValueError):
                resp_last = None

            if resp_last is not None and resp_last == last_cursor:
                break

            if resp_last is not None:
                params["start"] = resp_last
                last_cursor = resp_last
            else:
                last_ts = batch[-1]["time"]
                last_cursor = last_ts + 1e-6
                params["start"] = last_cursor

            safety_counter += 1
            if safety_counter > 200:
                logger.warning(
                    "TradesHistory pagination aborted after 200 pages to avoid infinite loop.",
                    extra=structured_log_extra(
                        event="portfolio_sync_pagination_aborted", pages=safety_counter
                    ),
                )
                break

        if new_trades:
            try:
                self.portfolio.ingest_trades(new_trades, persist=False)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "portfolio.sync.ingest_trades_failed",
                    extra={"since": since_ts, "count": len(new_trades)},
                )
                return {"new_trades": 0, "new_cash_flows": 0}

            normalized_trades = [
                self.portfolio._normalize_trade_payload(t) for t in new_trades
            ]
            self.store.save_trades(normalized_trades)

        # 2. Fetch Ledgers (New Flow)
        # Find the latest ledger entry to determine start time
        last_entry = self.store.get_latest_ledger_entry()
        last_ledger_time = last_entry.time if last_entry else 0

        ledger_params = {}
        if last_ledger_time > 0:
            ledger_params["start"] = last_ledger_time

        # Efficient duplicate detection for boundary
        known_ids_at_boundary = set()
        if last_ledger_time > 0:
            # Fetch only entries at or after the last timestamp (small window usually)
            boundary_entries = self.store.get_ledger_entries(since=last_ledger_time)
            known_ids_at_boundary = {e.id for e in boundary_entries}

        ledger_resp = self.rest_client.get_ledgers(params=ledger_params)
        ledger_dict = ledger_resp.get("ledger", {})

        new_ledger_entries = []
        cash_flow_records = []

        if ledger_dict:
            # Normalize and store
            for lid, info in ledger_dict.items():
                # info has refid, time, type, aclass, asset, amount, fee, balance
                entry_time = info.get("time", 0.0)

                # Deduplication logic
                if entry_time < last_ledger_time:
                    continue
                if entry_time == last_ledger_time and lid in known_ids_at_boundary:
                    continue

                new_ledger_entries.append(self._create_ledger_entry(lid, info))

            # Sort by time
            new_ledger_entries.sort(key=lambda x: (x.time, x.id))

            engine = BalanceEngine(self.portfolio.balances)

            for entry in new_ledger_entries:
                self.store.save_ledger_entry(entry)

                # Update in-memory balances
                # BalanceEngine modifies self.portfolio.balances in-place (reference copy)
                engine.apply_entry(entry)

                # Classify and record Cash Flow (but do NOT apply to portfolio balances again, as engine.apply_entry did it)
                cf = classify_cashflow(entry)
                if cf:
                    cash_flow_records.append(cf)

        if cash_flow_records:
            self.store.save_cash_flows(cash_flow_records)

        # 3. Reconcile
        self._reconcile()
        self._last_sync_ok = True

        return {
            "new_trades": len(new_trades),
            "new_cash_flows": len(cash_flow_records),
        }

    def _reconcile(self):
        """Fetch live balances and flag drift."""
        try:
            balance_resp = self.rest_client.get_private("Balance")
        except Exception:  # noqa: BLE001
            # Offline mode: use local balances
            logger.warning(
                "Failed to fetch live balance. Using local ledger balances.",
                extra=structured_log_extra(event="portfolio_offline_mode"),
            )
            # Drift is not checked since we have no source of truth
            # But we can still report status
            return

        drift_detected = self.portfolio.reconcile(balance_resp)

        # Additional: Compare Ledger Balances vs Live Balances
        # self.portfolio.balances IS the ledger balance now.
        # self.portfolio.reconcile compares self.balances vs API.
        # So it ALREADY does exactly what we want: verify ledger vs live.

        if drift_detected:
            logger.warning(
                "Portfolio Drift Detected during reconciliation.",
                extra=structured_log_extra(event="portfolio_drift_detected"),
            )

    def record_decision(self, record: "DecisionRecord"):
        """
        Persists a strategy decision record to the portfolio store.
        """
        self.store.add_decision(record)

    def get_decisions(
        self,
        plan_id: Optional[str] = None,
        since: Optional[int] = None,
        limit: Optional[int] = None,
        strategy_name: Optional[str] = None,
    ) -> List["DecisionRecord"]:
        return self.store.get_decisions(
            plan_id=plan_id, since=since, limit=limit, strategy_name=strategy_name
        )

    def record_execution_plan(self, plan):
        """Persist a full execution plan for downstream execution services."""
        self.store.save_execution_plan(plan)

    def get_execution_plans(
        self,
        plan_id: Optional[str] = None,
        since: Optional[int] = None,
        limit: Optional[int] = None,
    ):
        return self.store.get_execution_plans(plan_id=plan_id, since=since, limit=limit)

    def get_execution_plan(self, plan_id: str):
        return self.store.get_execution_plan(plan_id)

    # ------------------------------------------------------------------
    # Proxy helpers to embedded Portfolio
    # ------------------------------------------------------------------
    @property
    def balances(self):
        return self.portfolio.balances

    @balances.setter
    def balances(self, value):
        self.portfolio.balances = value

    @property
    def positions(self):
        return self.portfolio.positions

    @property
    def realized_pnl_history(self):
        return self.portfolio.realized_pnl_history

    @property
    def drift_flag(self) -> bool:
        return self.portfolio.drift_flag

    def get_equity(self, include_manual: Optional[bool] = None):
        return self.portfolio.equity_view(include_manual=include_manual)

    def get_positions(self):
        return self.portfolio.get_positions()

    def get_drift_status(self):
        return self.portfolio.get_drift_status()

    def get_position(self, pair: str):
        return self.portfolio.get_position(pair)

    def get_trade_history(
        self,
        pair: Optional[str] = None,
        limit: Optional[int] = None,
        since: Optional[int] = None,
        until: Optional[int] = None,
        ascending: bool = False,
    ) -> List[Dict]:
        return self.portfolio.get_trade_history(
            pair=pair, limit=limit, since=since, until=until, ascending=ascending
        )

    def get_cash_flows(
        self,
        asset: Optional[str] = None,
        limit: Optional[int] = None,
        since: Optional[int] = None,
        until: Optional[int] = None,
        ascending: bool = False,
    ) -> List[CashFlowRecord]:
        return self.portfolio.get_cash_flows(
            asset=asset, limit=limit, since=since, until=until, ascending=ascending
        )

    def get_fee_summary(self) -> Dict[str, Union[Dict[str, float], float]]:
        return self.portfolio.get_fee_summary()

    def get_asset_exposure(self, include_manual: Optional[bool] = None):
        return self.portfolio.get_asset_exposure(include_manual=include_manual)

    def get_realized_pnl_by_strategy(
        self, include_manual: Optional[bool] = None
    ) -> Dict[str, float]:
        return self.portfolio.get_realized_pnl_by_strategy(
            include_manual=include_manual
        )

    def get_strategy_performance(self, window_hours: int = 72):
        window = timedelta(hours=window_hours)
        return compute_strategy_performance(self.portfolio, window)

    def create_snapshot(self) -> PortfolioSnapshot:
        return self.portfolio.snapshot()

    def get_snapshots(
        self, since: Optional[int] = None, limit: Optional[int] = None
    ) -> List[PortfolioSnapshot]:
        return self.portfolio.get_snapshots(since=since, limit=limit)

    def get_latest_snapshot(self) -> Optional[PortfolioSnapshot]:
        return self.portfolio.get_latest_snapshot()

    def _create_ledger_entry(self, lid: str, info: Dict[str, Any]) -> LedgerEntry:
        """Helper to instantiate LedgerEntry from raw API response."""
        entry_time = info.get("time", 0.0)

        # Handle optional balance field
        raw_balance = info.get("balance")
        balance_decimal = (
            Decimal(str(raw_balance)) if raw_balance is not None else None
        )

        return LedgerEntry(
            id=lid,
            time=entry_time,
            type=info.get("type", ""),
            subtype=info.get("subtype", ""),
            aclass=info.get("aclass", ""),
            asset=self.portfolio._normalize_asset(info.get("asset", "")),
            amount=Decimal(str(info.get("amount", 0))),
            fee=Decimal(str(info.get("fee", 0))),
            balance=balance_decimal,
            refid=info.get("refid"),
            misc=None,  # Not always present or needs extraction
            raw=info,
        )
