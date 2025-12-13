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
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import (
    DefaultDict,
    Dict,
    Iterable,
    List,
    Optional,
    Sequence,
    Tuple,
    Union,
    cast,
)

from kraken_bot.config import PortfolioConfig
from kraken_bot.market_data.api import MarketDataAPI
from kraken_bot.portfolio.models import (
    AssetBalance,
    AssetExposure,
    AssetValuation,
    CashFlowRecord,
    DriftMismatchedAsset,
    DriftStatus,
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
        strategy_tags: Optional[Dict[str, str]] = None,
        userref_to_strategy: Optional[Dict[str, str]] = None,
    ):
        self.config = config
        self.market_data = market_data
        self.store = store
        self.snapshot_interval_seconds = snapshot_interval_seconds
        self.strategy_tags = strategy_tags or {}
        self.userref_to_strategy = userref_to_strategy or {}

        self.balances: Dict[str, AssetBalance] = {}
        self.positions: Dict[str, SpotPosition] = {}
        self.realized_pnl_history: List[RealizedPnLRecord] = []
        self.realized_pnl_base_by_pair: Dict[str, float] = defaultdict(float)
        self.fees_paid_base_by_pair: Dict[str, float] = defaultdict(float)
        self.drift_flag: bool = False
        self.drift_status = DriftStatus(
            drift_flag=False,
            expected_position_value_base=0.0,
            actual_balance_value_base=0.0,
            tolerance_base=self.config.reconciliation_tolerance,
            mismatched_assets=[],
        )
        self._last_snapshot_ts: int = 0

    def _round_vol(self, pair: str, vol: float) -> float:
        """Round volume to the pair's configured lot decimals using ROUND_FLOOR."""
        try:
            meta = self.market_data.get_pair_metadata(pair)
            d_vol = Decimal(str(vol))
            quantizer = Decimal("1." + "0" * meta.volume_decimals)
            return float(d_vol.quantize(quantizer, rounding=ROUND_FLOOR))
        except Exception:
            # Fallback for missing metadata:
            # If it's effectively zero (float error), snap it.
            if vol < 1e-9:
                return 0.0
            return vol

    def _round_price(self, pair: str, price: float) -> float:
        """Round price to the pair's configured price decimals using ROUND_HALF_UP."""
        try:
            meta = self.market_data.get_pair_metadata(pair)
            d_price = Decimal(str(price))
            quantizer = Decimal("1." + "0" * meta.price_decimals)
            return float(d_price.quantize(quantizer, rounding=ROUND_HALF_UP))
        except Exception:
            return price

    # ------------------------------------------------------------------
    # Ingestion helpers
    # ------------------------------------------------------------------
    def ingest_trades(self, trades: Iterable[Dict], persist: bool = True):
        """Process and optionally persist a collection of raw trades."""

        trades_sorted = sorted(trades, key=lambda t: t.get("time", 0))
        if persist:
            self.store.save_trades(
                [self._normalize_trade_payload(t) for t in trades_sorted]
            )

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

            # Use MarketDataAPI to normalize the asset code
            raw_asset = entry.get("asset", "")
            normalized_asset = self.market_data.normalize_asset(raw_asset)

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
        # Normalize assets to ensure positions are keyed by the canonical symbol (e.g. DOGE not XDG)
        base_asset = self.market_data.normalize_asset(pair_meta.base)
        quote_asset = self.market_data.normalize_asset(pair_meta.quote)

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
            new_total_qty = self._round_vol(pair, position.base_size + vol)
            position.avg_entry_price = (
                self._round_price(pair, (previous_cost + cost) / new_total_qty)
                if new_total_qty
                else 0.0
            )
            position.base_size = new_total_qty

            # Fees on BUY are realized immediately as a loss.
            # We must subtract this fee from realized PnL to maintain Equity = PnL + Cash + AssetValue.
            position.realized_pnl_base -= fee_in_base
            self.realized_pnl_base_by_pair[pair] -= fee_in_base

            self.realized_pnl_history.append(
                RealizedPnLRecord(
                    trade_id=trade.get("id", ""),
                    order_id=trade.get("ordertxid"),
                    pair=pair,
                    time=int(trade.get("time", 0)),
                    side=side,
                    base_delta=vol,
                    quote_delta=-cost,
                    fee_asset=quote_asset,
                    fee_amount=fee,
                    pnl_quote=-fee_in_base,
                    strategy_tag=strategy_tag,
                    raw_userref=raw_userref,
                    comment=comment,
                )
            )
        else:
            gross_pnl_quote = (price - position.avg_entry_price) * vol
            pnl_conversion = self._convert_to_base_currency(
                gross_pnl_quote, quote_asset
            )
            pnl_base = pnl_conversion.value_base - fee_in_base
            position.realized_pnl_base += pnl_base
            self.realized_pnl_base_by_pair[pair] += pnl_base

            # Use max(0.0, ...) combined with _round_vol to safely close positions
            raw_new_size = max(0.0, position.base_size - vol)
            position.base_size = self._round_vol(pair, raw_new_size)

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
        balance = self.balances.get(
            record.asset, AssetBalance(record.asset, 0.0, 0.0, 0.0)
        )
        balance.total += record.amount
        balance.free += record.amount
        self.balances[record.asset] = balance

    def apply_cash_flow(self, record: CashFlowRecord) -> None:
        """Apply a persisted cash flow record to rebuild portfolio balances."""

        self._process_cash_flow(record)

    def apply_cash_flows(self, records: Sequence[CashFlowRecord]) -> None:
        """Apply multiple cash flow records to rebuild balances."""

        for record in records:
            self.apply_cash_flow(record)

    # ------------------------------------------------------------------
    # Equity & reconciliation
    # ------------------------------------------------------------------
    def reconcile(self, live_balances: Dict[str, str]) -> bool:
        """Reconcile live balances and flag drift based on the configured tolerance.

        This method compares the local ledger-based balances (self.balances) with
        the live balances reported by the API. It also compares the positions (from trades)
        against the local balances.
        """

        # 1. Compare Ledger (self.balances) vs Live (live_balances)
        live_assets = set()
        drift_detected = False
        mismatched_assets: List[DriftMismatchedAsset] = []

        # We use a tolerance for comparison
        tolerance_base = self.config.reconciliation_tolerance

        # Helper to convert drift to base currency
        def to_base(amt, asset):
            return self._convert_to_base_currency(amt, asset)

        for asset_raw, amount_str in live_balances.items():
            asset = self._normalize_asset(asset_raw)
            live_assets.add(asset)

            live_qty = float(amount_str)

            # Local ledger balance
            local_bal = self.balances.get(
                asset, AssetBalance(asset, 0.0, 0.0, 0.0)
            ).total

            diff_qty = abs(live_qty - local_bal)
            conversion = to_base(diff_qty, asset)
            diff_val = conversion.value_base

            # Drift if value > tolerance OR unvalued but non-zero quantity mismatch
            if diff_val > tolerance_base:
                drift_detected = True
                mismatched_assets.append(
                    DriftMismatchedAsset(
                        asset=asset,
                        expected_quantity=local_bal,
                        actual_quantity=live_qty,
                        difference_base=diff_val,
                    )
                )
            elif conversion.status == "unvalued" and diff_qty > 1e-9:
                # Treat unvalued quantity mismatch as drift
                drift_detected = True
                mismatched_assets.append(
                    DriftMismatchedAsset(
                        asset=asset,
                        expected_quantity=local_bal,
                        actual_quantity=live_qty,
                        difference_base=0.0,  # Cannot value it
                    )
                )

        # Check for assets in local but not in live (implying 0 live)
        for asset, bal in self.balances.items():
            if asset not in live_assets and bal.total > 0:
                # Check value
                conversion = to_base(bal.total, asset)
                val = conversion.value_base

                if val > tolerance_base:
                    drift_detected = True
                    mismatched_assets.append(
                        DriftMismatchedAsset(
                            asset=asset,
                            expected_quantity=bal.total,
                            actual_quantity=0.0,
                            difference_base=val,
                        )
                    )
                elif conversion.status == "unvalued" and bal.total > 1e-9:
                    drift_detected = True
                    mismatched_assets.append(
                        DriftMismatchedAsset(
                            asset=asset,
                            expected_quantity=bal.total,
                            actual_quantity=0.0,
                            difference_base=0.0,
                        )
                    )

        # 2. Compare Positions (Trades) vs Ledger Balances (self.balances)
        # This is the "internal consistency" check.

        position_totals: DefaultDict[str, float] = defaultdict(float)
        for position in self.positions.values():
            position_totals[position.base_asset] += position.base_size

        expected_position_value_base = 0.0
        actual_balance_value_base = 0.0

        for asset, pos_total in position_totals.items():
            balance_total = self.balances.get(
                asset, AssetBalance(asset, 0.0, 0.0, 0.0)
            ).total

            diff_qty = abs(pos_total - balance_total)
            conversion = to_base(diff_qty, asset)
            diff_value = conversion.value_base

            pos_val = to_base(pos_total, asset).value_base
            bal_val = to_base(balance_total, asset).value_base
            expected_position_value_base += pos_val
            actual_balance_value_base += bal_val

            if diff_value > tolerance_base:
                drift_detected = True
                mismatched_assets.append(
                    DriftMismatchedAsset(
                        asset=asset,
                        expected_quantity=pos_total,
                        actual_quantity=balance_total,
                        difference_base=diff_value,
                    )
                )
            elif conversion.status == "unvalued" and diff_qty > 1e-9:
                 drift_detected = True
                 mismatched_assets.append(
                    DriftMismatchedAsset(
                        asset=asset,
                        expected_quantity=pos_total,
                        actual_quantity=balance_total,
                        difference_base=0.0, # Unvalued
                    )
                )

        self.drift_flag = drift_detected
        self.drift_status = DriftStatus(
            drift_flag=drift_detected,
            expected_position_value_base=expected_position_value_base,
            actual_balance_value_base=actual_balance_value_base,
            tolerance_base=tolerance_base,
            mismatched_assets=mismatched_assets,
        )
        return drift_detected

    def get_drift_status(self) -> DriftStatus:
        return self.drift_status

    def equity_view(self, include_manual: Optional[bool] = None) -> EquityView:
        include_manual = self._should_include_manual(include_manual)
        equity = 0.0
        cash = 0.0
        unrealized = 0.0
        price_drift = False
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
            current_price, used_fallback = self._get_position_price(position.pair)
            if current_price is None:
                # No available pricing; fall back to cost basis but flag drift
                price_drift = True
                current_price = position.avg_entry_price
            elif used_fallback:
                price_drift = True

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
            drift_flag=self.drift_flag or price_drift,
            unvalued_assets=unvalued_assets,
        )

    def snapshot(
        self,
        now: Optional[int] = None,
        persist: bool = True,
        enforce_retention: bool = True,
    ) -> PortfolioSnapshot:
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
                    source_pair=(
                        conversion.source_pair
                        if asset != self.config.base_currency
                        else None
                    ),
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
            realized_pnl_base_by_pair=self._filtered_realized_pnl(
                self._should_include_manual(None)
            ),
            unrealized_pnl_base_by_pair={
                p.pair: p.unrealized_pnl_base
                for p in self.positions.values()
                if self._should_include_manual(None)
                or not self._is_manual_tag(p.strategy_tag)
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
        return (
            self.config.track_manual_trades
            if include_manual is None
            else include_manual
        )

    def _extract_trade_tags(
        self, trade: Dict
    ) -> tuple[Optional[str], Optional[str], Optional[str]]:
        raw_userref = (
            str(trade["userref"]) if trade.get("userref") is not None else None
        )
        comment = str(trade["comment"]) if trade.get("comment") else None
        strategy_tag: Optional[str] = None

        if trade.get("strategy_tag"):
            strategy_tag = str(trade["strategy_tag"])

        if strategy_tag is None and raw_userref is not None:
            strategy_tag = self._strategy_from_userref(raw_userref)

        if (
            strategy_tag is None
            and comment is not None
            and comment in self.strategy_tags
        ):
            strategy_tag = self.strategy_tags[comment]

        return strategy_tag, raw_userref, comment

    def _strategy_from_userref(self, raw_userref: str) -> Optional[str]:
        userref_key = str(raw_userref)
        if userref_key in self.userref_to_strategy:
            return self.userref_to_strategy[userref_key]

        if userref_key in self.strategy_tags:
            return userref_key

        if ":" in userref_key:
            prefix = userref_key.split(":", 1)[0]
            if prefix in self.userref_to_strategy:
                return self.userref_to_strategy[prefix]
            if prefix in self.strategy_tags:
                return prefix

        return None

    @staticmethod
    def _is_manual_tag(strategy_tag: Optional[str]) -> bool:
        return not strategy_tag or strategy_tag == "manual"

    def _is_asset_included(self, asset: str) -> bool:
        if self.config.include_assets and asset not in self.config.include_assets:
            return False
        if self.config.exclude_assets and asset in self.config.exclude_assets:
            return False
        return True

    def _get_position_price(self, pair: str) -> Tuple[Optional[float], bool]:
        """Return latest or fallback price and whether a fallback was used."""

        try:
            live_price = self.market_data.get_latest_price(pair)
        except Exception:
            live_price = None

        if live_price is not None:
            return float(live_price), False

        fallback_price = self._get_fallback_price(pair)
        return (fallback_price, True) if fallback_price is not None else (None, True)

    def _get_fallback_price(self, pair: str) -> Optional[float]:
        """Attempt to retrieve a non-live price from stored OHLC bars."""
        # Note: We rely on public methods only.
        # This fallback is strictly for VALUATION (equity approximation),
        # not for active trading decisions.

        cfg = getattr(self.market_data, "_config", None)
        timeframes: List[str] = []
        if cfg and getattr(cfg, "market_data", None):
            timeframes.extend(getattr(cfg.market_data, "ws_timeframes", []))
            timeframes.extend(getattr(cfg.market_data, "backfill_timeframes", []))

        if not timeframes:
            timeframes = ["1m", "5m", "15m"]

        # Use the public get_ohlc method
        get_ohlc = getattr(self.market_data, "get_ohlc", None)
        if callable(get_ohlc):
            for timeframe in dict.fromkeys(timeframes):
                try:
                    bars = get_ohlc(pair, timeframe, 1)
                    if isinstance(bars, Sequence) and bars:
                        last_bar = bars[-1]
                        return float(last_bar.close)
                except Exception:
                    continue

        return None

    def _convert_to_base_currency(self, amount: float, asset: str) -> ConversionResult:
        if amount == 0 or not asset:
            return ConversionResult(0.0, None, "valued")
        if not self._is_asset_included(asset):
            return ConversionResult(0.0, None, "excluded")

        # IMPORTANT: base currency is always 1:1
        if asset == self.config.base_currency:
            return ConversionResult(amount, None, "valued")

        # Use MarketDataAPI to get valuation pair
        pair = self.config.valuation_pairs.get(asset) or self.market_data.get_valuation_pair(asset)

        # If still no pair, return unvalued.
        if not pair:
             return ConversionResult(0.0, None, "unvalued")

        price = None
        try:
            price = self.market_data.get_latest_price(pair)
        except Exception:
            price = None

        if price is None:
            return ConversionResult(0.0, pair, "unvalued")
        return ConversionResult(amount * price, pair, "valued")

    def _normalize_asset(self, asset: str) -> str:
        """Deprecated: delegating to MarketDataAPI."""
        return self.market_data.normalize_asset(asset)

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
        return self.store.get_trades(
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
        return self.store.get_cash_flows(
            asset=asset, limit=limit, since=since, until=until, ascending=ascending
        )

    def get_fee_summary(self) -> Dict[str, Union[Dict[str, float], float]]:
        return {
            "by_pair": dict(self.fees_paid_base_by_pair),
            "total_base": sum(self.fees_paid_base_by_pair.values()),
        }

    def get_snapshots(
        self, since: Optional[int] = None, limit: Optional[int] = None
    ) -> List[PortfolioSnapshot]:
        return self.store.get_snapshots(since=since, limit=limit)

    def get_latest_snapshot(self) -> Optional[PortfolioSnapshot]:
        snapshots = self.get_snapshots(limit=1)
        return snapshots[0] if snapshots else None

    def get_asset_exposure(
        self, include_manual: Optional[bool] = None
    ) -> List[AssetExposure]:
        equity = self.equity_view(include_manual=include_manual)
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

    def _realized_pnl_by_strategy(self, include_manual: bool) -> Dict[str, float]:
        realized_by_strategy: Dict[str, float] = defaultdict(float)
        for record in self.realized_pnl_history:
            if self._is_manual_tag(record.strategy_tag):
                if not include_manual:
                    continue
                strategy_key = "manual"
            else:
                strategy_key = cast(str, record.strategy_tag)

            realized_by_strategy[strategy_key] += record.pnl_quote

        return dict(realized_by_strategy)

    def get_realized_pnl_by_strategy(
        self, include_manual: Optional[bool] = None
    ) -> Dict[str, float]:
        include_manual = self._should_include_manual(include_manual)
        return self._realized_pnl_by_strategy(include_manual)
