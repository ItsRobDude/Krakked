"""Lightweight backtest runner built on the live strategy engine."""

from __future__ import annotations

import copy
import logging
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from kraken_bot.config import AppConfig, ConnectionStatus, OHLCBar, PairMetadata
from kraken_bot.connection.rest_client import KrakenRESTClient
from kraken_bot.execution.adapter import SimulationExecutionAdapter
from kraken_bot.execution.oms import ExecutionService
from kraken_bot.execution.models import ExecutionResult, LocalOrder
from kraken_bot.market_data.api import MarketDataAPI
from kraken_bot.market_data.ohlc_store import FileOHLCStore
from kraken_bot.portfolio.manager import PortfolioService
from kraken_bot.strategy.engine import StrategyEngine
from kraken_bot.strategy.models import ExecutionPlan

logger = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    plans: List[ExecutionPlan]
    executions: List[ExecutionResult]


class BacktestPortfolioService(PortfolioService):
    """Portfolio service variant that skips remote sync for offline runs."""

    def sync(self) -> Dict[str, int]:  # pragma: no cover - smoke tested via runner
        return {"new_trades": 0, "new_cash_flows": 0}


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

        self._universe = [self._pair_metadata_from_symbol(p) for p in pairs]
        self._universe_map = {p.canonical: p for p in self._universe}

        self._bar_cache: Dict[Tuple[str, str], List[OHLCBar]] = {}
        for pair in pairs:
            for timeframe in self._timeframes:
                bars = self._ohlc_store.get_bars(pair, timeframe, lookback=1_000_000)
                bounded = [
                    bar
                    for bar in bars
                    if self._start_ts <= int(bar.timestamp) <= self._end_ts
                ]
                self._bar_cache[(pair, timeframe)] = bounded

        timestamps: set[int] = set()
        for bars in self._bar_cache.values():
            timestamps.update(int(bar.timestamp) for bar in bars)
        self._timeline = sorted(ts for ts in timestamps if self._start_ts <= ts <= self._end_ts)
        total_bars = sum(len(bars) for bars in self._bar_cache.values())
        logger.info(
            "Backtest market data ready with %s bars across %s pairs", total_bars, len(self._universe)
        )

    def _pair_metadata_from_symbol(self, symbol: str) -> PairMetadata:
        base, quote = symbol.split("/") if "/" in symbol else (symbol[:3], symbol[3:])
        return PairMetadata(
            canonical=symbol,
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
        bars = self._bar_cache.get((pair, timeframe), [])
        cutoff = int(self._current_time.timestamp()) if self._current_time else self._end_ts
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


def _ingest_simulated_fills(
    execution: ExecutionResult,
    portfolio: PortfolioService,
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
        portfolio.portfolio.ingest_trades(trades, persist=True)


def _trade_from_order(order: LocalOrder) -> Optional[Dict]:
    price = order.avg_fill_price or order.requested_price
    volume = order.cumulative_base_filled or order.requested_base_size
    if price is None or volume <= 0:
        return None

    return {
        "id": order.kraken_order_id or order.local_id,
        "ordertxid": order.kraken_order_id,
        "pair": order.pair,
        "time": order.updated_at.timestamp(),
        "type": order.side,
        "ordertype": order.order_type,
        "price": float(price),
        "cost": float(price) * float(volume),
        "fee": 0.0,
        "vol": float(volume),
        "margin": 0.0,
        "misc": "",
        "posstatus": None,
    }


def run_backtest(
    config: AppConfig,
    start: datetime,
    end: datetime,
    timeframes: Optional[Iterable[str]] = None,
) -> BacktestResult:
    """Run the configured strategies across stored OHLC bars for the window."""

    config_copy = copy.deepcopy(config)
    config_copy.execution.mode = "simulation"

    frames = list(timeframes) if timeframes else ["1h"]
    pairs = list(config_copy.universe.include_pairs)

    market_data = BacktestMarketData(config_copy, pairs, frames, start, end)

    with tempfile.NamedTemporaryFile(suffix=".db") as tmp_db:
        portfolio_service = BacktestPortfolioService(
            config_copy, market_data, db_path=tmp_db.name
        )

        strategy_engine = StrategyEngine(config_copy, market_data, portfolio_service)
        strategy_engine.initialize()

        execution_service = ExecutionService(
            adapter=SimulationExecutionAdapter(config=config_copy.execution),
            store=portfolio_service.store,
            config=config_copy.execution,
            market_data=market_data,
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

            _ingest_simulated_fills(execution, portfolio_service, _trade_from_order)

    return BacktestResult(plans=plans, executions=executions)
