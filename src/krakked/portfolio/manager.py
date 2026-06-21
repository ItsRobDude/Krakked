# src/krakked/portfolio/manager.py

import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from threading import RLock
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set, Union

from krakked.config import AppConfig, get_config_dir
from krakked.connection.rate_limiter import RateLimiter
from krakked.connection.rest_client import KrakenRESTClient
from krakked.logging_config import structured_log_extra
from krakked.market_data.api import MarketDataAPI
from krakked.portfolio.sync_status import (
    LIVE_SYNC_DEGRADED_REASON,
    LIVE_SYNC_LEDGERS_UNAVAILABLE_REASON,
    LIVE_SYNC_TRADE_HISTORY_LAG_ALERT_TITLE,
    LIVE_SYNC_TRADE_HISTORY_LAGGING_REASON,
    LIVE_SYNC_TRADES_UNAVAILABLE_REASON,
    PORTFOLIO_SYNC_FAILED_REASON,
    AccountTruthSnapshot,
    live_sync_trade_history_lag_escalated_reason,
    max_live_sync_age_seconds,
    read_portfolio_sync_status,
)
from krakked.strategy.performance import compute_strategy_performance

from .balance_engine import BalanceEngine, classify_cashflow, rebuild_balances
from .models import (
    AssetBalance,
    AssetExposure,
    BalanceSnapshot,
    CashFlowRecord,
    DriftStatus,
    EquityView,
    LedgerEntry,
    PortfolioSnapshot,
    SpotPosition,
)
from .portfolio import Portfolio
from .store import PortfolioStore, SQLitePortfolioStore

if TYPE_CHECKING:  # pragma: no cover
    from krakked.execution.models import ExecutionResult, LocalOrder
    from krakked.strategy.models import DecisionRecord

logger = logging.getLogger(__name__)

DEFAULT_PAPER_STARTING_CASH_USD = 10_000.0
DEFAULT_PORTFOLIO_DB_NAME = "portfolio.db"
DEFAULT_PROFILE_PAPER_DB_NAME = "portfolio.paper.db"
_SYNC_TIME_UNSET = object()


@dataclass(frozen=True)
class _TradeSyncResult:
    count: int
    trade_ids: Set[str]
    failed: bool = False


@dataclass(frozen=True)
class _LedgerSyncResult:
    cash_flow_count: int
    trade_refids: Set[str]
    failed: bool = False


@dataclass(frozen=True)
class _TradeHistoryLagStatus:
    ref_times: Dict[str, float]
    escalated_refids: Set[str]
    max_age_seconds: int

    @property
    def missing_refids(self) -> Set[str]:
        return set(self.ref_times)

    @property
    def escalated(self) -> bool:
        return bool(self.escalated_refids)


def resolve_portfolio_db_path(config: AppConfig, db_path: Optional[str] = None) -> str:
    configured_path = str(
        db_path
        or getattr(config.portfolio, "db_path", None)
        or DEFAULT_PORTFOLIO_DB_NAME
    )
    if (
        getattr(config.execution, "mode", None) == "paper"
        and configured_path == DEFAULT_PORTFOLIO_DB_NAME
        and getattr(config.session, "profile_name", None)
    ):
        profile_name = str(config.session.profile_name)
        target = (
            get_config_dir() / "profiles" / profile_name / DEFAULT_PROFILE_PAPER_DB_NAME
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        return str(target)

    resolved = Path(configured_path).expanduser()
    if resolved.is_absolute():
        return str(resolved)
    return str(resolved)


class PortfolioService:
    def __init__(
        self,
        config: AppConfig,
        market_data: MarketDataAPI,
        db_path: str = "portfolio.db",
        rest_client: Optional[KrakenRESTClient] = None,
        rate_limiter: Optional[RateLimiter] = None,
        alert_notifier: Optional[Any] = None,
    ):
        self.config = config.portfolio
        self.app_config = config  # Keep full config if needed
        self.market_data = market_data
        resolved_db_path = resolve_portfolio_db_path(config, db_path)
        self.store: PortfolioStore = SQLitePortfolioStore(
            db_path=resolved_db_path,
            auto_migrate_schema=self.config.auto_migrate_schema,
        )
        self.rest_client = rest_client or KrakenRESTClient(
            rate_limiter=rate_limiter
        )  # Used for balance checks and ledger/trades fetching if not passed in
        self.alert_notifier = alert_notifier

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
        # Compatibility default; live safety surfaces treat missing sync time
        # as degraded.
        self._account_truth_lock = RLock()
        self._sync_in_progress: bool = False
        self._last_sync_ok: bool = True
        self._last_sync_at: Optional[datetime] = None
        self._last_sync_reason: Optional[str] = None
        self._baseline_source: str = (
            "paper_wallet"
            if getattr(config.execution, "mode", "paper") == "paper"
            else "ledger_history"
        )
        self._cached_equity: Optional[EquityView] = None
        self._cached_positions: List[SpotPosition] = []
        self._cached_asset_exposure: List[AssetExposure] = []
        self._cached_drift_status: Optional[DriftStatus] = None
        self._cached_last_snapshot_ts: Optional[int] = None
        self._exchange_reference_balances: Dict[str, AssetBalance] = {}
        self._exchange_reference_checked_at: Optional[datetime] = None
        self._exchange_reference_equity: Optional[EquityView] = None
        self._trade_history_lag_alerted_refs: Set[str] = set()

    @property
    def last_sync_ok(self) -> bool:
        """True if the last sync completed successfully."""

        lock = getattr(self, "_account_truth_lock", None)
        if lock is None:
            return getattr(self, "_last_sync_ok", True)
        with lock:
            return self._last_sync_ok

    @property
    def last_sync_at(self) -> Optional[datetime]:
        """Timestamp of the most recent successful sync."""

        lock = getattr(self, "_account_truth_lock", None)
        if lock is None:
            return getattr(self, "_last_sync_at", None)
        with lock:
            return self._last_sync_at

    @property
    def last_sync_reason(self) -> Optional[str]:
        """Reason for the last failed sync, if any."""

        lock = getattr(self, "_account_truth_lock", None)
        if lock is None:
            return getattr(self, "_last_sync_reason", None)
        with lock:
            return self._last_sync_reason

    @property
    def sync_in_progress(self) -> bool:
        """True while a portfolio sync attempt is currently running."""

        lock = getattr(self, "_account_truth_lock", None)
        if lock is None:
            return bool(getattr(self, "_sync_in_progress", False))
        with lock:
            return self._sync_in_progress

    def _set_sync_in_progress(self, value: bool) -> None:
        lock = getattr(self, "_account_truth_lock", None)
        if lock is None:
            self._sync_in_progress = bool(value)
            return
        with lock:
            self._sync_in_progress = bool(value)

    def _set_last_sync_state(
        self,
        *,
        ok: bool,
        reason: Optional[str],
        last_sync_at: object = _SYNC_TIME_UNSET,
    ) -> None:
        lock = getattr(self, "_account_truth_lock", None)
        if lock is None:
            self._last_sync_ok = bool(ok)
            self._last_sync_reason = reason
            if last_sync_at is not _SYNC_TIME_UNSET:
                self._last_sync_at = (
                    last_sync_at if isinstance(last_sync_at, datetime) else None
                )
            return
        with lock:
            self._last_sync_ok = bool(ok)
            self._last_sync_reason = reason
            if last_sync_at is not _SYNC_TIME_UNSET:
                self._last_sync_at = (
                    last_sync_at if isinstance(last_sync_at, datetime) else None
                )

    def _execution_mode(self) -> Optional[str]:
        execution_config = getattr(getattr(self, "app_config", None), "execution", None)
        mode = getattr(execution_config, "mode", None)
        return mode if isinstance(mode, str) else None

    def get_account_truth_snapshot(
        self,
        *,
        execution_mode: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> AccountTruthSnapshot:
        """Return a small atomic view of sync and drift truth for safety checks."""

        lock = getattr(self, "_account_truth_lock", None)
        if lock is None:
            state = SimpleNamespace(
                config=self.config,
                last_sync_ok=getattr(self, "_last_sync_ok", True),
                last_sync_reason=getattr(self, "_last_sync_reason", None),
                last_sync_at=getattr(self, "_last_sync_at", None),
                sync_in_progress=bool(getattr(self, "_sync_in_progress", False)),
            )
        else:
            with lock:
                state = SimpleNamespace(
                    config=self.config,
                    last_sync_ok=self._last_sync_ok,
                    last_sync_reason=self._last_sync_reason,
                    last_sync_at=self._last_sync_at,
                    sync_in_progress=self._sync_in_progress,
                )

        now_value = now if isinstance(now, datetime) else datetime.now(timezone.utc)
        sync_status = read_portfolio_sync_status(
            state,
            execution_mode=execution_mode or self._execution_mode(),
            now=now_value,
        )
        drift_status = self.get_drift_status()
        drift_flag = bool(getattr(drift_status, "drift_flag", False))
        drift_info: Optional[Dict[str, Any]]
        if drift_status is None:
            drift_info = None
        else:
            try:
                drift_info = asdict(drift_status)
            except TypeError:
                drift_info = None

        return AccountTruthSnapshot(
            portfolio_sync_ok=sync_status.ok,
            portfolio_sync_reason=sync_status.reason,
            portfolio_last_sync_at=sync_status.last_sync_at,
            portfolio_sync_in_progress=sync_status.in_progress,
            drift_flag=drift_flag,
            drift_info=drift_info,
            generated_at=now_value,
            max_age_seconds=sync_status.max_age_seconds,
            age_seconds=sync_status.age_seconds,
        )

    @property
    def baseline_source(self) -> str:
        """Describes the portfolio baseline currently exposed to the UI."""

        return self._baseline_source

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

    def _refresh_cached_views(self) -> None:
        self._cached_positions = list(self.portfolio.get_positions())
        self._cached_equity = self.portfolio.equity_view()
        self._cached_asset_exposure = self.portfolio.get_asset_exposure()
        self._cached_drift_status = self.portfolio.get_drift_status()
        self._cached_last_snapshot_ts = (
            getattr(self.portfolio, "_last_snapshot_ts", 0) or None
        )
        if self._cached_last_snapshot_ts is None:
            latest_snapshot = self.portfolio.get_latest_snapshot()
            self._cached_last_snapshot_ts = (
                latest_snapshot.timestamp if latest_snapshot else None
            )

    def _bootstrap_from_store(self):
        if self._bootstrapped:
            return

        if self._is_paper_mode():
            snapshot = self.store.get_latest_balance_snapshot()
            trades = self.store.get_trades(limit=None)
            should_seed_wallet = (
                snapshot is None
                or self._should_reset_legacy_paper_snapshot(snapshot, trades)
            )
            if should_seed_wallet:
                self._seed_paper_wallet()
                self._save_balance_snapshot(datetime.now(timezone.utc))
            else:
                assert snapshot is not None
                self.portfolio.balances = dict(snapshot.balances)

            self.portfolio.positions.clear()
            self.portfolio.realized_pnl_history.clear()
            self.portfolio.realized_pnl_base_by_pair.clear()
            self.portfolio.fees_paid_base_by_pair.clear()
            trades.sort(key=lambda t: t.get("time", 0))
            if trades:
                self.portfolio.ingest_trades(trades, persist=False)
            self._baseline_source = "paper_wallet"
            self._refresh_cached_views()
            self._bootstrapped = True
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

        self._refresh_cached_views()
        self._bootstrapped = True

    def sync(self) -> Dict[str, int]:
        """
        Synchronizes local state with the Kraken API via a multi-stage update.

        This process ensures the local database mirrors the exchange state by:
        1. Fetching `TradesHistory` incrementally, handling pagination and inclusive
           timestamp boundaries to filter duplicates.
        2. Fetching `Ledgers` to capture non-trade transfers (deposits, withdrawals, fees),
           replaying them through the `BalanceEngine` to rebuild accurate balances.
        3. Reconciling the calculated local balance against the live `Balance` API
           to detect and flag any drift.

        Returns:
            Dict[str, int]: Statistics on the sync operation (e.g., {"new_trades": 5, "new_cash_flows": 1}).
        """

        logger.info(
            "Syncing portfolio...",
            extra=structured_log_extra(event="portfolio_sync_start"),
        )

        self._set_sync_in_progress(True)
        try:
            self._bootstrap_from_store()

            if self._is_paper_mode():
                return self._sync_paper_wallet()

            # 1. Fetch and Save Trades
            latest_trades = self.store.get_trades(limit=1)  # ordered by time desc
            since_ts = latest_trades[0]["time"] if latest_trades else None

            trade_result = self._sync_trades_history(since_ts)
            if trade_result.failed:
                return {"new_trades": 0, "new_cash_flows": 0}

            # 2. Fetch Ledgers
            ledger_result = self._sync_ledgers()
            if ledger_result.failed:
                return {"new_trades": trade_result.count, "new_cash_flows": 0}

            trade_history_lag = self._missing_trade_history_refs(
                trade_result.trade_ids,
            )
            if trade_history_lag.missing_refids:
                self._set_last_sync_state(
                    ok=False,
                    reason=self._trade_history_lag_reason(trade_history_lag),
                )
                self._refresh_cached_views()
                logger.warning(
                    "Ledger trade references arrived before matching TradesHistory records.",
                    extra=structured_log_extra(
                        event="portfolio_trade_history_lagging",
                        missing_trade_refs=sorted(trade_history_lag.missing_refids),
                        escalated_refids=sorted(trade_history_lag.escalated_refids),
                        max_age_seconds=trade_history_lag.max_age_seconds,
                    ),
                )
                self._send_trade_history_lag_alert(trade_history_lag)
                return {
                    "new_trades": trade_result.count,
                    "new_cash_flows": ledger_result.cash_flow_count,
                }
            if hasattr(self, "_trade_history_lag_alerted_refs"):
                self._trade_history_lag_alerted_refs.clear()

            # 3. Reconcile
            reconcile_ok = self._reconcile()
            self._refresh_cached_views()
            if not reconcile_ok:
                return {
                    "new_trades": trade_result.count,
                    "new_cash_flows": ledger_result.cash_flow_count,
                }

            self._set_last_sync_state(
                ok=True,
                reason=None,
                last_sync_at=datetime.now(timezone.utc),
            )

            return {
                "new_trades": trade_result.count,
                "new_cash_flows": ledger_result.cash_flow_count,
            }
        except Exception as exc:
            self._set_last_sync_state(ok=False, reason=PORTFOLIO_SYNC_FAILED_REASON)
            logger.exception(
                "Portfolio sync failed unexpectedly.",
                extra=structured_log_extra(
                    event="portfolio_sync_failed",
                    error=str(exc),
                ),
            )
            raise
        finally:
            self._set_sync_in_progress(False)

    def _is_paper_mode(self) -> bool:
        execution_config = getattr(getattr(self, "app_config", None), "execution", None)
        return getattr(execution_config, "mode", None) == "paper"

    def _seed_paper_wallet(self) -> None:
        base_currency = getattr(self.config, "base_currency", "USD")
        self.portfolio.balances = {
            base_currency: AssetBalance(
                asset=base_currency,
                free=DEFAULT_PAPER_STARTING_CASH_USD,
                reserved=0.0,
                total=DEFAULT_PAPER_STARTING_CASH_USD,
            )
        }

    def _should_reset_legacy_paper_snapshot(
        self, snapshot: BalanceSnapshot, trades: List[Dict[str, Any]]
    ) -> bool:
        if trades:
            return False

        balances = dict(snapshot.balances)
        if len(balances) != 1:
            return True

        base_currency = getattr(self.config, "base_currency", "USD")
        base_balance = balances.get(base_currency)
        if base_balance is None:
            return True

        return abs(base_balance.total - DEFAULT_PAPER_STARTING_CASH_USD) > 1e-9

    def _sync_paper_wallet(self) -> Dict[str, int]:
        """Persist local paper state without overwriting it from Kraken balances."""

        now = datetime.now(timezone.utc)
        self._baseline_source = "paper_wallet"
        self._refresh_exchange_reference(now)
        self._save_balance_snapshot(now)
        self.portfolio.maybe_snapshot(now=int(now.timestamp()))
        self._refresh_cached_views()

        self._set_last_sync_state(ok=True, reason=None, last_sync_at=now)
        return {"new_trades": 0, "new_cash_flows": 0}

    def _refresh_exchange_reference(self, now: datetime) -> None:
        if self.rest_client is None:
            return
        try:
            balance_resp = self.rest_client.get_private("Balance")
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.warning(
                "Paper exchange reference refresh failed",
                extra=structured_log_extra(
                    event="paper_reference_refresh_failed",
                    error=str(exc),
                ),
            )
            return

        balances = self._balances_from_exchange(balance_resp)
        self._exchange_reference_balances = balances
        self._exchange_reference_equity = self._equity_view_for_balances(balances)
        self._exchange_reference_checked_at = now

    def _balances_from_exchange(
        self, balance_resp: Dict[str, str]
    ) -> Dict[str, AssetBalance]:
        balances: Dict[str, AssetBalance] = {}
        for asset_raw, amount_str in balance_resp.items():
            try:
                total = float(amount_str)
            except (TypeError, ValueError):
                continue

            if abs(total) < 1e-12:
                continue

            asset = self.market_data.normalize_asset(asset_raw)
            balances[asset] = AssetBalance(
                asset=asset,
                free=total,
                reserved=0.0,
                total=total,
            )

        return balances

    def _equity_view_for_balances(
        self, balances: Dict[str, AssetBalance]
    ) -> EquityView:
        equity = 0.0
        cash = 0.0
        unvalued_assets: List[str] = []
        for asset, balance in balances.items():
            conversion = self.portfolio._convert_to_base_currency(balance.total, asset)
            equity += conversion.value_base
            if asset == self.config.base_currency:
                cash += conversion.value_base
            if conversion.status == "unvalued":
                unvalued_assets.append(asset)

        return EquityView(
            equity_base=equity,
            cash_base=cash,
            realized_pnl_base_total=0.0,
            unrealized_pnl_base_total=0.0,
            drift_flag=False,
            unvalued_assets=unvalued_assets,
        )

    def _save_balance_snapshot(self, now: datetime) -> None:
        snapshot = BalanceSnapshot(
            id=None,
            time=now.timestamp(),
            last_ledger_id="",
            balances={
                asset: AssetBalance(
                    asset=balance.asset,
                    free=balance.free,
                    reserved=balance.reserved,
                    total=balance.total,
                )
                for asset, balance in self.portfolio.balances.items()
            },
        )
        latest_snapshot = self.store.get_latest_balance_snapshot()
        if latest_snapshot and latest_snapshot.balances == snapshot.balances:
            return
        self.store.save_balance_snapshot(snapshot)

    def ingest_simulated_trades(self, trades: List[Dict[str, Any]]) -> None:
        if not trades:
            return

        for trade in trades:
            self._apply_simulated_trade_balances(trade)

        self.portfolio.ingest_trades(trades, persist=True)
        now = datetime.now(timezone.utc)
        self._save_balance_snapshot(now)
        self.portfolio.maybe_snapshot(now=int(now.timestamp()))
        self._refresh_cached_views()

    def _apply_simulated_trade_balances(self, trade: Dict[str, Any]) -> None:
        pair_meta = self.market_data.get_pair_metadata(trade["pair"])
        base_asset = self.market_data.normalize_asset(pair_meta.base)
        quote_asset = self.market_data.normalize_asset(pair_meta.quote)
        volume = float(trade.get("vol", 0.0))
        cost = float(trade.get("cost", 0.0))
        fee = float(trade.get("fee", 0.0))
        side = str(trade.get("type", "")).lower()

        base_balance = self.portfolio.balances.get(
            base_asset, AssetBalance(base_asset, 0.0, 0.0, 0.0)
        )
        quote_balance = self.portfolio.balances.get(
            quote_asset, AssetBalance(quote_asset, 0.0, 0.0, 0.0)
        )

        if side == "buy":
            base_delta = volume
            quote_delta = -(cost + fee)
        else:
            base_delta = -volume
            quote_delta = cost - fee

        base_balance.free += base_delta
        base_balance.total += base_delta
        quote_balance.free += quote_delta
        quote_balance.total += quote_delta

        self.portfolio.balances[base_asset] = base_balance
        self.portfolio.balances[quote_asset] = quote_balance

    def ingest_filled_orders(
        self, execution: "ExecutionResult", fee_bps: float = 0.0
    ) -> None:
        trades: List[Dict[str, Any]] = []
        for order in getattr(execution, "orders", []):
            trade = self._trade_from_filled_order(order, fee_bps=fee_bps)
            if trade is not None:
                trades.append(trade)

        if trades:
            self.ingest_simulated_trades(trades)

    def _trade_from_filled_order(
        self, order: "LocalOrder", fee_bps: float = 0.0
    ) -> Optional[Dict[str, Any]]:
        if getattr(order, "status", None) != "filled":
            return None

        price = getattr(order, "avg_fill_price", None) or getattr(
            order, "requested_price", None
        )
        volume = getattr(order, "cumulative_base_filled", 0.0) or getattr(
            order, "requested_base_size", 0.0
        )
        if price is None or float(volume) <= 0:
            return None

        price_value = float(price)
        volume_value = float(volume)
        notional = price_value * volume_value
        fee = notional * max(float(fee_bps), 0.0) / 10_000.0
        updated_at = getattr(order, "updated_at", datetime.now(timezone.utc))
        timestamp = (
            updated_at.timestamp()
            if isinstance(updated_at, datetime)
            else datetime.now(timezone.utc).timestamp()
        )

        return {
            "id": f"paper-trade-{order.local_id}",
            "ordertxid": getattr(order, "kraken_order_id", None) or order.local_id,
            "pair": order.pair,
            "time": timestamp,
            "type": order.side,
            "ordertype": order.order_type,
            "price": price_value,
            "cost": notional,
            "fee": fee,
            "vol": volume_value,
            "margin": 0.0,
            "misc": "",
            "posstatus": None,
            "strategy_tag": order.strategy_id,
            "userref": order.userref,
        }

    def _sync_trades_history(self, since_ts: Optional[float]) -> _TradeSyncResult:
        """Fetch, deduplicate, enrich, and persist new trades."""
        params = {}
        known_txids_at_boundary = set()

        if since_ts:
            params["start"] = since_ts
            # Prefetch trades at the boundary to prevent duplicates
            # Store methods expect int timestamps but allow float as well in practice.
            # We explicit cast if necessary, but here since_ts + 1e-6 is float.
            # The store implementation _to_timestamp handles float fine.
            # We just need to make sure the type checker is happy or ignore it if store.py types are strict.
            boundary_trades = self.store.get_trades(
                since=since_ts, until=since_ts + 1e-6  # type: ignore[arg-type]
            )
            for t in boundary_trades:
                known_txids_at_boundary.add(t.get("ordertxid"))
                # Also add trade ID itself if available (usually 'id' in our store matches what we'd expect?)
                # Actually, 'ordertxid' is the order ID, trade ID is usually the key in the map or 'id'
                if t.get("id"):
                    known_txids_at_boundary.add(t.get("id"))

        new_trades: List[Dict] = []
        safety_counter = 0
        last_cursor = since_ts

        try:
            while True:
                resp = self.rest_client.get_private("TradesHistory", params=params)
                trades_dict = resp.get("trades", {})
                if not trades_dict:
                    break

                batch: List[Dict[str, Any]] = []
                for txid, trade_data in trades_dict.items():
                    # Deduplicate against boundary
                    if txid in known_txids_at_boundary:
                        continue

                    trade_record: Dict[str, Any] = dict(trade_data)
                    trade_record["id"] = txid

                    # --- Strategy attribution ---
                    # TradesHistory does not reliably include order-level metadata like `userref`.
                    # If this trade belongs to an order we submitted/tracked locally, enrich the
                    # stored trade record with a strategy tag (and userref when available) so that
                    # PnL attribution works even when Kraken does not return `userref` on trades.
                    try:
                        ordertxid = trade_record.get("ordertxid")
                        local_order = (
                            self.store.get_order_by_reference(kraken_order_id=ordertxid)
                            if ordertxid
                            else None
                        )
                    except Exception:
                        local_order = None

                    if local_order is not None:
                        trade_record.setdefault("strategy_tag", local_order.strategy_id)
                        if getattr(local_order, "userref", None) is not None:
                            trade_record.setdefault("userref", local_order.userref)

                    batch.append(trade_record)

                batch.sort(key=lambda x: x["time"])

                if batch:
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
                    if batch:
                        last_ts = batch[-1]["time"]
                        last_cursor = last_ts + 1e-6
                        params["start"] = last_cursor
                    else:
                        if resp_last:
                            params["start"] = resp_last
                            last_cursor = resp_last
                        else:
                            break

                safety_counter += 1
                if safety_counter > 200:
                    logger.warning(
                        "TradesHistory pagination aborted after 200 pages.",
                        extra=structured_log_extra(
                            event="portfolio_sync_pagination_aborted",
                            pages=safety_counter,
                        ),
                    )
                    break
        except Exception as exc:  # noqa: BLE001
            self._set_last_sync_state(
                ok=False,
                reason=LIVE_SYNC_TRADES_UNAVAILABLE_REASON,
            )
            logger.warning(
                "TradesHistory sync unavailable; account truth is degraded until Kraken trade history can be verified.",
                extra=structured_log_extra(
                    event="portfolio_trades_history_unavailable",
                    error=str(exc),
                ),
            )
            return _TradeSyncResult(count=0, trade_ids=set(), failed=True)

        if new_trades:
            try:
                self.portfolio.ingest_trades(new_trades, persist=False)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "portfolio.sync.ingest_trades_failed",
                    extra={"since": since_ts, "count": len(new_trades)},
                )
                self._set_last_sync_state(
                    ok=False,
                    reason=LIVE_SYNC_TRADES_UNAVAILABLE_REASON,
                )
                return _TradeSyncResult(count=0, trade_ids=set(), failed=True)

            normalized_trades = [
                self.portfolio._normalize_trade_payload(t) for t in new_trades
            ]
            self.store.save_trades(normalized_trades)

        return _TradeSyncResult(
            count=len(new_trades),
            trade_ids={str(trade["id"]) for trade in new_trades if trade.get("id")},
        )

    def _sync_ledgers(self) -> _LedgerSyncResult:
        """Fetch, process, and persist new ledger entries."""
        last_entry = self.store.get_latest_ledger_entry()
        last_ledger_time = last_entry.time if last_entry else 0

        ledger_params = {}
        if last_ledger_time > 0:
            ledger_params["start"] = last_ledger_time

        known_ids_at_boundary = set()
        if last_ledger_time > 0:
            boundary_entries = self.store.get_ledger_entries(since=last_ledger_time)
            known_ids_at_boundary = {e.id for e in boundary_entries}

        try:
            ledger_resp = self.rest_client.get_ledgers(params=ledger_params)
            ledger_dict = ledger_resp.get("ledger", {})
        except Exception as exc:  # noqa: BLE001
            self._set_last_sync_state(
                ok=False,
                reason=LIVE_SYNC_LEDGERS_UNAVAILABLE_REASON,
            )
            logger.warning(
                "Ledgers sync unavailable; account truth is degraded until Kraken ledgers can be verified.",
                extra=structured_log_extra(
                    event="portfolio_ledgers_unavailable",
                    error=str(exc),
                ),
            )
            return _LedgerSyncResult(cash_flow_count=0, trade_refids=set(), failed=True)

        new_ledger_entries = []
        cash_flow_records = []
        trade_refids: Set[str] = set()

        if ledger_dict:
            for lid, info in ledger_dict.items():
                entry_time = info.get("time", 0.0)

                if entry_time < last_ledger_time:
                    continue
                if entry_time == last_ledger_time and lid in known_ids_at_boundary:
                    continue

                new_ledger_entries.append(self._create_ledger_entry(lid, info))

            new_ledger_entries.sort(key=lambda x: (x.time, x.id))

            engine = BalanceEngine(self.portfolio.balances)

            for entry in new_ledger_entries:
                self.store.save_ledger_entry(entry)
                engine.apply_entry(entry)
                if entry.type == "trade" and entry.refid:
                    trade_refids.add(str(entry.refid))

                cf = classify_cashflow(entry)
                if cf:
                    cash_flow_records.append(cf)

        if cash_flow_records:
            self.store.save_cash_flows(cash_flow_records)

        return _LedgerSyncResult(
            cash_flow_count=len(cash_flow_records),
            trade_refids=trade_refids,
        )

    def _missing_trade_history_refs(
        self, fetched_trade_ids: Set[str]
    ) -> _TradeHistoryLagStatus:
        since_ts = (
            self._last_sync_at.timestamp()
            if isinstance(self._last_sync_at, datetime)
            else None
        )
        ref_times = self.store.get_trade_ledger_ref_times(since=since_ts)
        if not ref_times:
            return _TradeHistoryLagStatus(
                ref_times={},
                escalated_refids=set(),
                max_age_seconds=max_live_sync_age_seconds(self.config),
            )

        candidate_refs = set(ref_times)
        known_trade_ids = set(fetched_trade_ids)
        known_trade_ids.update(self.store.get_trade_ids_by_ids(candidate_refs))
        missing = {
            refid: ref_times[refid]
            for refid in candidate_refs
            if refid not in known_trade_ids
        }
        max_age_seconds = max_live_sync_age_seconds(self.config)
        now_ts = datetime.now(timezone.utc).timestamp()
        escalated_refids = {
            refid
            for refid, ledger_time in missing.items()
            if now_ts - ledger_time > max_age_seconds
        }
        return _TradeHistoryLagStatus(
            ref_times=missing,
            escalated_refids=escalated_refids,
            max_age_seconds=max_age_seconds,
        )

    def _trade_history_lag_reason(self, status: _TradeHistoryLagStatus) -> str:
        if status.escalated:
            return live_sync_trade_history_lag_escalated_reason(status.max_age_seconds)
        return LIVE_SYNC_TRADE_HISTORY_LAGGING_REASON

    def _send_trade_history_lag_alert(self, status: _TradeHistoryLagStatus) -> None:
        if not hasattr(self, "_trade_history_lag_alerted_refs"):
            self._trade_history_lag_alerted_refs = set()
        new_escalated_refids = status.escalated_refids - getattr(
            self, "_trade_history_lag_alerted_refs", set()
        )
        if not new_escalated_refids:
            return

        notifier = getattr(self, "alert_notifier", None)
        if notifier is None or not hasattr(notifier, "send"):
            self._trade_history_lag_alerted_refs.update(new_escalated_refids)
            return

        try:
            notifier.send(
                event="portfolio_trade_history_lag_escalated",
                title=LIVE_SYNC_TRADE_HISTORY_LAG_ALERT_TITLE,
                message=self._trade_history_lag_reason(status),
                severity="error",
                context={
                    "missing_trade_refs": sorted(status.missing_refids),
                    "escalated_refids": sorted(status.escalated_refids),
                    "max_age_seconds": status.max_age_seconds,
                    "oldest_unmatched_ledger_time": min(status.ref_times.values()),
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Failed to send trade-history lag alert",
                extra=structured_log_extra(
                    event="portfolio_trade_history_lag_alert_failed",
                    error=str(exc),
                ),
            )
        finally:
            self._trade_history_lag_alerted_refs.update(new_escalated_refids)

    def _reconcile(self) -> bool:
        """Fetch live balances and flag drift."""
        try:
            balance_resp = self.rest_client.get_private("Balance")
        except Exception as exc:  # noqa: BLE001
            self._set_last_sync_state(ok=False, reason=LIVE_SYNC_DEGRADED_REASON)
            logger.warning(
                "Live balance reconciliation unavailable; local ledger balances are display-only until Kraken balances can be verified.",
                extra=structured_log_extra(
                    event="portfolio_truth_unavailable",
                    error=str(exc),
                ),
            )
            return False

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
        return True

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

    def get_exchange_reference_summary(self) -> Optional[Dict[str, Any]]:
        if self._exchange_reference_equity is None:
            return None

        return {
            "equity_usd": self._exchange_reference_equity.equity_base,
            "cash_usd": self._exchange_reference_equity.cash_base,
            "checked_at": self._exchange_reference_checked_at,
        }

    @property
    def drift_flag(self) -> bool:
        return self.portfolio.drift_flag

    def get_equity(self, include_manual: Optional[bool] = None) -> EquityView:
        return self.portfolio.equity_view(include_manual=include_manual)

    def get_cached_equity(self) -> EquityView:
        if self._cached_equity is None:
            self._refresh_cached_views()
        assert self._cached_equity is not None
        return self._cached_equity

    def get_positions(self):
        return self.portfolio.get_positions()

    def get_cached_positions(self) -> List[SpotPosition]:
        if self._cached_equity is None:
            self._refresh_cached_views()
        return list(self._cached_positions)

    def get_drift_status(self):
        return self.portfolio.get_drift_status()

    def get_cached_drift_status(self) -> DriftStatus:
        if self._cached_drift_status is None:
            self._refresh_cached_views()
        assert self._cached_drift_status is not None
        return self._cached_drift_status

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

    def get_cached_asset_exposure(self) -> List[AssetExposure]:
        if self._cached_equity is None:
            self._refresh_cached_views()
        return list(self._cached_asset_exposure)

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
        snapshot = self.portfolio.snapshot()
        self._cached_last_snapshot_ts = snapshot.timestamp
        return snapshot

    def get_snapshots(
        self, since: Optional[int] = None, limit: Optional[int] = None
    ) -> List[PortfolioSnapshot]:
        return self.portfolio.get_snapshots(since=since, limit=limit)

    def get_latest_snapshot(self) -> Optional[PortfolioSnapshot]:
        return self.portfolio.get_latest_snapshot()

    def get_cached_last_snapshot_ts(self) -> Optional[int]:
        if self._cached_equity is None:
            self._refresh_cached_views()
        return self._cached_last_snapshot_ts

    def _create_ledger_entry(self, lid: str, info: Dict[str, Any]) -> LedgerEntry:
        """Helper to instantiate LedgerEntry from raw API response."""
        entry_time = info.get("time", 0.0)

        # Handle optional balance field
        raw_balance = info.get("balance")
        balance_decimal = Decimal(str(raw_balance)) if raw_balance is not None else None

        # Normalize the asset name (e.g., 'XXBT' -> 'BTC') to ensure consistency
        # with the rest of the system before storing in the database.
        normalized_asset = self.portfolio.market_data.normalize_asset(
            info.get("asset", "")
        )

        return LedgerEntry(
            id=lid,
            time=entry_time,
            type=info.get("type", ""),
            subtype=info.get("subtype", ""),
            aclass=info.get("aclass", ""),
            asset=normalized_asset,
            amount=Decimal(str(info.get("amount", 0))),
            fee=Decimal(str(info.get("fee", 0))),
            balance=balance_decimal,
            refid=info.get("refid"),
            misc=None,  # Not always present or needs extraction
            raw=info,
        )
