"""Lightweight backtest runner built on the live strategy engine."""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from krakked.config import AppConfig
from krakked.connection.rest_client import KrakenRESTClient
from krakked.execution.adapter import SimulationExecutionAdapter
from krakked.execution.models import ExecutionResult, LocalOrder
from krakked.execution.oms import ExecutionService
from krakked.execution.router import apply_slippage, round_order_price
from krakked.market_data.api import ASSET_ALIASES, MarketDataAPI
from krakked.market_data.metadata_store import PairMetadataStore
from krakked.market_data.models import ConnectionStatus, OHLCBar, PairMetadata
from krakked.market_data.ohlc_store import FileOHLCStore
from krakked.portfolio.manager import PortfolioService
from krakked.portfolio.models import AssetBalance
from krakked.strategy.engine import StrategyEngine
from krakked.strategy.models import ExecutionPlan

logger = logging.getLogger(__name__)

_TIMEFRAME_UNIT_SECONDS = {
    "m": 60,
    "h": 60 * 60,
    "d": 24 * 60 * 60,
}


def _timeframe_seconds(timeframe: str) -> int:
    normalized = str(timeframe or "").strip().lower()
    if len(normalized) < 2:
        return 0

    unit = normalized[-1]
    multiplier = _TIMEFRAME_UNIT_SECONDS.get(unit)
    if multiplier is None:
        return 0

    try:
        magnitude = int(normalized[:-1])
    except ValueError:
        return 0

    return magnitude * multiplier


@dataclass
class BacktestCoverageItem:
    pair: str
    timeframe: str
    bar_count: int
    first_bar_at: Optional[datetime]
    last_bar_at: Optional[datetime]
    status: str

    @property
    def series_key(self) -> str:
        return f"{self.pair}@{self.timeframe}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pair": self.pair,
            "timeframe": self.timeframe,
            "bar_count": self.bar_count,
            "first_bar_at": (
                self.first_bar_at.astimezone(UTC).isoformat()
                if self.first_bar_at is not None
                else None
            ),
            "last_bar_at": (
                self.last_bar_at.astimezone(UTC).isoformat()
                if self.last_bar_at is not None
                else None
            ),
            "status": self.status,
        }


@dataclass
class BacktestPreflight:
    coverage: List[BacktestCoverageItem] = field(default_factory=list)
    usable_series_count: int = 0
    missing_series: List[str] = field(default_factory=list)
    partial_series: List[str] = field(default_factory=list)
    status: str = "ready"
    summary_note: str = ""
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "coverage": [item.to_dict() for item in self.coverage],
            "usable_series_count": self.usable_series_count,
            "missing_series": list(self.missing_series),
            "partial_series": list(self.partial_series),
            "status": self.status,
            "summary_note": self.summary_note,
            "warnings": list(self.warnings),
        }


@dataclass
class BacktestSummary:
    start: datetime
    end: datetime
    starting_cash_usd: float
    ending_equity_usd: float
    pairs: List[str]
    timeframes: List[str]
    total_cycles: int = 0
    total_actions: int = 0
    blocked_actions: int = 0
    total_orders: int = 0
    filled_orders: int = 0
    rejected_orders: int = 0
    execution_errors: int = 0
    absolute_pnl_usd: float = 0.0
    return_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    realized_pnl_usd: float = 0.0
    unrealized_pnl_usd: float = 0.0
    fee_bps: float = 0.0
    slippage_bps: float = 0.0
    cost_model: str = ""
    usable_series_count: int = 0
    missing_series: List[str] = field(default_factory=list)
    partial_series: List[str] = field(default_factory=list)
    coverage: List[BacktestCoverageItem] = field(default_factory=list)
    blocked_reason_counts: Dict[str, int] = field(default_factory=dict)
    per_strategy: Dict[str, Dict[str, float | int]] = field(default_factory=dict)
    trust_level: str = ""
    trust_note: str = ""
    notable_warnings: List[str] = field(default_factory=list)
    replay_inputs: Dict[str, Any] = field(default_factory=dict)
    assumptions: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "start": self.start.astimezone(UTC).isoformat(),
            "end": self.end.astimezone(UTC).isoformat(),
            "starting_cash_usd": self.starting_cash_usd,
            "ending_equity_usd": self.ending_equity_usd,
            "absolute_pnl_usd": self.absolute_pnl_usd,
            "return_pct": self.return_pct,
            "max_drawdown_pct": self.max_drawdown_pct,
            "realized_pnl_usd": self.realized_pnl_usd,
            "unrealized_pnl_usd": self.unrealized_pnl_usd,
            "pairs": list(self.pairs),
            "timeframes": list(self.timeframes),
            "total_cycles": self.total_cycles,
            "total_actions": self.total_actions,
            "blocked_actions": self.blocked_actions,
            "total_orders": self.total_orders,
            "filled_orders": self.filled_orders,
            "rejected_orders": self.rejected_orders,
            "execution_errors": self.execution_errors,
            "fee_bps": self.fee_bps,
            "slippage_bps": self.slippage_bps,
            "cost_model": self.cost_model,
            "usable_series_count": self.usable_series_count,
            "missing_series": list(self.missing_series),
            "partial_series": list(self.partial_series),
            "coverage": [item.to_dict() for item in self.coverage],
            "blocked_reason_counts": dict(self.blocked_reason_counts),
            "per_strategy": copy.deepcopy(self.per_strategy),
            "trust_level": self.trust_level,
            "trust_note": self.trust_note,
            "notable_warnings": list(self.notable_warnings),
            "replay_inputs": copy.deepcopy(self.replay_inputs),
            "assumptions": list(self.assumptions),
        }


@dataclass
class BacktestPreflightResult:
    start: datetime
    end: datetime
    pairs: List[str]
    timeframes: List[str]
    preflight: BacktestPreflight

    def to_dict(self) -> Dict[str, Any]:
        return {
            "start": self.start.astimezone(UTC).isoformat(),
            "end": self.end.astimezone(UTC).isoformat(),
            "pairs": list(self.pairs),
            "timeframes": list(self.timeframes),
            "preflight": self.preflight.to_dict(),
        }


@dataclass
class BacktestResult:
    plans: List[ExecutionPlan]
    executions: List[ExecutionResult]
    preflight: Optional[BacktestPreflight] = None
    summary: Optional[BacktestSummary] = None

    def to_report_dict(self) -> Dict[str, Any]:
        return {
            "report_version": 1,
            "generated_at": datetime.now(UTC).isoformat(),
            "summary": self.summary.to_dict() if self.summary is not None else None,
            "preflight": (
                self.preflight.to_dict() if self.preflight is not None else None
            ),
        }


class BacktestPortfolioService(PortfolioService):
    """Portfolio service variant that skips remote sync for offline runs."""

    def __init__(
        self,
        config: AppConfig,
        market_data: MarketDataAPI,
        db_path: str,
        *,
        starting_cash_usd: float,
    ):
        super().__init__(config, market_data, db_path=db_path)
        self._starting_cash_usd = float(starting_cash_usd)
        self._seed_starting_cash()
        self._baseline_source = "simulation_wallet"
        self._refresh_cached_views()

    def _seed_starting_cash(self) -> None:
        base_currency = getattr(self.config, "base_currency", "USD")
        self.portfolio.balances[base_currency] = AssetBalance(
            asset=base_currency,
            free=self._starting_cash_usd,
            reserved=0.0,
            total=self._starting_cash_usd,
        )

    def sync(self) -> Dict[str, int]:  # pragma: no cover - smoke tested via runner
        self._refresh_cached_views()
        self._last_sync_ok = True
        self._last_sync_reason = None
        self._last_sync_at = datetime.now(UTC)
        return {"new_trades": 0, "new_cash_flows": 0}

    def ingest_simulated_trades(self, trades: List[Dict[str, Any]]) -> None:
        if not trades:
            return
        for trade in trades:
            self._apply_simulated_trade_balances(trade)
        self.portfolio.ingest_trades(trades, persist=True)
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


class BacktestMarketData(MarketDataAPI):
    """Market data facade that replays stored OHLC bars without network access."""

    def __init__(
        self,
        config: AppConfig,
        pairs: Iterable[str],
        timeframes: Iterable[str],
        start: datetime,
        end: datetime,
    ):
        super().__init__(config, rest_client=None, rate_limiter=None)
        self._rest_client: Optional[KrakenRESTClient] = None
        self._ws_client: Optional[Any] = None
        self._ohlc_store = FileOHLCStore(config.market_data)

        self._timeframes = list(timeframes)
        self._start_ts = int(start.replace(tzinfo=UTC).timestamp())
        self._end_ts = int(end.replace(tzinfo=UTC).timestamp())
        self._current_time = start.replace(tzinfo=UTC)
        self._alias_map = {}
        self._asset_map = {}
        self._valuation_map = {"USD": "USD"}

        cached_metadata = self._load_cached_metadata()
        self._universe = [
            self._resolve_pair_metadata(p, cached_metadata) for p in pairs
        ]
        self._universe_map = {p.canonical: p for p in self._universe}
        self._index_pair_metadata(self._universe)

        self._bar_cache: Dict[Tuple[str, str], List[OHLCBar]] = {}
        coverage: List[BacktestCoverageItem] = []
        for pair_meta in self._universe:
            for timeframe in self._timeframes:
                bars = self._ohlc_store.get_bars(
                    pair_meta.canonical, timeframe, lookback=1_000_000
                )
                bounded = [
                    bar
                    for bar in bars
                    if self._start_ts <= int(bar.timestamp) <= self._end_ts
                ]
                self._bar_cache[(pair_meta.canonical, timeframe)] = bounded
                if not bounded:
                    status = "missing"
                    first_bar_at = None
                    last_bar_at = None
                else:
                    first_bar_at = datetime.fromtimestamp(
                        int(bounded[0].timestamp), tz=UTC
                    )
                    last_bar_at = datetime.fromtimestamp(
                        int(bounded[-1].timestamp), tz=UTC
                    )
                    interval_seconds = _timeframe_seconds(timeframe)
                    coverage_end_ts = int(bounded[-1].timestamp) + interval_seconds
                    status = (
                        "partial_window"
                        if int(bounded[0].timestamp) > self._start_ts
                        or coverage_end_ts < self._end_ts
                        else "ok"
                    )
                coverage.append(
                    BacktestCoverageItem(
                        pair=pair_meta.ws_symbol,
                        timeframe=timeframe,
                        bar_count=len(bounded),
                        first_bar_at=first_bar_at,
                        last_bar_at=last_bar_at,
                        status=status,
                    )
                )

        timestamps: set[int] = set()
        for bars in self._bar_cache.values():
            timestamps.update(int(bar.timestamp) for bar in bars)
        self._timeline = sorted(
            ts for ts in timestamps if self._start_ts <= ts <= self._end_ts
        )
        self._coverage = coverage
        self._missing_series = [
            item.series_key for item in self._coverage if item.status == "missing"
        ]
        self._partial_series = [
            item.series_key
            for item in self._coverage
            if item.status == "partial_window"
        ]
        self._preflight = BacktestPreflight(
            coverage=list(self._coverage),
            usable_series_count=sum(
                1 for item in self._coverage if item.status != "missing"
            ),
            missing_series=list(self._missing_series),
            partial_series=list(self._partial_series),
            status="ready",
            summary_note="",
            warnings=[],
        )
        self._preflight = _assess_preflight(self._preflight)
        total_bars = sum(len(bars) for bars in self._bar_cache.values())
        logger.info(
            "Backtest market data ready with %s bars across %s pairs",
            total_bars,
            len(self._universe),
        )

    def _load_cached_metadata(self) -> List[PairMetadata]:
        metadata_path = getattr(self._metadata_store, "path", None)
        if metadata_path and metadata_path.exists():
            return self._metadata_store.load()
        return PairMetadataStore().load()

    def _build_pair_alias_index(
        self, metadata_items: Iterable[PairMetadata]
    ) -> Dict[str, PairMetadata]:
        alias_map: Dict[str, PairMetadata] = {}
        for item in metadata_items:
            candidates = {
                item.canonical.upper(),
                item.raw_name.upper(),
                item.rest_symbol.upper(),
                item.ws_symbol.upper(),
                item.canonical.upper().replace("/", ""),
                item.raw_name.upper().replace("/", ""),
                item.rest_symbol.upper().replace("/", ""),
                item.ws_symbol.upper().replace("/", ""),
            }
            for candidate in candidates:
                if candidate:
                    alias_map[candidate] = item
        return alias_map

    def _resolve_pair_metadata(
        self, symbol: str, metadata_items: Iterable[PairMetadata]
    ) -> PairMetadata:
        normalized = str(symbol).strip().upper()
        alias_map = self._build_pair_alias_index(metadata_items)

        direct = alias_map.get(normalized) or alias_map.get(normalized.replace("/", ""))
        if direct is not None:
            return direct

        if "/" in normalized:
            base, quote = normalized.split("/", 1)
            alias_base = ASSET_ALIASES.get(base, base)
            alias_quote = ASSET_ALIASES.get(quote, quote)
            candidates = [
                f"{alias_base}/{alias_quote}",
                f"{alias_base}{alias_quote}",
            ]
            for candidate in candidates:
                matched = alias_map.get(candidate)
                if matched is not None:
                    return matched

        return self._pair_metadata_from_symbol(symbol)

    def _index_pair_metadata(self, metadata_items: Iterable[PairMetadata]) -> None:
        self._alias_map = {}
        self._asset_map = {"USD": "USD", "ZUSD": "USD", "XBT": "XBT", "XXBT": "XBT"}
        self._valuation_map = {"USD": "USD"}

        for item in metadata_items:
            aliases = {
                item.canonical,
                item.raw_name,
                item.rest_symbol,
                item.ws_symbol,
                item.canonical.replace("/", ""),
                item.raw_name.replace("/", ""),
                item.rest_symbol.replace("/", ""),
                item.ws_symbol.replace("/", ""),
            }
            for alias in aliases:
                if alias:
                    self._alias_map[alias.upper()] = item

            if item.base:
                canonical_base = item.base
                if item.ws_symbol and "/" in item.ws_symbol:
                    canonical_base = item.ws_symbol.split("/", 1)[0]
                self._asset_map[item.base] = canonical_base
                self._asset_map[canonical_base] = canonical_base
                if item.quote == "USD":
                    self._valuation_map[canonical_base] = item.canonical
                    self._valuation_map[item.base] = item.canonical

    def _pair_metadata_from_symbol(self, symbol: str) -> PairMetadata:
        base, quote = symbol.split("/") if "/" in symbol else (symbol[:3], symbol[3:])
        return PairMetadata(
            canonical=symbol.replace("/", "").upper(),
            base=base,
            quote=quote,
            rest_symbol=symbol,
            ws_symbol=symbol,
            raw_name=symbol,
            price_decimals=8,
            volume_decimals=8,
            lot_size=1.0,
            min_order_size=0.0,
            status="online",
        )

    def set_time(self, now: datetime) -> None:
        self._current_time = now.replace(tzinfo=UTC)

    def iter_timestamps(self) -> Iterable[int]:
        return iter(self._timeline)

    def _filtered_bars(self, pair: str, timeframe: str) -> List[OHLCBar]:
        canonical = self.normalize_pair(pair)
        bars = self._bar_cache.get((canonical, timeframe), [])
        cutoff = (
            int(self._current_time.timestamp()) if self._current_time else self._end_ts
        )
        return [bar for bar in bars if int(bar.timestamp) <= cutoff]

    def get_ohlc(self, pair: str, timeframe: str, lookback: int) -> List[OHLCBar]:
        filtered = self._filtered_bars(pair, timeframe)
        return filtered[-lookback:]

    def get_ohlc_since(self, pair: str, timeframe: str, since_ts: int) -> List[OHLCBar]:
        filtered = self._filtered_bars(pair, timeframe)
        return [bar for bar in filtered if int(bar.timestamp) >= since_ts]

    def get_data_status(self) -> ConnectionStatus:
        return ConnectionStatus(
            rest_api_reachable=True,
            websocket_connected=True,
            streaming_pairs=len(self._universe),
            stale_pairs=0,
            subscription_errors=0,
        )

    def get_latest_price(self, pair: str) -> Optional[float]:
        for timeframe in self._timeframes:
            bars = self._filtered_bars(pair, timeframe)
            if bars:
                return float(bars[-1].close)
        return None

    def get_best_bid_ask(self, pair: str) -> Optional[Dict[str, float]]:
        latest_price = self.get_latest_price(pair)
        if latest_price is None:
            return None
        return {"bid": latest_price, "ask": latest_price}

    def get_missing_series(self) -> List[str]:
        return list(self._missing_series)

    def get_partial_series(self) -> List[str]:
        return list(self._partial_series)

    def get_preflight(self) -> BacktestPreflight:
        return BacktestPreflight(
            coverage=list(self._preflight.coverage),
            usable_series_count=self._preflight.usable_series_count,
            missing_series=list(self._preflight.missing_series),
            partial_series=list(self._preflight.partial_series),
            status=self._preflight.status,
            summary_note=self._preflight.summary_note,
            warnings=list(self._preflight.warnings),
        )


def _ingest_simulated_fills(
    execution: ExecutionResult,
    portfolio: BacktestPortfolioService,
    build_trade: Callable[[LocalOrder], Optional[Dict]],
) -> None:
    trades = []
    for order in execution.orders:
        if order.status != "filled":
            continue
        trade = build_trade(order)
        if trade:
            trades.append(trade)
    if trades:
        portfolio.ingest_simulated_trades(trades)


def _trade_from_order(order: LocalOrder) -> Optional[Dict]:
    return _trade_from_order_with_costs(order, fee_bps=0.0)


def _trade_from_order_with_costs(order: LocalOrder, fee_bps: float) -> Optional[Dict]:
    price = order.avg_fill_price or order.requested_price
    volume = order.cumulative_base_filled or order.requested_base_size
    if price is None or volume <= 0:
        return None
    notional = float(price) * float(volume)
    fee = notional * max(float(fee_bps), 0.0) / 10_000.0

    return {
        "id": f"sim-trade-{order.local_id}",
        "ordertxid": order.kraken_order_id or order.local_id,
        "pair": order.pair,
        "time": order.updated_at.timestamp(),
        "type": order.side,
        "ordertype": order.order_type,
        "price": float(price),
        "cost": notional,
        "fee": fee,
        "vol": float(volume),
        "margin": 0.0,
        "misc": "",
        "posstatus": None,
        "strategy_tag": order.strategy_id,
        "userref": order.userref,
    }


def _resolve_simulated_fill_price(
    order: LocalOrder,
    pair_metadata: PairMetadata,
    latest_price: Optional[float],
    config: AppConfig,
) -> Optional[float]:
    reference_price = latest_price if latest_price is not None else order.requested_price
    if reference_price is None:
        return None

    slippage_order = copy.copy(order)
    slippage_order.requested_price = float(reference_price)
    adjusted = apply_slippage(slippage_order, config.execution)
    if adjusted is None:
        return None
    return round_order_price(pair_metadata, float(adjusted))


def _fallback_preflight(market_data: Any) -> BacktestPreflight:
    get_missing_series = getattr(market_data, "get_missing_series", None)
    missing_series = list(get_missing_series()) if callable(get_missing_series) else []
    usable_series_count = 0 if missing_series else 1
    return _assess_preflight(
        BacktestPreflight(
        coverage=[],
        usable_series_count=usable_series_count,
        missing_series=missing_series,
        partial_series=[],
        )
    )


def _get_preflight(market_data: Any) -> BacktestPreflight:
    get_preflight = getattr(market_data, "get_preflight", None)
    if callable(get_preflight):
        return get_preflight()
    return _fallback_preflight(market_data)


def _assess_preflight(preflight: BacktestPreflight) -> BacktestPreflight:
    coverage = list(preflight.coverage)
    usable_items = [item for item in coverage if item.status != "missing"]
    missing_series = list(preflight.missing_series)
    partial_series = list(preflight.partial_series)
    warnings: List[str] = []

    if preflight.usable_series_count <= 0:
        status = "unusable"
        summary_note = "No usable historical series were found for the requested window."
    elif missing_series or partial_series:
        status = "limited"
        if missing_series:
            warnings.append(
                f"{len(missing_series)} requested series are missing from the local OHLC store."
            )
        if partial_series:
            warnings.append(
                f"{len(partial_series)} requested series only partially cover the requested window."
            )
        if usable_items and all(item.status == "partial_window" for item in usable_items):
            warnings.append(
                "All usable series are only partially covered for the requested window."
            )
            summary_note = (
                "Coverage is limited: every usable series is only partially covered."
            )
        else:
            summary_note = (
                "Coverage is limited: some requested series are missing or partial."
            )
    else:
        status = "ready"
        summary_note = "Coverage looks complete for the requested replay window."

    return BacktestPreflight(
        coverage=coverage,
        usable_series_count=preflight.usable_series_count,
        missing_series=missing_series,
        partial_series=partial_series,
        status=status,
        summary_note=summary_note,
        warnings=warnings,
    )


def build_backtest_preflight(
    config: AppConfig,
    start: datetime,
    end: datetime,
    timeframes: Optional[Iterable[str]] = None,
) -> BacktestPreflightResult:
    if end <= start:
        raise ValueError("Backtest end must be after start")

    config_copy = copy.deepcopy(config)
    frames = (
        list(timeframes) if timeframes else _default_backtest_timeframes(config_copy)
    )
    pairs = _configured_backtest_pairs(config_copy)
    market_data = BacktestMarketData(config_copy, pairs, frames, start, end)
    try:
        preflight = _get_preflight(market_data)
        get_universe_metadata = getattr(market_data, "get_universe_metadata", None)
        summary_pairs = (
            [pair_meta.ws_symbol for pair_meta in get_universe_metadata()]
            if callable(get_universe_metadata)
            else list(pairs)
        )
        return BacktestPreflightResult(
            start=start,
            end=end,
            pairs=summary_pairs,
            timeframes=list(frames),
            preflight=preflight,
        )
    finally:
        shutdown = getattr(market_data, "shutdown", None)
        if callable(shutdown):
            shutdown()


def run_backtest(
    config: AppConfig,
    start: datetime,
    end: datetime,
    timeframes: Optional[Iterable[str]] = None,
    *,
    starting_cash_usd: float = 10_000.0,
    fee_bps: float = 25.0,
    db_path: Optional[str] = None,
    strict_data: bool = False,
) -> BacktestResult:
    """Run the configured strategies across stored OHLC bars for the window."""

    if end <= start:
        raise ValueError("Backtest end must be after start")
    if starting_cash_usd <= 0:
        raise ValueError("starting_cash_usd must be greater than 0")
    if fee_bps < 0:
        raise ValueError("fee_bps must be greater than or equal to 0")

    config_copy = copy.deepcopy(config)
    config_copy.execution.mode = "simulation"
    config_copy.execution.validate_only = False
    config_copy.execution.allow_live_trading = False
    config_copy.execution.max_plan_age_seconds = 0

    frames = (
        list(timeframes) if timeframes else _default_backtest_timeframes(config_copy)
    )
    pairs = _configured_backtest_pairs(config_copy)

    market_data = BacktestMarketData(config_copy, pairs, frames, start, end)
    preflight = _get_preflight(market_data)
    if preflight.usable_series_count == 0:
        requested = ", ".join(
            f"{pair}@{timeframe}" for pair in pairs for timeframe in frames
        )
        raise ValueError(
            "No historical OHLC bars found for the requested replay window. "
            f"Checked: {requested}"
        )
    if strict_data and (preflight.missing_series or preflight.partial_series):
        details: List[str] = []
        if preflight.missing_series:
            details.append("missing: " + ", ".join(preflight.missing_series))
        if preflight.partial_series:
            details.append("partial: " + ", ".join(preflight.partial_series))
        raise ValueError(
            "Historical data coverage failed in strict mode: " + "; ".join(details)
        )
    if not list(market_data.iter_timestamps()):
        raise ValueError(
            "No replay timestamps were available inside the requested window."
        )

    temp_dir: Optional[TemporaryDirectory[str]] = None
    resolved_db_path: Optional[Path] = None
    if db_path:
        resolved_db_path = Path(db_path).expanduser().resolve()
        resolved_db_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        temp_dir = TemporaryDirectory(prefix="krakked-backtest-")
        resolved_db_path = Path(temp_dir.name) / "backtest.db"

    try:
        portfolio_service = BacktestPortfolioService(
            config_copy,
            market_data,
            db_path=str(resolved_db_path),
            starting_cash_usd=starting_cash_usd,
        )
        initial_snapshot = getattr(portfolio_service.portfolio, "snapshot", None)
        if callable(initial_snapshot):
            initial_snapshot(now=int(start.replace(tzinfo=UTC).timestamp()))

        strategy_engine = StrategyEngine(config_copy, market_data, portfolio_service)
        strategy_engine.initialize()

        execution_service = ExecutionService(
            adapter=SimulationExecutionAdapter(
                config=config_copy.execution,
                fill_price_resolver=lambda order, pair_metadata, latest_price: _resolve_simulated_fill_price(
                    order, pair_metadata, latest_price, config_copy
                ),
            ),
            store=portfolio_service.store,
            config=config_copy.execution,
            market_data=market_data,
            risk_status_provider=strategy_engine.get_risk_status,
        )

        plans: List[ExecutionPlan] = []
        executions: List[ExecutionResult] = []
        for ts in market_data.iter_timestamps():
            now = datetime.fromtimestamp(ts, tz=UTC)
            market_data.set_time(now)
            plan = strategy_engine.run_cycle(now=now)
            plans.append(plan)

            execution = execution_service.execute_plan(plan)
            executions.append(execution)

            _ingest_simulated_fills(
                execution,
                portfolio_service,
                lambda order: _trade_from_order_with_costs(order, fee_bps=fee_bps),
            )
            maybe_snapshot = getattr(
                portfolio_service.portfolio, "maybe_snapshot", None
            )
            if callable(maybe_snapshot):
                maybe_snapshot(now=ts)

        get_universe_metadata = getattr(market_data, "get_universe_metadata", None)
        summary_pairs = (
            [pair_meta.ws_symbol for pair_meta in get_universe_metadata()]
            if callable(get_universe_metadata)
            else list(pairs)
        )

        summary = _build_backtest_summary(
            start=start,
            end=end,
            pairs=summary_pairs,
            timeframes=frames,
            plans=plans,
            executions=executions,
            market_data=market_data,
            portfolio=portfolio_service,
            starting_cash_usd=starting_cash_usd,
            fee_bps=fee_bps,
            strict_data=strict_data,
            preflight=preflight,
        )
        return BacktestResult(
            plans=plans,
            executions=executions,
            preflight=preflight,
            summary=summary,
        )
    finally:
        if "portfolio_service" in locals():
            close_store = getattr(portfolio_service.store, "close", None)
            if callable(close_store):
                close_store()
        shutdown = getattr(market_data, "shutdown", None)
        if callable(shutdown):
            shutdown()
        if temp_dir is not None:
            temp_dir.cleanup()


def _default_backtest_timeframes(config: AppConfig) -> List[str]:
    discovered: List[str] = []
    for strategy_id in config.strategies.enabled:
        strat_cfg = config.strategies.configs.get(strategy_id)
        if strat_cfg is None:
            continue
        params = strat_cfg.params or {}
        value = params.get("timeframes")
        if isinstance(value, (list, tuple)):
            discovered.extend(str(item) for item in value if item)
        elif value:
            discovered.append(str(value))

        for key in ("timeframe", "regime_timeframe"):
            if params.get(key):
                discovered.append(str(params[key]))

    for timeframe in getattr(config.market_data, "backfill_timeframes", []) or []:
        discovered.append(str(timeframe))

    ordered: List[str] = []
    for timeframe in discovered or ["1h"]:
        if timeframe not in ordered:
            ordered.append(timeframe)
    return ordered


def _configured_backtest_pairs(config: AppConfig) -> List[str]:
    requested_pairs = set(config.universe.include_pairs or [])
    for strategy_id in config.strategies.enabled:
        strat_cfg = config.strategies.configs.get(strategy_id)
        if strat_cfg is None:
            continue
        params = strat_cfg.params or {}
        pair_values = params.get("pairs")
        if isinstance(pair_values, list):
            requested_pairs.update(str(pair) for pair in pair_values if pair)
    return sorted(requested_pairs)


def _build_backtest_summary(
    *,
    start: datetime,
    end: datetime,
    pairs: List[str],
    timeframes: List[str],
    plans: List[ExecutionPlan],
    executions: List[ExecutionResult],
    market_data: BacktestMarketData,
    portfolio: BacktestPortfolioService,
    starting_cash_usd: float,
    fee_bps: float,
    strict_data: bool,
    preflight: BacktestPreflight,
) -> BacktestSummary:
    total_actions = sum(len(plan.actions) for plan in plans)
    blocked_actions = sum(
        1
        for plan in plans
        for action in plan.actions
        if getattr(action, "blocked", False)
    )
    orders = [order for execution in executions for order in execution.orders]
    filled_orders = sum(1 for order in orders if order.status == "filled")
    rejected_orders = sum(1 for order in orders if order.status == "rejected")
    execution_errors = sum(len(execution.errors) for execution in executions)
    blocked_reason_counts = _build_blocked_reason_counts(plans)
    get_equity = getattr(portfolio, "get_equity", None)
    equity_view = get_equity() if callable(get_equity) else None
    ending_equity = (
        equity_view.equity_base if equity_view is not None else float(starting_cash_usd)
    )
    absolute_pnl = ending_equity - float(starting_cash_usd)
    return_pct = (
        (absolute_pnl / float(starting_cash_usd)) * 100.0 if starting_cash_usd else 0.0
    )
    snapshots = portfolio.get_snapshots()
    max_drawdown_pct = _compute_max_drawdown_pct(snapshots)
    per_strategy = _build_strategy_summary(portfolio)
    slippage_bps = float(getattr(portfolio.app_config.execution, "max_slippage_bps", 0))
    cost_model = (
        "Immediate candle-close fills using configured slippage and flat taker fees."
    )

    assumptions = [
        "Starts from a synthetic USD-only wallet with no existing positions.",
        "Replays stored OHLC bars only; no Kraken REST or WebSocket calls are used.",
        "Uses the existing strategy, risk, order router, and OMS layers offline.",
        f"Fills are immediate from the latest available candle close with {slippage_bps:.0f} bps slippage and {fee_bps:.2f} bps taker fees.",
        "Does not model order book depth, spread dynamics, latency queueing, or partial fills.",
    ]
    replay_inputs = {
        "start": start.astimezone(UTC).isoformat(),
        "end": end.astimezone(UTC).isoformat(),
        "pairs": list(pairs),
        "timeframes": list(timeframes),
        "enabled_strategies": list(getattr(portfolio.app_config.strategies, "enabled", [])),
        "starting_cash_usd": float(starting_cash_usd),
        "fee_bps": float(fee_bps),
        "slippage_bps": slippage_bps,
        "strict_data": strict_data,
    }
    trust_level, trust_note, notable_warnings = _build_replay_diagnostics(
        total_actions=total_actions,
        blocked_actions=blocked_actions,
        total_orders=len(orders),
        filled_orders=filled_orders,
        execution_errors=execution_errors,
        preflight=preflight,
    )

    return BacktestSummary(
        start=start,
        end=end,
        starting_cash_usd=starting_cash_usd,
        ending_equity_usd=ending_equity,
        absolute_pnl_usd=absolute_pnl,
        return_pct=return_pct,
        max_drawdown_pct=max_drawdown_pct,
        realized_pnl_usd=(
            equity_view.realized_pnl_base_total if equity_view is not None else 0.0
        ),
        unrealized_pnl_usd=(
            equity_view.unrealized_pnl_base_total if equity_view is not None else 0.0
        ),
        pairs=pairs,
        timeframes=timeframes,
        total_cycles=len(plans),
        total_actions=total_actions,
        blocked_actions=blocked_actions,
        total_orders=len(orders),
        filled_orders=filled_orders,
        rejected_orders=rejected_orders,
        execution_errors=execution_errors,
        fee_bps=float(fee_bps),
        slippage_bps=slippage_bps,
        cost_model=cost_model,
        usable_series_count=preflight.usable_series_count,
        missing_series=list(preflight.missing_series),
        partial_series=list(preflight.partial_series),
        coverage=list(preflight.coverage),
        blocked_reason_counts=blocked_reason_counts,
        per_strategy=per_strategy,
        trust_level=trust_level,
        trust_note=trust_note,
        notable_warnings=notable_warnings,
        replay_inputs=replay_inputs,
        assumptions=assumptions,
    )


def _compute_max_drawdown_pct(snapshots: List[Any]) -> float:
    if not snapshots:
        return 0.0

    ordered = sorted(snapshots, key=lambda snapshot: snapshot.timestamp)
    peak = ordered[0].equity_base
    max_drawdown = 0.0
    for snapshot in ordered:
        peak = max(peak, snapshot.equity_base)
        if peak <= 0:
            continue
        drawdown = ((peak - snapshot.equity_base) / peak) * 100.0
        max_drawdown = max(max_drawdown, drawdown)
    return max_drawdown


def _build_strategy_summary(
    portfolio: BacktestPortfolioService,
) -> Dict[str, Dict[str, float | int]]:
    realized = portfolio.get_realized_pnl_by_strategy(include_manual=False)
    summary: Dict[str, Dict[str, float | int]] = {
        strategy_id: {
            "realized_pnl_usd": float(pnl),
            "trade_count": 0,
            "winning_trades": 0,
            "losing_trades": 0,
        }
        for strategy_id, pnl in realized.items()
    }

    for record in getattr(portfolio, "realized_pnl_history", []):
        strategy_id = getattr(record, "strategy_tag", None)
        if not strategy_id or strategy_id == "manual":
            continue
        entry = summary.setdefault(
            strategy_id,
            {
                "realized_pnl_usd": 0.0,
                "trade_count": 0,
                "winning_trades": 0,
                "losing_trades": 0,
            },
        )
        entry["trade_count"] = int(entry["trade_count"]) + 1
        pnl_quote = float(getattr(record, "pnl_quote", 0.0))
        if pnl_quote > 0:
            entry["winning_trades"] = int(entry["winning_trades"]) + 1
        elif pnl_quote < 0:
            entry["losing_trades"] = int(entry["losing_trades"]) + 1

    return summary


def _build_blocked_reason_counts(plans: List[ExecutionPlan]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for plan in plans:
        for action in plan.actions:
            if not getattr(action, "blocked", False):
                continue
            reasons = list(getattr(action, "blocked_reasons", []) or [])
            if not reasons:
                reasons = ["Blocked by risk guardrails"]
            for reason in reasons:
                counts[reason] = counts.get(reason, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _build_replay_diagnostics(
    *,
    total_actions: int,
    blocked_actions: int,
    total_orders: int,
    filled_orders: int,
    execution_errors: int,
    preflight: BacktestPreflight,
) -> tuple[str, str, List[str]]:
    warnings = list(preflight.warnings)

    if total_actions == 0:
        warnings.append("No strategy actions were generated in this window.")
    if total_orders == 0:
        warnings.append("No orders were submitted, so this run is weak for execution learning.")
    elif filled_orders == 0:
        warnings.append(
            "Orders were submitted but none filled, so execution outcomes are limited."
        )
    if total_actions > 0 and blocked_actions == total_actions:
        warnings.append("All strategy actions were blocked by guardrails.")
    elif total_actions > 0 and blocked_actions / total_actions >= 0.75:
        warnings.append("Most strategy actions were blocked by guardrails.")
    if execution_errors > 0:
        warnings.append(f"{execution_errors} execution errors occurred during the replay.")

    if execution_errors > 0:
        trust_level = "weak_signal"
        trust_note = "Weak signal: execution errors occurred during the replay."
    elif total_actions == 0:
        trust_level = "weak_signal"
        trust_note = "Weak signal: no strategy actions were generated in this window."
    elif total_orders == 0:
        trust_level = "weak_signal"
        trust_note = "Weak signal: no orders were submitted in this window."
    elif filled_orders == 0:
        trust_level = "weak_signal"
        trust_note = "Weak signal: no orders filled in this window."
    elif preflight.status == "limited":
        trust_level = "limited"
        trust_note = "Limited signal: historical coverage is incomplete for part of the requested window."
    elif blocked_actions == total_actions and total_actions > 0:
        trust_level = "limited"
        trust_note = "Limited signal: all strategy actions were blocked by guardrails."
    elif blocked_actions > 0:
        trust_level = "limited"
        trust_note = "Limited signal: some strategy actions were blocked by guardrails."
    else:
        trust_level = "decision_helpful"
        trust_note = "Decision-helpful: coverage was complete and the replay produced filled trades."

    return trust_level, trust_note, warnings
