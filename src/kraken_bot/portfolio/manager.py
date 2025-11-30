# src/kraken_bot/portfolio/manager.py

import logging
from typing import TYPE_CHECKING, Dict, List, Optional, Union

from kraken_bot.config import AppConfig
from kraken_bot.connection.rate_limiter import RateLimiter
from kraken_bot.market_data.api import MarketDataAPI
from kraken_bot.connection.rest_client import KrakenRESTClient
from .portfolio import Portfolio
from .store import PortfolioStore, SQLitePortfolioStore
from .models import CashFlowRecord, PortfolioSnapshot

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
        self.store: PortfolioStore = SQLitePortfolioStore(db_path=db_path)
        self.rest_client = rest_client or KrakenRESTClient(
            rate_limiter=rate_limiter
        )  # Used for balance checks and ledger/trades fetching if not passed in

        self.portfolio = Portfolio(self.config, self.market_data, self.store)
        self._bootstrapped = False

    def initialize(self):
        """
        Loads config, performs initial sync, builds internal state.
        """
        logger.info("Initializing PortfolioService...")
        self._bootstrap_from_store()
        self.sync()
        logger.info("PortfolioService initialized.")

    def _bootstrap_from_store(self):
        if self._bootstrapped:
            return

        trades = self.store.get_trades(limit=None)
        trades.sort(key=lambda t: t.get("time", 0))
        if trades:
            self.portfolio.ingest_trades(trades, persist=False)

        cash_flows = self.store.get_cash_flows(limit=None)
        cash_flows.sort(key=lambda c: c.time)
        for record in cash_flows:
            # Safe internal call to rebuild balances from persisted cash flows.
            self.portfolio._process_cash_flow(record)

        self._bootstrapped = True

    def sync(self) -> Dict[str, int]:
        """
        Fetches new trades/ledgers, updates state, reconciles.
        """
        logger.info("Syncing portfolio...")

        self._bootstrap_from_store()

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
                logger.warning("TradesHistory pagination aborted after 200 pages to avoid infinite loop.")
                break

        if new_trades:
            self.portfolio.ingest_trades(new_trades, persist=True)

        # 2. Fetch Ledgers for Cash Flows
        latest_cashflows = self.store.get_cash_flows(limit=1)
        since_ledger = latest_cashflows[0].time if latest_cashflows else None

        ledger_params = {}
        if since_ledger:
            ledger_params["start"] = since_ledger

        ledger_resp = self.rest_client.get_ledgers(params=ledger_params)
        ledger_entries = ledger_resp.get("ledger", {})

        cash_flow_records = self.portfolio.ingest_cashflows(ledger_entries, persist=True)

        # 3. Reconcile
        self._reconcile()

        return {
            "new_trades": len(new_trades),
            "new_cash_flows": len(cash_flow_records),
        }

    def _reconcile(self):
        """Fetch live balances and flag drift."""
        balance_resp = self.rest_client.get_private("Balance")
        drift_detected = self.portfolio.reconcile(balance_resp)
        if drift_detected:
            logger.warning("Portfolio Drift Detected during reconciliation.")

    def record_decision(self, record: "DecisionRecord"):
        """
        Persists a strategy decision record to the portfolio store.
        """
        self.store.add_decision(record)

    def get_decisions(
        self, plan_id: Optional[str] = None, since: Optional[int] = None, limit: Optional[int] = None, strategy_name: Optional[str] = None
    ) -> List["DecisionRecord"]:
        return self.store.get_decisions(plan_id=plan_id, since=since, limit=limit, strategy_name=strategy_name)

    def record_execution_plan(self, plan):
        """Persist a full execution plan for downstream execution services."""
        self.store.save_execution_plan(plan)

    def get_execution_plans(self, plan_id: Optional[str] = None, since: Optional[int] = None, limit: Optional[int] = None):
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
        return self.portfolio.get_trade_history(pair=pair, limit=limit, since=since, until=until, ascending=ascending)

    def get_cash_flows(
        self,
        asset: Optional[str] = None,
        limit: Optional[int] = None,
        since: Optional[int] = None,
        until: Optional[int] = None,
        ascending: bool = False,
    ) -> List[CashFlowRecord]:
        return self.portfolio.get_cash_flows(asset=asset, limit=limit, since=since, until=until, ascending=ascending)

    def get_fee_summary(self) -> Dict[str, Union[Dict[str, float], float]]:
        return self.portfolio.get_fee_summary()

    def get_asset_exposure(self, include_manual: Optional[bool] = None):
        return self.portfolio.get_asset_exposure(include_manual=include_manual)

    def get_realized_pnl_by_strategy(self, include_manual: Optional[bool] = None) -> Dict[str, float]:
        return self.portfolio.get_realized_pnl_by_strategy(include_manual=include_manual)

    def create_snapshot(self) -> PortfolioSnapshot:
        return self.portfolio.snapshot()

    def get_snapshots(self, since: Optional[int] = None, limit: Optional[int] = None) -> List[PortfolioSnapshot]:
        return self.portfolio.get_snapshots(since=since, limit=limit)

    def get_latest_snapshot(self) -> Optional[PortfolioSnapshot]:
        return self.portfolio.get_latest_snapshot()
