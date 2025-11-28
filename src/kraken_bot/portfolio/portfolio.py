"""In-memory portfolio tracking and persistence helpers.

The :class:`Portfolio` class encapsulates portfolio state management backed by
an arbitrary :class:`~kraken_bot.portfolio.store.PortfolioStore`.  It is
designed to be lightweight so higher level services can delegate bookkeeping
without duplicating logic.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass
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


@dataclass
class ConversionResult:
    value_base: float
    source_pair: Optional[str]
    status: str


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

        strategy_tag, raw_userref, comment = self._extract_trade_tags(trade)

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
                strategy_tag=strategy_tag,
                raw_userref=raw_userref,
                comment=comment,
            )
            self.positions[pair] = position

        fee_conversion = self._convert_to_base_currency(fee, quote_asset)
        fee_in_base = fee_conversion.value_base
        position.fees_paid_base += fee_in_base
        self.fees_paid_base_by_pair[pair] += fee_in_base

        if side == "buy":
            previous_cost = position.base_size * position.avg_entry_price
            new_total_qty = position.base_size + vol
            position.avg_entry_price = (previous_cost + cost) / new_total_qty if new_total_qty else 0.0
            position.base_size = new_total_qty
        else:
            gross_pnl_quote = (price - position.avg_entry_price) * vol
            pnl_conversion = self._convert_to_base_currency(gross_pnl_quote, quote_asset)
            pnl_base = pnl_conversion.value_base - fee_in_base
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
                    strategy_tag=strategy_tag,
                    raw_userref=raw_userref,
                    comment=comment,
                )
            )

        position.strategy_tag = strategy_tag
        position.raw_userref = raw_userref
        position.comment = comment

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
            diff_value = self._convert_to_base_currency(diff_qty, asset).value_base
            if diff_value > self.config.reconciliation_tolerance:
                drift_detected = True
                break

        self.drift_flag = drift_detected
        return drift_detected

    def equity_view(self, include_manual: Optional[bool] = None) -> EquityView:
        include_manual = self._should_include_manual(include_manual)
        equity = 0.0
        cash = 0.0
        unrealized = 0.0
        unvalued_assets: List[str] = []

        for asset, balance in self.balances.items():
            conversion = self._convert_to_base_currency(balance.total, asset)
            value_base = conversion.value_base
            equity += value_base
            if conversion.status == "unvalued":
                unvalued_assets.append(asset)
            if asset == self.config.base_currency:
                cash += value_base

        for position in self.positions.values():
            if not include_manual and self._is_manual_tag(position.strategy_tag):
                continue
            current_price = self.market_data.get_latest_price(position.pair)
            if current_price is None:
                continue
            current_val = position.base_size * current_price
            cost_basis = position.base_size * position.avg_entry_price
            position.current_value_base = current_val
            position.unrealized_pnl_base = current_val - cost_basis
            unrealized += position.unrealized_pnl_base

        realized_by_pair = self._filtered_realized_pnl(include_manual)

        return EquityView(
            equity_base=equity,
            cash_base=cash,
            realized_pnl_base_total=sum(realized_by_pair.values()),
            unrealized_pnl_base_total=unrealized,
            drift_flag=self.drift_flag,
            unvalued_assets=unvalued_assets,
        )

    def snapshot(self, now: Optional[int] = None, persist: bool = True, enforce_retention: bool = True) -> PortfolioSnapshot:
        now = now or int(time.time())
        equity = self.equity_view()

        valuations: List[AssetValuation] = []
        for asset, balance in self.balances.items():
            if not self._is_asset_included(asset):
                continue
            conversion = self._convert_to_base_currency(balance.total, asset)
            valuations.append(
                AssetValuation(
                    asset=asset,
                    amount=balance.total,
                    value_base=conversion.value_base,
                    source_pair=conversion.source_pair
                    if asset != self.config.base_currency
                    else None,
                    valuation_status=conversion.status,
                )
            )

        snapshot = PortfolioSnapshot(
            timestamp=now,
            equity_base=equity.equity_base,
            cash_base=equity.cash_base,
            asset_valuations=valuations,
            realized_pnl_base_total=equity.realized_pnl_base_total,
            unrealized_pnl_base_total=equity.unrealized_pnl_base_total,
            realized_pnl_base_by_pair=self._filtered_realized_pnl(self._should_include_manual(None)),
            unrealized_pnl_base_by_pair={
                p.pair: p.unrealized_pnl_base
                for p in self.positions.values()
                if self._should_include_manual(None) or not self._is_manual_tag(p.strategy_tag)
            },
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
    def _should_include_manual(self, include_manual: Optional[bool]) -> bool:
        return self.config.track_manual_trades if include_manual is None else include_manual

    def _extract_trade_tags(self, trade: Dict) -> tuple[str, Optional[str], Optional[str]]:
        raw_userref = str(trade["userref"]) if trade.get("userref") is not None else None
        comment = str(trade["comment"]) if trade.get("comment") else None
        if trade.get("strategy_tag"):
            strategy_tag = str(trade["strategy_tag"])
        elif raw_userref is not None:
            strategy_tag = raw_userref
        elif comment is not None:
            strategy_tag = comment
        else:
            strategy_tag = "manual"

        return strategy_tag, raw_userref, comment

    @staticmethod
    def _is_manual_tag(strategy_tag: Optional[str]) -> bool:
        return not strategy_tag or strategy_tag == "manual"

    def _is_asset_included(self, asset: str) -> bool:
        if self.config.include_assets and asset not in self.config.include_assets:
            return False
        if self.config.exclude_assets and asset in self.config.exclude_assets:
            return False
        return True

    def _convert_to_base_currency(self, amount: float, asset: str) -> ConversionResult:
        if amount == 0 or not asset:
            return ConversionResult(0.0, None, "valued")
        if not self._is_asset_included(asset):
            return ConversionResult(0.0, None, "excluded")
        if asset == self.config.base_currency:
            return ConversionResult(amount, None, "valued")

        pair = self.config.valuation_pairs.get(asset) or f"{asset}{self.config.base_currency}"
        price = None
        try:
            price = self.market_data.get_latest_price(pair)
        except Exception:
            price = None

        if price is None:
            return ConversionResult(0.0, pair, "unvalued")
        return ConversionResult(amount * price, pair, "valued")

    def _normalize_asset(self, asset: str) -> str:
        return asset.replace("Z", "", 1) if asset.startswith("Z") else asset.replace("X", "", 1)

    @staticmethod
    def _normalize_trade_payload(trade: Dict) -> Dict:
        serializable = {**trade}
        for key, value in list(serializable.items()):
            if isinstance(value, (dict, list)):
                serializable[key] = value
        return serializable

    def get_position(self, pair: str) -> Optional[SpotPosition]:
        return self.positions.get(pair)

    def get_positions(self) -> List[SpotPosition]:
        return list(self.positions.values())

    def get_trade_history(
        self,
        pair: Optional[str] = None,
        limit: Optional[int] = None,
        since: Optional[int] = None,
        until: Optional[int] = None,
        ascending: bool = False,
    ) -> List[Dict]:
        return self.store.get_trades(pair=pair, limit=limit, since=since, until=until, ascending=ascending)

    def get_cash_flows(
        self,
        asset: Optional[str] = None,
        limit: Optional[int] = None,
        since: Optional[int] = None,
        until: Optional[int] = None,
        ascending: bool = False,
    ) -> List[CashFlowRecord]:
        return self.store.get_cash_flows(asset=asset, limit=limit, since=since, until=until, ascending=ascending)

    def get_fee_summary(self) -> Dict[str, float]:
        return {
            "by_pair": dict(self.fees_paid_base_by_pair),
            "total_base": sum(self.fees_paid_base_by_pair.values()),
        }

    def get_snapshots(self, since: Optional[int] = None, limit: Optional[int] = None) -> List[PortfolioSnapshot]:
        return self.store.get_snapshots(since=since, limit=limit)

    def get_latest_snapshot(self) -> Optional[PortfolioSnapshot]:
        snapshots = self.get_snapshots(limit=1)
        return snapshots[0] if snapshots else None

    def get_asset_exposure(self) -> List[AssetExposure]:
        equity = self.equity_view()
        exposures: List[AssetExposure] = []
        for asset, balance in self.balances.items():
            if not self._is_asset_included(asset):
                continue
            conversion = self._convert_to_base_currency(balance.total, asset)
            value_base = conversion.value_base
            pct = (value_base / equity.equity_base) if equity.equity_base else 0.0
            exposures.append(
                AssetExposure(
                    asset=asset,
                    amount=balance.total,
                    value_base=value_base,
                    percentage_of_equity=pct,
                    valuation_status=conversion.status,
                )
            )
        return exposures

    def _filtered_realized_pnl(self, include_manual: bool) -> Dict[str, float]:
        realized_by_pair: Dict[str, float] = defaultdict(float)
        for record in self.realized_pnl_history:
            if include_manual or not self._is_manual_tag(record.strategy_tag):
                realized_by_pair[record.pair] += record.pnl_quote
        if not realized_by_pair:
            return {}
        return dict(realized_by_pair)

