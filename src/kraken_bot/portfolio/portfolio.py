"""In-memory portfolio tracking and persistence helpers.

The :class:`Portfolio` class encapsulates portfolio state management backed by
an arbitrary :class:`~kraken_bot.portfolio.store.PortfolioStore`.  It is
designed to be lightweight so higher level services can delegate bookkeeping
without duplicating logic.
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Dict, Iterable, List, Optional

from kraken_bot.config import PortfolioConfig
from kraken_bot.market_data.api import MarketDataAPI
from kraken_bot.portfolio.models import (
    AssetBalance,
    AssetExposure,
    AssetValuation,
    CashFlowRecord,
    EquityView,
    PortfolioSnapshot,
    RealizedPnLRecord,
    SpotPosition,
)
from kraken_bot.portfolio.store import PortfolioStore


class Portfolio:
    """Tracks balances, positions, and PnL in memory with SQLite persistence."""

    def __init__(
        self,
        config: PortfolioConfig,
        market_data: MarketDataAPI,
        store: PortfolioStore,
        snapshot_interval_seconds: int = 3600,
    ):
        self.config = config
        self.market_data = market_data
        self.store = store
        self.snapshot_interval_seconds = snapshot_interval_seconds

        self.balances: Dict[str, AssetBalance] = {}
        self.positions: Dict[str, SpotPosition] = {}
        self.realized_pnl_history: List[RealizedPnLRecord] = []
        self.realized_pnl_base_by_pair: Dict[str, float] = defaultdict(float)
        self.fees_paid_base_by_pair: Dict[str, float] = defaultdict(float)
        self.drift_flag: bool = False
        self._last_snapshot_ts: int = 0

    # ------------------------------------------------------------------
    # Ingestion helpers
    # ------------------------------------------------------------------
    def ingest_trades(self, trades: Iterable[Dict], persist: bool = True):
        """Process and optionally persist a collection of raw trades."""

        trades_sorted = sorted(trades, key=lambda t: t.get("time", 0))
        if persist:
            self.store.save_trades([self._normalize_trade_payload(t) for t in trades_sorted])

        for trade in trades_sorted:
            self._process_trade(trade)

    def ingest_cashflows(
        self, ledger_entries: Dict[str, Dict], persist: bool = True
    ) -> List[CashFlowRecord]:
        """Detect deposit/withdrawal cashflows from ledger entries."""

        records: List[CashFlowRecord] = []
        for ledger_id, entry in ledger_entries.items():
            ltype = entry.get("type")
            if ltype not in {"deposit", "withdrawal", "adjustment", "staking"}:
                continue

            normalized_asset = self._normalize_asset(entry.get("asset", ""))
            record = CashFlowRecord(
                id=ledger_id,
                time=int(entry.get("time", 0)),
                asset=normalized_asset,
                amount=float(entry.get("amount", 0)),
                type=ltype,
                note=f"Ref: {entry.get('refid')}" if entry.get("refid") else None,
            )
            records.append(record)
            self._process_cash_flow(record)

        if persist and records:
            self.store.save_cash_flows(records)

        return records

    # ------------------------------------------------------------------
    # Core calculations
    # ------------------------------------------------------------------
    def _process_trade(self, trade: Dict):
        pair_meta = self.market_data.get_pair_metadata(trade["pair"])
        pair = pair_meta.canonical
        base_asset = pair_meta.base
        quote_asset = pair_meta.quote

        side = trade["type"]
        price = float(trade["price"])
        vol = float(trade["vol"])
        cost = float(trade["cost"])
        fee = float(trade.get("fee", 0.0))

        position = self.positions.get(pair)
        if position is None:
            position = SpotPosition(
                pair=pair,
                base_asset=base_asset,
                quote_asset=quote_asset,
                base_size=0.0,
                avg_entry_price=0.0,
                realized_pnl_base=0.0,
                fees_paid_base=0.0,
            )
            self.positions[pair] = position

        fee_in_base = self._convert_to_base_currency(fee, quote_asset)
        position.fees_paid_base += fee_in_base
        self.fees_paid_base_by_pair[pair] += fee_in_base

        if side == "buy":
            previous_cost = position.base_size * position.avg_entry_price
            new_total_qty = position.base_size + vol
            position.avg_entry_price = (previous_cost + cost) / new_total_qty if new_total_qty else 0.0
            position.base_size = new_total_qty
        else:
            gross_pnl_quote = (price - position.avg_entry_price) * vol
            pnl_base = self._convert_to_base_currency(gross_pnl_quote, quote_asset) - fee_in_base
            position.realized_pnl_base += pnl_base
            self.realized_pnl_base_by_pair[pair] += pnl_base
            position.base_size = max(0.0, position.base_size - vol)
            self.realized_pnl_history.append(
                RealizedPnLRecord(
                    trade_id=trade.get("id", ""),
                    order_id=trade.get("ordertxid"),
                    pair=pair,
                    time=int(trade.get("time", 0)),
                    side=side,
                    base_delta=-vol,
                    quote_delta=cost,
                    fee_asset=quote_asset,
                    fee_amount=fee,
                    pnl_quote=pnl_base,
                    strategy_tag="manual",
                )
            )

    def _process_cash_flow(self, record: CashFlowRecord):
        balance = self.balances.get(record.asset, AssetBalance(record.asset, 0.0, 0.0, 0.0))
        balance.total += record.amount
        balance.free += record.amount
        self.balances[record.asset] = balance

    # ------------------------------------------------------------------
    # Equity & reconciliation
    # ------------------------------------------------------------------
    def reconcile(self, live_balances: Dict[str, str]) -> bool:
        """Reconcile live balances and flag drift based on the configured tolerance."""

        self.balances = {
            self._normalize_asset(asset): AssetBalance(
                asset=self._normalize_asset(asset),
                free=float(amount),
                reserved=0.0,
                total=float(amount),
            )
            for asset, amount in live_balances.items()
        }

        position_totals = defaultdict(float)
        for position in self.positions.values():
            position_totals[position.base_asset] += position.base_size

        drift_detected = False
        for asset, pos_total in position_totals.items():
            balance_total = self.balances.get(asset, AssetBalance(asset, 0.0, 0.0, 0.0)).total
            diff_qty = abs(pos_total - balance_total)
            diff_value = self._convert_to_base_currency(diff_qty, asset)
            if diff_value > self.config.reconciliation_tolerance:
                drift_detected = True
                break

        self.drift_flag = drift_detected
        return drift_detected

    def equity_view(self) -> EquityView:
        equity = 0.0
        cash = 0.0
        unrealized = 0.0

        for asset, balance in self.balances.items():
            value_base = self._convert_to_base_currency(balance.total, asset)
            equity += value_base
            if asset == self.config.base_currency:
                cash += value_base

        for position in self.positions.values():
            current_price = self.market_data.get_latest_price(position.pair)
            if current_price is None:
                continue
            current_val = position.base_size * current_price
            cost_basis = position.base_size * position.avg_entry_price
            position.current_value_base = current_val
            position.unrealized_pnl_base = current_val - cost_basis
            unrealized += position.unrealized_pnl_base

        return EquityView(
            equity_base=equity,
            cash_base=cash,
            realized_pnl_base_total=sum(self.realized_pnl_base_by_pair.values()),
            unrealized_pnl_base_total=unrealized,
            drift_flag=self.drift_flag,
        )

    def snapshot(self, now: Optional[int] = None, persist: bool = True, enforce_retention: bool = True) -> PortfolioSnapshot:
        now = now or int(time.time())
        equity = self.equity_view()

        valuations = [
            AssetValuation(
                asset=asset,
                amount=balance.total,
                value_base=self._convert_to_base_currency(balance.total, asset),
                source_pair=f"{asset}{self.config.base_currency}" if asset != self.config.base_currency else None,
            )
            for asset, balance in self.balances.items()
        ]

        snapshot = PortfolioSnapshot(
            timestamp=now,
            equity_base=equity.equity_base,
            cash_base=equity.cash_base,
            asset_valuations=valuations,
            realized_pnl_base_total=equity.realized_pnl_base_total,
            unrealized_pnl_base_total=equity.unrealized_pnl_base_total,
            realized_pnl_base_by_pair=dict(self.realized_pnl_base_by_pair),
            unrealized_pnl_base_by_pair={p.pair: p.unrealized_pnl_base for p in self.positions.values()},
        )

        if persist:
            self.store.save_snapshot(snapshot)
            if enforce_retention:
                cutoff = now - int(self.config.snapshot_retention_days * 86400)
                self.store.prune_snapshots(cutoff)
        self._last_snapshot_ts = now
        return snapshot

    def maybe_snapshot(self, now: Optional[int] = None) -> Optional[PortfolioSnapshot]:
        now = now or int(time.time())
        if now - self._last_snapshot_ts < self.snapshot_interval_seconds:
            return None
        return self.snapshot(now=now)

    # ------------------------------------------------------------------
    # Utility helpers
    # ------------------------------------------------------------------
    def _convert_to_base_currency(self, amount: float, asset: str) -> float:
        if amount == 0 or not asset:
            return 0.0
        if asset == self.config.base_currency:
            return amount
        pair = f"{asset}{self.config.base_currency}"
        price = self.market_data.get_latest_price(pair)
        return amount * price if price is not None else 0.0

    def _normalize_asset(self, asset: str) -> str:
        return asset.replace("Z", "", 1) if asset.startswith("Z") else asset.replace("X", "", 1)

    @staticmethod
    def _normalize_trade_payload(trade: Dict) -> Dict:
        serializable = {**trade}
        for key, value in list(serializable.items()):
            if isinstance(value, (dict, list)):
                serializable[key] = value
        return serializable

    def get_positions(self) -> List[SpotPosition]:
        return list(self.positions.values())

    def get_asset_exposure(self) -> List[AssetExposure]:
        equity = self.equity_view()
        exposures: List[AssetExposure] = []
        for asset, balance in self.balances.items():
            value_base = self._convert_to_base_currency(balance.total, asset)
            pct = (value_base / equity.equity_base) if equity.equity_base else 0.0
            exposures.append(
                AssetExposure(
                    asset=asset,
                    amount=balance.total,
                    value_base=value_base,
                    percentage_of_equity=pct,
                )
            )
        return exposures

