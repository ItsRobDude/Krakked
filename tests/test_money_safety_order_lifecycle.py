"""Money-safety proof: order lifecycle and response-loss safety gap.

These tests drive the REAL ExecutionService + REAL KrakenExecutionAdapter + a
REAL temp SQLite PortfolioStore against the deterministic fake Kraken client, so
the behavior proven here is the production code path, not a mock of it.

See docs/money-safety-proof-plan.md, Milestones A and B. These tests establish
the fake Kraken harness and prove that one live submit intent does not blindly
duplicate when the exchange accepts the order but the local caller loses the
response.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Dict, cast
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

from krakked import cli
from krakked.config import (
    AppConfig,
    ExecutionConfig,
    MarketDataConfig,
    PortfolioConfig,
    RegionCapabilities,
    RegionProfile,
    RiskConfig,
    StrategiesConfig,
    StrategyConfig,
    UniverseConfig,
)
from krakked.connection.rest_client import KrakenRESTClient
from krakked.execution.adapter import KrakenExecutionAdapter
from krakked.execution.oms import (
    PORTFOLIO_DRIFT_ORDER_BLOCKED_MESSAGE,
    PORTFOLIO_SYNC_ORDER_BLOCKED_MESSAGE,
    ExecutionService,
)
from krakked.main import EMERGENCY_FLATTEN_MAX_NO_PROGRESS_ATTEMPTS, _run_loop_iteration
from krakked.market_data.api import MarketDataAPI
from krakked.market_data.models import ConnectionStatus, OHLCBar, PairMetadata
from krakked.metrics import SystemMetrics
from krakked.portfolio.manager import PortfolioService
from krakked.portfolio.models import LedgerEntry
from krakked.portfolio.store import SQLitePortfolioStore
from krakked.portfolio.sync_status import (
    LIVE_ACCOUNT_TRUTH_REFRESH_TIMEOUT_REASON,
    LIVE_SYNC_DEGRADED_REASON,
    LIVE_SYNC_LEDGERS_UNAVAILABLE_REASON,
    LIVE_SYNC_TRADE_HISTORY_LAG_ALERT_TITLE,
    LIVE_SYNC_TRADE_HISTORY_LAGGING_REASON,
    LIVE_SYNC_TRADES_UNAVAILABLE_REASON,
    live_sync_stale_reason,
    live_sync_trade_history_lag_escalated_reason,
)
from krakked.strategy.engine import StrategyEngine
from krakked.strategy.models import ExecutionPlan, RiskAdjustedAction
from krakked.ui.api import create_api
from krakked.ui.context import AppContext, SessionState
from tests.fakes.fake_kraken import (
    ACCEPT,
    ACCEPT_THEN_LOST,
    RATE_LIMIT,
    SERVICE_UNAVAILABLE,
    FakeKrakenRESTClient,
)

USERREF = 99


def _inactive_risk():
    return SimpleNamespace(
        kill_switch_active=False,
        portfolio_sync_ok=True,
        portfolio_sync_reason=None,
    )


def _degraded_risk():
    return SimpleNamespace(
        kill_switch_active=False,
        portfolio_sync_ok=False,
        portfolio_sync_reason="Live balance reconciliation unavailable: API Down",
    )


def _drift_risk():
    return SimpleNamespace(
        kill_switch_active=False,
        portfolio_sync_ok=True,
        portfolio_sync_reason=None,
        drift_flag=True,
        drift_info={"mismatched_assets": [{"asset": "USD"}]},
    )


def _live_config() -> ExecutionConfig:
    return ExecutionConfig(
        mode="live",
        validate_only=False,
        allow_live_trading=True,
        paper_tests_completed=True,
        live_strategy_allowlist=["strat"],
        dead_man_switch_seconds=0,
        default_order_type="limit",
        min_order_notional_usd=20.0,
        max_retries=3,
        retry_backoff_seconds=0,
        retry_backoff_factor=1.0,
    )


def _market_data(mid_price: float = 100.0) -> MagicMock:
    md = MagicMock()

    def _build_metadata(pair: str) -> PairMetadata:
        compact = str(pair).replace("/", "")
        base, quote = compact[:3], compact[3:]
        rest_symbol = f"{base}/{quote}"
        return PairMetadata(
            canonical=compact,
            base=base,
            quote=quote,
            rest_symbol=rest_symbol,
            ws_symbol=rest_symbol,
            raw_name=pair,
            price_decimals=1,
            volume_decimals=8,
            lot_size=0.00000001,
            min_order_size=0.0001,
            status="online",
        )

    md.get_pair_metadata_or_raise.side_effect = _build_metadata
    md.get_pair_metadata.side_effect = _build_metadata
    md.get_best_bid_ask.return_value = {"bid": mid_price - 0.5, "ask": mid_price + 0.5}
    md.get_latest_price.return_value = mid_price
    md.get_valuation_pair.side_effect = lambda asset: (
        "XBTUSD" if str(asset) in {"XBT", "XXBT"} else None
    )
    md.normalize_asset.side_effect = lambda asset: {
        "XXBT": "XBT",
        "XBT": "XBT",
        "ZUSD": "USD",
        "USD": "USD",
    }.get(str(asset), str(asset))
    return md


class _DecisionLoopMarketData:
    """Deterministic market data that makes rs_rotation emit a real intent."""

    pairs = ["XBTUSD", "ETHUSD", "SOLUSD", "ADAUSD"]
    prices = {
        "XBTUSD": 100.0,
        "ETHUSD": 50.0,
        "SOLUSD": 25.0,
        "ADAUSD": 1.0,
    }
    returns = {
        "XBTUSD": 0.24,
        "ETHUSD": 0.08,
        "SOLUSD": -0.03,
        "ADAUSD": -0.08,
    }

    @staticmethod
    def _canonical(pair: str) -> str:
        compact = str(pair).replace("/", "").upper()
        if compact.startswith("BTC"):
            compact = "XBT" + compact[3:]
        return compact

    def get_data_status(self) -> ConnectionStatus:
        return ConnectionStatus(
            rest_api_reachable=True,
            websocket_connected=True,
            streaming_pairs=len(self.pairs),
            stale_pairs=0,
            subscription_errors=0,
        )

    def get_universe(self) -> list[str]:
        return list(self.pairs)

    def get_display_pair(self, pair: str) -> str:
        compact = self._canonical(pair)
        if compact.endswith("USD"):
            return f"{compact[:-3]}/USD"
        return compact

    def normalize_asset(self, asset: str) -> str:
        value = str(asset).upper()
        return {
            "ZUSD": "USD",
            "USD": "USD",
            "XXBT": "XBT",
            "XBT": "XBT",
            "BTC": "XBT",
        }.get(value, value)

    def get_valuation_pair(self, asset: str) -> str | None:
        normalized = self.normalize_asset(asset)
        if normalized == "USD":
            return None
        candidate = f"{normalized}USD"
        return candidate if candidate in self.prices else None

    def get_pair_metadata(self, pair: str) -> PairMetadata:
        compact = self._canonical(pair)
        if compact not in self.prices:
            raise ValueError(f"Unknown pair {pair}")
        base = compact[:-3] if compact.endswith("USD") else compact[:3]
        quote = "USD" if compact.endswith("USD") else compact[3:]
        return PairMetadata(
            canonical=compact,
            base=base,
            quote=quote,
            rest_symbol=compact,
            ws_symbol=f"{base}/{quote}",
            raw_name=compact,
            price_decimals=1,
            volume_decimals=8,
            lot_size=0.00000001,
            min_order_size=0.0001,
            status="online",
            liquidity_24h_usd=1_000_000_000.0,
        )

    def get_pair_metadata_or_raise(self, pair: str) -> PairMetadata:
        return self.get_pair_metadata(pair)

    def get_latest_price(self, pair: str) -> float:
        return self.prices[self._canonical(pair)]

    def get_best_bid_ask(self, pair: str) -> dict[str, float]:
        price = self.get_latest_price(pair)
        return {"bid": price - 0.1, "ask": price + 0.1}

    def get_ohlc(self, pair: str, timeframe: str, lookback: int) -> list[OHLCBar]:
        del timeframe
        compact = self._canonical(pair)
        last_close = self.prices[compact]
        relative_return = self.returns.get(compact, 0.0)
        first_close = last_close / (1.0 + relative_return)
        count = max(int(lookback or 1), 1)
        bars: list[OHLCBar] = []
        for idx in range(count):
            fraction = idx / (count - 1) if count > 1 else 1.0
            close = first_close + ((last_close - first_close) * fraction)
            open_price = close * 0.999
            bars.append(
                OHLCBar(
                    timestamp=1_700_000_000 + (idx * 3600),
                    open=open_price,
                    high=max(open_price, close) * 1.001,
                    low=min(open_price, close) * 0.999,
                    close=close,
                    volume=1_000.0,
                )
            )
        return bars


def _app_config(db_path: str) -> AppConfig:
    return AppConfig(
        region=RegionProfile(
            code="TEST",
            capabilities=RegionCapabilities(
                supports_margin=False,
                supports_futures=False,
                supports_staking=False,
            ),
        ),
        universe=UniverseConfig(
            include_pairs=["XBTUSD"], exclude_pairs=[], min_24h_volume_usd=0.0
        ),
        market_data=MarketDataConfig(
            ws={},
            ohlc_store={},
            backfill_timeframes=[],
            ws_timeframes=[],
        ),
        portfolio=PortfolioConfig(
            db_path=db_path,
            reconciliation_tolerance=0.0001,
            valuation_pairs={"XBT": "XBTUSD"},
        ),
        execution=_live_config(),
        strategies=StrategiesConfig(
            enabled=["strat"],
            configs={
                "strat": StrategyConfig(
                    name="strat",
                    type="manual",
                    enabled=True,
                    userref=USERREF,
                )
            },
        ),
    )


def _paper_config(db_path: str) -> AppConfig:
    config = _app_config(db_path)
    config.universe.include_pairs = ["XBTUSD", "ETHUSD"]
    config.portfolio.valuation_pairs = {
        "XBT": "XBTUSD",
        "ETH": "ETHUSD",
    }
    config.execution = ExecutionConfig(
        mode="paper",
        validate_only=False,
        allow_live_trading=False,
        default_order_type="market",
        min_order_notional_usd=20.0,
        max_retries=1,
        retry_backoff_seconds=0,
        retry_backoff_factor=1.0,
    )
    return config


def _decision_loop_config(db_path: str) -> AppConfig:
    execution = _live_config()
    execution.live_strategy_allowlist = ["rs_rotation"]
    execution.max_retries = 1

    return AppConfig(
        region=RegionProfile(
            code="TEST",
            capabilities=RegionCapabilities(
                supports_margin=False,
                supports_futures=False,
                supports_staking=False,
            ),
        ),
        universe=UniverseConfig(
            include_pairs=list(_DecisionLoopMarketData.pairs),
            exclude_pairs=[],
            min_24h_volume_usd=0.0,
        ),
        market_data=MarketDataConfig(
            ws={},
            ohlc_store={},
            backfill_timeframes=["4h"],
            ws_timeframes=[],
        ),
        portfolio=PortfolioConfig(
            db_path=db_path,
            reconciliation_tolerance=0.0001,
            valuation_pairs={
                "XBT": "XBTUSD",
                "ETH": "ETHUSD",
                "SOL": "SOLUSD",
                "ADA": "ADAUSD",
            },
        ),
        execution=execution,
        risk=RiskConfig(
            max_portfolio_risk_pct=50.0,
            max_per_asset_pct=20.0,
            max_per_strategy_pct={"rs_rotation": 20.0},
            min_liquidity_24h_usd=0.0,
        ),
        strategies=StrategiesConfig(
            enabled=["rs_rotation"],
            configs={
                "rs_rotation": StrategyConfig(
                    name="rs_rotation",
                    type="relative_strength",
                    enabled=True,
                    userref=4242,
                    params={
                        "pairs": list(_DecisionLoopMarketData.pairs),
                        "lookback_bars": 42,
                        "timeframe": "4h",
                        "rebalance_interval_hours": 24,
                        "top_n": 1,
                        "total_allocation_pct": 5.0,
                        "confidence_return_bps": 100.0,
                    },
                )
            },
        ),
    )


def _action(**overrides: Any) -> RiskAdjustedAction:
    base: Dict[str, Any] = dict(
        pair="XBTUSD",
        strategy_id="strat",
        action_type="open",
        target_base_size=1.0,
        target_notional_usd=100.0,
        current_base_size=0.0,
        reason="",
        blocked=False,
        blocked_reasons=[],
        strategy_tag="tag",
        userref=USERREF,
        risk_limits_snapshot={},
    )
    base.update(overrides)
    return RiskAdjustedAction(**base)


def _plan(plan_id: str = "plan-1") -> ExecutionPlan:
    return ExecutionPlan(
        plan_id=plan_id,
        generated_at=datetime.now(UTC),
        actions=[_action()],
        metadata={"order_type": "limit"},
    )


def _service(
    client: FakeKrakenRESTClient, store: SQLitePortfolioStore
) -> ExecutionService:
    config = _live_config()
    adapter = KrakenExecutionAdapter(
        client=cast(KrakenRESTClient, client), config=config
    )
    return ExecutionService(
        adapter=adapter,
        store=store,
        config=config,
        market_data=_market_data(),
        risk_status_provider=_inactive_risk,
    )


def _service_with_risk(
    client: FakeKrakenRESTClient,
    store: SQLitePortfolioStore,
    risk_status_provider,
) -> ExecutionService:
    config = _live_config()
    adapter = KrakenExecutionAdapter(
        client=cast(KrakenRESTClient, client), config=config
    )
    return ExecutionService(
        adapter=adapter,
        store=store,
        config=config,
        market_data=_market_data(),
        risk_status_provider=risk_status_provider,
    )


def _service_with_account_truth(
    client: FakeKrakenRESTClient,
    store: SQLitePortfolioStore,
    portfolio: PortfolioService,
) -> ExecutionService:
    config = _live_config()
    adapter = KrakenExecutionAdapter(
        client=cast(KrakenRESTClient, client), config=config
    )
    return ExecutionService(
        adapter=adapter,
        store=store,
        config=config,
        market_data=_market_data(),
        risk_status_provider=_inactive_risk,
        account_truth_provider=portfolio.get_account_truth_snapshot,
    )


def _portfolio_service(
    client: FakeKrakenRESTClient,
    db_path,
    *,
    alert_notifier: Any | None = None,
    clock: Any | None = None,
) -> PortfolioService:
    service = PortfolioService(
        config=_app_config(str(db_path)),
        market_data=_market_data(),
        db_path=str(db_path),
        rest_client=cast(KrakenRESTClient, client),
        alert_notifier=alert_notifier,
        clock=clock,
    )
    return service


def _decision_loop_services(client: FakeKrakenRESTClient, db_path):
    config = _decision_loop_config(str(db_path))
    market_data = _DecisionLoopMarketData()
    market_data_api = cast(MarketDataAPI, market_data)
    portfolio = PortfolioService(
        config=config,
        market_data=market_data_api,
        db_path=str(db_path),
        rest_client=cast(KrakenRESTClient, client),
    )
    strategy_engine = StrategyEngine(config, market_data_api, portfolio)
    strategy_engine.initialize()
    adapter = KrakenExecutionAdapter(
        client=cast(KrakenRESTClient, client),
        config=config.execution,
    )
    execution_service = ExecutionService(
        adapter=adapter,
        store=cast(SQLitePortfolioStore, portfolio.store),
        config=config.execution,
        market_data=market_data_api,
        risk_status_provider=strategy_engine.get_risk_status,
        account_truth_provider=portfolio.get_account_truth_snapshot,
    )
    return portfolio, strategy_engine, execution_service


class _RecordingAlerts:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def send(self, **kwargs: Any) -> bool:
        self.events.append(kwargs)
        return True


def _service_with_alerts(
    client: FakeKrakenRESTClient,
    store: SQLitePortfolioStore,
    alerts: _RecordingAlerts,
) -> ExecutionService:
    config = _live_config()
    adapter = KrakenExecutionAdapter(
        client=cast(KrakenRESTClient, client), config=config
    )
    return ExecutionService(
        adapter=adapter,
        store=store,
        config=config,
        market_data=_market_data(),
        risk_status_provider=_inactive_risk,
        alert_notifier=alerts,
    )


def _seed_sellable_position(
    client: FakeKrakenRESTClient,
    db_path,
    *,
    size: float = 1.0,
    price: float = 100.0,
    plan_id: str = "seed-sellable-position",
) -> PortfolioService:
    store = SQLitePortfolioStore(str(db_path))
    service = _service(client, store)
    result = service.execute_plan(
        ExecutionPlan(
            plan_id=plan_id,
            generated_at=datetime.now(UTC),
            actions=[
                _action(
                    target_base_size=size,
                    target_notional_usd=size * price,
                    current_base_size=0.0,
                )
            ],
            metadata={"order_type": "limit", "requested_price": price},
        )
    )
    assert result.success is True
    order = result.orders[0]
    assert order.kraken_order_id is not None

    client.close_order(order.kraken_order_id, price=price)
    service.refresh_open_orders()
    service.reconcile_orders()
    store.close()

    portfolio = _portfolio_service(client, db_path)
    sync_result = portfolio.sync()
    assert sync_result["new_trades"] == 1
    assert portfolio.last_sync_ok is True
    assert portfolio.get_positions()
    return portfolio


def _seed_dust_position(
    client: FakeKrakenRESTClient,
    db_path,
    *,
    size: str = "0.00000001",
    price: float = 100.0,
) -> PortfolioService:
    response = client.add_order(
        {
            "pair": "XBTUSD",
            "type": "buy",
            "ordertype": "limit",
            "volume": size,
            "price": str(price),
            "cl_ord_id": "seed-dust-position",
        }
    )
    txid = response["txid"][0]
    client.close_order(txid, price=price)

    portfolio = _portfolio_service(client, db_path)
    sync_result = portfolio.sync()
    assert sync_result["new_trades"] == 1
    assert portfolio.last_sync_ok is True
    assert portfolio.get_positions()
    return portfolio


def _real_flatten_context(
    client: FakeKrakenRESTClient,
    portfolio: PortfolioService,
) -> tuple[TestClient, AppContext, StrategyEngine, ExecutionService, MagicMock]:
    config = portfolio.app_config
    market_data = _market_data()
    strategy_engine = StrategyEngine(config, market_data, portfolio)
    execution_service = _service_with_account_truth(
        client,
        cast(SQLitePortfolioStore, portfolio.store),
        portfolio,
    )
    session = SessionState(
        active=False,
        mode=config.execution.mode,
        loop_interval_sec=config.session.loop_interval_sec,
        profile_name=config.session.profile_name,
        ml_enabled=config.ml.enabled,
        emergency_flatten=config.session.emergency_flatten,
        account_id=config.session.account_id or "default",
    )
    refresh_metrics_state = MagicMock(name="refresh_metrics_state")
    context = AppContext(
        config=config,
        client=cast(KrakenRESTClient, client),
        market_data=market_data,
        portfolio_service=portfolio,
        portfolio=portfolio,
        strategy_engine=strategy_engine,
        execution_service=execution_service,
        metrics=SystemMetrics(),
        session=session,
    )
    app = create_api(context)
    test_client = TestClient(app)
    test_client.context = context  # type: ignore[attr-defined]
    return (
        test_client,
        context,
        strategy_engine,
        execution_service,
        refresh_metrics_state,
    )


def _paper_flatten_context(db_path) -> tuple[TestClient, AppContext, MagicMock]:
    config = _paper_config(str(db_path))
    market_data = _DecisionLoopMarketData()
    client = FakeKrakenRESTClient(add_order_mode=ACCEPT)
    portfolio = PortfolioService(
        config=config,
        market_data=cast(MarketDataAPI, market_data),
        db_path=str(db_path),
        rest_client=cast(KrakenRESTClient, client),
    )
    portfolio.sync()
    strategy_engine = StrategyEngine(
        config, cast(MarketDataAPI, market_data), portfolio
    )
    execution_service = ExecutionService(
        store=cast(SQLitePortfolioStore, portfolio.store),
        config=config.execution,
        market_data=cast(MarketDataAPI, market_data),
        risk_status_provider=_inactive_risk,
    )
    execution_service.adapter.client = None
    session = SessionState(
        active=False,
        mode="paper",
        loop_interval_sec=config.session.loop_interval_sec,
        profile_name=config.session.profile_name,
        ml_enabled=config.ml.enabled,
        emergency_flatten=config.session.emergency_flatten,
        account_id=config.session.account_id or "default",
    )
    context = AppContext(
        config=config,
        client=cast(KrakenRESTClient, client),
        market_data=cast(MarketDataAPI, market_data),
        portfolio_service=portfolio,
        portfolio=portfolio,
        strategy_engine=strategy_engine,
        execution_service=execution_service,
        metrics=SystemMetrics(),
        session=session,
    )
    app = create_api(context)
    test_client = TestClient(app)
    test_client.context = context  # type: ignore[attr-defined]
    return test_client, context, MagicMock(name="refresh_metrics_state")


def _seed_paper_positions(context: AppContext) -> ExecutionPlan:
    execution_service = cast(ExecutionService, context.execution_service)
    portfolio = cast(PortfolioService, context.portfolio)
    plan = ExecutionPlan(
        plan_id="seed-paper-positions",
        generated_at=datetime.now(UTC),
        actions=[
            _action(
                pair="XBTUSD",
                strategy_id="strat",
                target_base_size=0.2,
                target_notional_usd=20.0,
                current_base_size=0.0,
            ),
            _action(
                pair="ETHUSD",
                strategy_id="strat",
                target_base_size=0.6,
                target_notional_usd=30.0,
                current_base_size=0.0,
            ),
        ],
        metadata={"order_type": "market"},
    )
    result = execution_service.execute_plan(plan)
    assert result.success is True
    assert all(order.status == "filled" for order in result.orders)
    portfolio.ingest_filled_orders(result)
    positions = portfolio.get_positions()
    assert {position.pair for position in positions} == {"XBTUSD", "ETHUSD"}
    return plan


def _run_emergency_flatten_once(
    *,
    now: datetime,
    portfolio: PortfolioService,
    market_data,
    strategy_engine: StrategyEngine,
    execution_service: ExecutionService,
    session: SessionState,
    refresh_metrics_state: MagicMock,
) -> None:
    _run_loop_iteration(
        now=now,
        strategy_interval=1,
        portfolio_interval=1,
        last_strategy_cycle=now - timedelta(seconds=2),
        last_portfolio_sync=now - timedelta(seconds=2),
        portfolio=portfolio,
        market_data=market_data,
        strategy_engine=strategy_engine,
        execution_service=execution_service,
        metrics=SystemMetrics(),
        refresh_metrics_state=refresh_metrics_state,
        session_active=False,
        session=session,
    )


def test_seeded_flatten_route_cancels_then_closes_and_resume_clears(tmp_path):
    client = FakeKrakenRESTClient(add_order_mode=ACCEPT)
    db_path = tmp_path / "seeded-route-flatten.db"
    portfolio = _seed_sellable_position(client, db_path)
    test_client, context, _strategy_engine, execution_service, refresh_metrics = (
        _real_flatten_context(client, portfolio)
    )

    working = execution_service.execute_plan(_plan("seed-working-open-order"))
    assert working.success is True
    assert working.orders[0].kraken_order_id is not None
    assert portfolio.store.get_open_orders()

    route_event_start = len(client.call_log)
    add_order_count_before_route = len(client.add_order_calls)

    with patch("krakked.ui.routes.execution.dump_runtime_overrides") as mock_dump:
        response = test_client.post(
            "/api/execution/flatten_all", json={"confirmation": "FLATTEN ALL"}
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["error"] is None
    assert context.session.emergency_flatten is True
    assert context.config.session.emergency_flatten is True
    mock_dump.assert_called_once()

    route_events = client.call_log[route_event_start:]
    cancel_index = next(
        idx
        for idx, event in enumerate(route_events)
        if event["event"] == "cancel_all_orders"
    )
    close_index = next(
        idx
        for idx, event in enumerate(route_events)
        if event["event"] == "add_order"
        and event["params"].get("type") == "sell"
        and event["params"].get("ordertype") == "market"
    )
    assert cancel_index < close_index
    assert client.cancel_all_calls == 1
    assert len(client.add_order_calls) == add_order_count_before_route + 1

    flatten_result = execution_service.get_recent_executions()[-1]
    assert flatten_result.plan_id.startswith("flatten_")
    assert flatten_result.success is True
    assert len(flatten_result.orders) == 1
    close_order = flatten_result.orders[0]
    assert close_order.side == "sell"
    assert close_order.risk_reducing is True
    assert close_order.kraken_order_id is not None
    assert close_order.status == "open"
    assert close_order.avg_fill_price is None

    persisted_results = portfolio.store.get_execution_results(limit=20)
    assert any(result.plan_id == flatten_result.plan_id for result in persisted_results)

    client.close_order(close_order.kraken_order_id, price=100.0)
    with patch("krakked.main.dump_runtime_overrides") as mock_dump_main:
        _run_emergency_flatten_once(
            now=datetime.now(UTC),
            portfolio=portfolio,
            market_data=context.market_data,
            strategy_engine=context.strategy_engine,
            execution_service=execution_service,
            session=context.session,
            refresh_metrics_state=refresh_metrics,
        )

    assert context.session.emergency_flatten is False
    assert context.config.session.emergency_flatten is False
    mock_dump_main.assert_called_once()
    assert portfolio.last_sync_ok is True
    assert all(
        abs(position.base_size) <= 1e-9 for position in portfolio.get_positions()
    )
    assert portfolio.store.get_open_orders() == []

    persisted_close = portfolio.store.get_order_by_reference(
        kraken_order_id=close_order.kraken_order_id
    )
    assert persisted_close is not None
    assert persisted_close.status == "closed"
    assert persisted_close.cumulative_base_filled == close_order.requested_base_size
    assert client.get_private("Balance")["XXBT"] == "0.00000000"


def test_paper_background_emergency_flatten_ingests_and_clears(tmp_path):
    _test_client, context, refresh_metrics = _paper_flatten_context(
        tmp_path / "paper-background-flatten.db"
    )
    _seed_paper_positions(context)
    trades_before = len(context.portfolio.get_trade_history())
    context.session.emergency_flatten = True
    context.config.session.emergency_flatten = True

    with patch("krakked.main.dump_runtime_overrides") as mock_dump_main:
        _run_emergency_flatten_once(
            now=datetime.now(UTC),
            portfolio=context.portfolio,
            market_data=context.market_data,
            strategy_engine=context.strategy_engine,
            execution_service=context.execution_service,
            session=context.session,
            refresh_metrics_state=refresh_metrics,
        )

    assert context.session.emergency_flatten is False
    assert context.config.session.emergency_flatten is False
    mock_dump_main.assert_called_once()
    assert refresh_metrics.called

    close_result = context.execution_service.get_recent_executions()[-1]
    assert close_result.success is True
    assert {order.side for order in close_result.orders} == {"sell"}
    assert all(order.status == "filled" for order in close_result.orders)
    assert all(order.avg_fill_price is not None for order in close_result.orders)
    assert len(context.portfolio.get_trade_history()) == trades_before + 2
    assert all(
        abs(position.base_size) <= 1e-9
        for position in context.portfolio.get_positions()
    )
    assert context.portfolio.balances["XBT"].total == pytest.approx(0.0)
    assert context.portfolio.balances["ETH"].total == pytest.approx(0.0)
    assert context.portfolio.get_snapshots(limit=1)


def test_paper_flatten_route_ingests_filled_orders(tmp_path):
    test_client, context, _refresh_metrics = _paper_flatten_context(
        tmp_path / "paper-route-flatten.db"
    )
    _seed_paper_positions(context)
    trades_before = len(context.portfolio.get_trade_history())

    with patch("krakked.ui.routes.execution.dump_runtime_overrides") as mock_dump:
        response = test_client.post(
            "/api/execution/flatten_all", json={"confirmation": "FLATTEN ALL"}
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["error"] is None
    assert context.session.emergency_flatten is True
    mock_dump.assert_called_once()

    flatten_result = context.execution_service.get_recent_executions()[-1]
    assert flatten_result.success is True
    assert all(order.status == "filled" for order in flatten_result.orders)
    assert all(order.avg_fill_price is not None for order in flatten_result.orders)
    assert len(context.portfolio.get_trade_history()) == trades_before + 2
    assert all(
        abs(position.base_size) <= 1e-9
        for position in context.portfolio.get_positions()
    )


def test_paper_background_emergency_flatten_rejects_missing_market_price(tmp_path):
    _test_client, context, refresh_metrics = _paper_flatten_context(
        tmp_path / "paper-missing-price-flatten.db"
    )
    _seed_paper_positions(context)
    trades_before = len(context.portfolio.get_trade_history())
    context.market_data.get_latest_price = lambda pair: None  # type: ignore[method-assign]
    context.session.emergency_flatten = True
    context.config.session.emergency_flatten = True

    _run_emergency_flatten_once(
        now=datetime.now(UTC),
        portfolio=context.portfolio,
        market_data=context.market_data,
        strategy_engine=context.strategy_engine,
        execution_service=context.execution_service,
        session=context.session,
        refresh_metrics_state=refresh_metrics,
    )

    close_result = context.execution_service.get_recent_executions()[-1]
    assert close_result.success is False
    assert close_result.errors == ["Unable to simulate fill: price unavailable"] * 2
    assert all(order.status == "rejected" for order in close_result.orders)
    assert all(order.avg_fill_price is None for order in close_result.orders)
    assert len(context.portfolio.get_trade_history()) == trades_before
    assert context.session.emergency_flatten is True
    assert getattr(context.session, "_emergency_flatten_no_progress_attempts") == 1
    assert {position.pair for position in context.portfolio.get_positions()} == {
        "XBTUSD",
        "ETHUSD",
    }


def test_paper_background_emergency_flatten_no_progress_cap(tmp_path):
    _test_client, context, refresh_metrics = _paper_flatten_context(
        tmp_path / "paper-no-progress-cap.db"
    )
    _seed_paper_positions(context)
    context.market_data.get_latest_price = lambda pair: None  # type: ignore[method-assign]
    context.session.emergency_flatten = True
    context.config.session.emergency_flatten = True
    metrics = SystemMetrics()

    for index in range(EMERGENCY_FLATTEN_MAX_NO_PROGRESS_ATTEMPTS):
        _run_loop_iteration(
            now=datetime.now(UTC) + timedelta(seconds=index),
            strategy_interval=1,
            portfolio_interval=1,
            last_strategy_cycle=datetime.now(UTC) - timedelta(seconds=2),
            last_portfolio_sync=datetime.now(UTC) - timedelta(seconds=2),
            portfolio=context.portfolio,
            market_data=context.market_data,
            strategy_engine=context.strategy_engine,
            execution_service=context.execution_service,
            metrics=metrics,
            refresh_metrics_state=refresh_metrics,
            session_active=False,
            session=context.session,
        )

    assert context.session.emergency_flatten is True
    assert "no position-reduction progress" in getattr(
        context.session, "_emergency_flatten_halted_reason"
    )
    recent_count = len(context.execution_service.get_recent_executions())

    _run_loop_iteration(
        now=datetime.now(UTC) + timedelta(seconds=30),
        strategy_interval=1,
        portfolio_interval=1,
        last_strategy_cycle=datetime.now(UTC) - timedelta(seconds=2),
        last_portfolio_sync=datetime.now(UTC) - timedelta(seconds=2),
        portfolio=context.portfolio,
        market_data=context.market_data,
        strategy_engine=context.strategy_engine,
        execution_service=context.execution_service,
        metrics=metrics,
        refresh_metrics_state=refresh_metrics,
        session_active=False,
        session=context.session,
    )

    assert len(context.execution_service.get_recent_executions()) == recent_count
    assert metrics.execution_errors >= 1


def test_seeded_flatten_route_keeps_armed_when_open_orders_remain(tmp_path):
    client = FakeKrakenRESTClient(
        add_order_mode=ACCEPT,
        cancel_all_leaves_orders_open=True,
    )
    db_path = tmp_path / "seeded-open-orders-remain.db"
    portfolio = _seed_sellable_position(client, db_path)
    test_client, context, _strategy_engine, execution_service, _refresh_metrics = (
        _real_flatten_context(client, portfolio)
    )

    working = execution_service.execute_plan(_plan("seed-uncleared-open-order"))
    assert working.success is True
    assert working.orders[0].kraken_order_id is not None
    add_order_count_before_route = len(client.add_order_calls)

    with patch("krakked.ui.routes.execution.dump_runtime_overrides") as mock_dump:
        response = test_client.post(
            "/api/execution/flatten_all", json={"confirmation": "FLATTEN ALL"}
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["data"] is None
    assert payload["error"] is not None
    assert "waiting for open orders" in payload["error"]
    assert "open orders remaining" in payload["error"]
    assert context.session.emergency_flatten is True
    mock_dump.assert_called_once()
    assert client.cancel_all_calls == 1
    assert len(client.add_order_calls) == add_order_count_before_route
    assert portfolio.store.get_open_orders()


def test_seeded_flatten_route_refuses_blind_close_when_account_truth_degraded(
    tmp_path,
):
    client = FakeKrakenRESTClient(add_order_mode=ACCEPT)
    db_path = tmp_path / "seeded-degraded-flatten.db"
    portfolio = _seed_sellable_position(client, db_path)
    test_client, context, _strategy_engine, _execution_service, _refresh_metrics = (
        _real_flatten_context(client, portfolio)
    )
    add_order_count_before_route = len(client.add_order_calls)
    client.fail_balance_reads(count=1)

    with patch("krakked.ui.routes.execution.dump_runtime_overrides") as mock_dump:
        response = test_client.post(
            "/api/execution/flatten_all", json={"confirmation": "FLATTEN ALL"}
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["data"] is None
    assert payload["error"] is not None
    assert "Can't verify your account right now" in payload["error"]
    assert "will not place close orders blind" in payload["error"]
    assert "account sync unavailable" in payload["error"]
    assert context.session.emergency_flatten is True
    assert context.config.session.emergency_flatten is True
    mock_dump.assert_called_once()
    assert client.cancel_all_calls == 1
    assert len(client.add_order_calls) == add_order_count_before_route
    assert portfolio.last_sync_ok is False


def test_seeded_background_emergency_flatten_retries_until_flat(tmp_path):
    client = FakeKrakenRESTClient(add_order_mode=ACCEPT)
    db_path = tmp_path / "seeded-background-flatten.db"
    portfolio = _seed_sellable_position(client, db_path)
    _test_client, context, _strategy_engine, execution_service, refresh_metrics = (
        _real_flatten_context(client, portfolio)
    )
    context.session.emergency_flatten = True
    context.config.session.emergency_flatten = True
    add_order_count_before_resume = len(client.add_order_calls)

    with patch("krakked.main.dump_runtime_overrides") as mock_dump_main:
        _run_emergency_flatten_once(
            now=datetime.now(UTC),
            portfolio=portfolio,
            market_data=context.market_data,
            strategy_engine=context.strategy_engine,
            execution_service=execution_service,
            session=context.session,
            refresh_metrics_state=refresh_metrics,
        )

    assert mock_dump_main.call_count == 0
    assert context.session.emergency_flatten is True
    assert len(client.add_order_calls) == add_order_count_before_resume + 1
    close_result = execution_service.get_recent_executions()[-1]
    close_order = close_result.orders[0]
    assert close_order.side == "sell"
    assert close_order.kraken_order_id is not None
    assert portfolio.store.get_open_orders()

    client.close_order(close_order.kraken_order_id, price=100.0)
    with patch("krakked.main.dump_runtime_overrides") as mock_dump_main:
        _run_emergency_flatten_once(
            now=datetime.now(UTC) + timedelta(seconds=2),
            portfolio=portfolio,
            market_data=context.market_data,
            strategy_engine=context.strategy_engine,
            execution_service=execution_service,
            session=context.session,
            refresh_metrics_state=refresh_metrics,
        )

    assert mock_dump_main.call_count == 1
    assert context.session.emergency_flatten is False
    assert context.config.session.emergency_flatten is False
    assert all(
        abs(position.base_size) <= 1e-9 for position in portfolio.get_positions()
    )
    assert portfolio.store.get_open_orders() == []


def test_seeded_background_emergency_flatten_clears_dust_without_retry_loop(
    tmp_path,
):
    client = FakeKrakenRESTClient(add_order_mode=ACCEPT)
    db_path = tmp_path / "seeded-dust-flatten.db"
    portfolio = _seed_dust_position(client, db_path)
    _test_client, context, _strategy_engine, execution_service, refresh_metrics = (
        _real_flatten_context(client, portfolio)
    )
    context.session.emergency_flatten = True
    context.config.session.emergency_flatten = True
    add_order_count_before_resume = len(client.add_order_calls)

    with patch("krakked.main.dump_runtime_overrides") as mock_dump_main:
        _run_emergency_flatten_once(
            now=datetime.now(UTC),
            portfolio=portfolio,
            market_data=context.market_data,
            strategy_engine=context.strategy_engine,
            execution_service=execution_service,
            session=context.session,
            refresh_metrics_state=refresh_metrics,
        )

    assert mock_dump_main.call_count == 1
    assert context.session.emergency_flatten is False
    assert context.config.session.emergency_flatten is False
    assert len(client.add_order_calls) == add_order_count_before_resume
    assert execution_service.get_recent_executions() == []
    assert portfolio.get_positions()
    assert client.get_private("Balance")["XXBT"] == "0.00000001"


def test_happy_path_live_order_lifecycle_persists_and_is_recoverable(tmp_path):
    """A live order submits once, gets a txid, persists, and is findable by userref."""

    client = FakeKrakenRESTClient(add_order_mode=ACCEPT)
    store = SQLitePortfolioStore(str(tmp_path / "portfolio.db"))
    service = _service(client, store)

    result = service.execute_plan(_plan())

    # Exactly one submission reached the exchange and was accepted.
    assert len(client.add_order_calls) == 1
    assert client.open_count == 1

    # The order came back open with the exchange txid and was persisted.
    assert result.orders
    order = result.orders[0]
    assert order.status == "open"
    assert order.kraken_order_id is not None

    persisted = store.get_order_by_reference(userref=USERREF)
    assert persisted is not None
    assert persisted.kraken_order_id == order.kraken_order_id


def test_lost_response_after_acceptance_never_creates_duplicate_live_orders(tmp_path):
    """A single intent must never result in more than one live order, even when
    the exchange accepts an order but the caller's response is lost.
    """

    client = FakeKrakenRESTClient(add_order_mode=ACCEPT_THEN_LOST)
    store = SQLitePortfolioStore(str(tmp_path / "portfolio.db"))
    service = _service(client, store)

    service.execute_plan(_plan())

    assert len(client.add_order_calls) == 1
    assert client.open_count == 1

    submitted = client.add_order_calls[0]
    assert submitted["cl_ord_id"]
    assert "userref" not in submitted
    assert client.get_open_order_calls == [{"cl_ord_id": submitted["cl_ord_id"]}]


def test_known_not_accepted_retry_boundary_can_submit_one_remote_order(tmp_path):
    """A known no-accept retry boundary may retry without duplicating exposure."""

    client = FakeKrakenRESTClient(add_order_modes=[RATE_LIMIT, ACCEPT])
    store = SQLitePortfolioStore(str(tmp_path / "portfolio.db"))
    service = _service(client, store)

    result = service.execute_plan(_plan())

    assert not result.errors
    assert len(client.add_order_calls) == 2
    assert client.open_count == 1
    assert result.orders
    assert result.orders[0].status == "open"


def test_generic_service_unavailable_without_remote_match_is_not_retried(tmp_path):
    """A generic live submit uncertainty must not be retried blindly."""

    client = FakeKrakenRESTClient(add_order_mode=SERVICE_UNAVAILABLE)
    store = SQLitePortfolioStore(str(tmp_path / "portfolio.db"))
    service = _service(client, store)

    result = service.execute_plan(_plan())
    service.refresh_open_orders()
    service.reconcile_orders()

    assert result.errors
    assert client.open_count == 0
    assert len(client.add_order_calls) == 1

    unknown_orders = store.get_open_orders()
    assert len(unknown_orders) == 1
    assert unknown_orders[0].status == "submit_unknown"
    assert unknown_orders[0].raw_request["cl_ord_id"] == unknown_orders[0].local_id
    assert "userref" not in unknown_orders[0].raw_request


def test_lost_response_restart_recovery_links_single_remote_order(tmp_path):
    """A restart must recover the accepted remote order without re-submitting."""

    client = FakeKrakenRESTClient(add_order_mode=ACCEPT_THEN_LOST)
    store = SQLitePortfolioStore(str(tmp_path / "portfolio.db"))
    service = _service(client, store)

    service.execute_plan(_plan())

    restarted = _service(client, store)
    restarted.load_open_orders_from_store()
    restarted.refresh_open_orders()
    restarted.reconcile_orders()

    persisted = store.get_order_by_reference(userref=USERREF)

    assert len(client.add_order_calls) == 1
    assert client.open_count == 1
    assert persisted is not None
    assert persisted.status == "open"
    assert persisted.kraken_order_id is not None
    assert persisted.local_id in restarted.open_orders


def test_unresolved_submit_unknown_blocks_new_opening_risk_after_restart(tmp_path):
    """An unresolved live submit uncertainty blocks new opening risk."""

    client = FakeKrakenRESTClient(add_order_mode=SERVICE_UNAVAILABLE)
    store = SQLitePortfolioStore(str(tmp_path / "portfolio.db"))
    service = _service(client, store)

    first_result = service.execute_plan(_plan(plan_id="plan-unknown"))

    assert first_result.errors
    assert len(client.add_order_calls) == 1

    client.add_order_mode = ACCEPT
    restarted = _service(client, store)
    restarted.load_open_orders_from_store()
    second_result = restarted.execute_plan(_plan(plan_id="plan-new"))

    assert second_result.errors
    assert len(client.add_order_calls) == 1
    assert second_result.orders
    assert second_result.orders[0].status == "rejected"
    assert "unresolved live submit intent" in (second_result.orders[0].last_error or "")


def test_same_plan_block_does_not_overwrite_submit_unknown(tmp_path):
    """Re-running the same plan must not replace the original submit_unknown row."""

    client = FakeKrakenRESTClient(add_order_mode=SERVICE_UNAVAILABLE)
    store = SQLitePortfolioStore(str(tmp_path / "portfolio.db"))
    service = _service(client, store)

    first_result = service.execute_plan(_plan(plan_id="same-plan"))

    assert first_result.errors
    original = store.get_open_orders()[0]
    assert original.status == "submit_unknown"
    original_client_order_id = original.raw_request["cl_ord_id"]

    client.add_order_mode = ACCEPT
    restarted = _service(client, store)
    second_result = restarted.execute_plan(_plan(plan_id="same-plan"))

    assert second_result.errors
    assert len(client.add_order_calls) == 1
    assert second_result.orders
    assert second_result.orders[0].status == "rejected"
    assert second_result.orders[0].local_id != original.local_id

    persisted = store.get_order_by_client_order_id(original_client_order_id)
    assert persisted is not None
    assert persisted.local_id == original.local_id
    assert persisted.status == "submit_unknown"
    assert persisted.kraken_order_id is None


def test_ambiguous_client_order_match_stays_submit_unknown(tmp_path):
    """Multiple remote matches for one cl_ord_id must not adopt an arbitrary txid."""

    client = FakeKrakenRESTClient(add_order_mode=ACCEPT_THEN_LOST)
    client.duplicate_client_order_matches = True
    store = SQLitePortfolioStore(str(tmp_path / "portfolio.db"))
    service = _service(client, store)

    result = service.execute_plan(_plan(plan_id="ambiguous-plan"))

    assert result.errors
    assert len(client.add_order_calls) == 1
    unknown = store.get_open_orders()[0]
    assert unknown.status == "submit_unknown"
    assert unknown.kraken_order_id is None


def test_submit_unknown_and_blocked_opening_emit_alerts(tmp_path):
    client = FakeKrakenRESTClient(add_order_mode=SERVICE_UNAVAILABLE)
    store = SQLitePortfolioStore(str(tmp_path / "portfolio.db"))
    alerts = _RecordingAlerts()
    service = _service_with_alerts(client, store, alerts)

    service.execute_plan(_plan(plan_id="alert-unknown"))

    assert [event["event"] for event in alerts.events] == ["order_submit_unknown"]

    client.add_order_mode = ACCEPT
    restarted_alerts = _RecordingAlerts()
    restarted = _service_with_alerts(client, store, restarted_alerts)
    restarted.execute_plan(_plan(plan_id="alert-blocked"))

    assert [event["event"] for event in restarted_alerts.events] == [
        "order_blocked_submit_unknown"
    ]


def test_lost_response_without_cl_ord_id_echo_is_not_adopted(tmp_path):
    """If the exchange filters but does NOT echo cl_ord_id back, recovery must
    refuse to adopt the single returned order and stay submit_unknown."""

    client = FakeKrakenRESTClient(
        add_order_mode=ACCEPT_THEN_LOST,
        echo_client_order_id=False,
    )
    store = SQLitePortfolioStore(str(tmp_path / "portfolio.db"))
    service = _service(client, store)

    result = service.execute_plan(_plan())

    # The exchange holds the order, but recovery could not attribute it.
    assert client.open_count == 1
    order = result.orders[0]
    assert order.status == "submit_unknown"
    assert order.kraken_order_id is None


def test_fill_restart_reconcile_and_portfolio_sync_proves_money_path(tmp_path):
    """A closed exchange order reconciles after restart and syncs into portfolio state."""

    client = FakeKrakenRESTClient(add_order_mode=ACCEPT)
    db_path = tmp_path / "portfolio.db"
    store = SQLitePortfolioStore(str(db_path))
    service = _service(client, store)

    result = service.execute_plan(_plan(plan_id="plan-fill"))
    assert len(client.add_order_calls) == 1
    assert result.orders
    order = result.orders[0]
    assert order.kraken_order_id is not None

    client.close_order(order.kraken_order_id, price=100.0)

    restarted = _service(client, store)
    restarted.load_open_orders_from_store()
    restarted.refresh_open_orders()
    restarted.reconcile_orders()

    persisted = store.get_order_by_reference(kraken_order_id=order.kraken_order_id)
    assert persisted is not None
    assert persisted.status == "closed"
    assert persisted.cumulative_base_filled == 1.0
    assert persisted.avg_fill_price == 100.0
    assert len(client.add_order_calls) == 1

    portfolio = _portfolio_service(client, db_path)
    sync_result = portfolio.sync()

    assert sync_result["new_trades"] == 1
    assert portfolio.last_sync_ok is True
    assert portfolio.get_drift_status().drift_flag is False

    trades = portfolio.store.get_trades()
    ledgers = portfolio.store.get_ledger_entries()
    assert any(trade.get("ordertxid") == order.kraken_order_id for trade in trades)
    assert any(entry.refid == trades[0].get("id") for entry in ledgers)


def test_partial_fill_reconciles_without_terminal_order_state(tmp_path):
    client = FakeKrakenRESTClient(add_order_mode=ACCEPT)
    store = SQLitePortfolioStore(str(tmp_path / "portfolio.db"))
    service = _service(client, store)

    result = service.execute_plan(_plan(plan_id="plan-partial"))
    order = result.orders[0]
    assert order.kraken_order_id is not None

    client.partial_fill_order(order.kraken_order_id, price=100.0, volume=0.25)
    client.partial_fill_order(order.kraken_order_id, price=120.0, volume=0.25)
    service.refresh_open_orders()

    persisted = store.get_order_by_reference(kraken_order_id=order.kraken_order_id)
    assert persisted is not None
    assert persisted.status == "partially_filled"
    assert persisted.cumulative_base_filled == 0.5
    assert persisted.avg_fill_price == 110.0
    assert client.open_count == 1


def test_fake_balance_failure_degrades_portfolio_sync(tmp_path):
    client = FakeKrakenRESTClient(add_order_mode=ACCEPT)
    portfolio = _portfolio_service(client, tmp_path / "portfolio.db")
    client.fail_balance_reads()

    result = portfolio.sync()

    assert result == {"new_trades": 0, "new_cash_flows": 1}
    assert portfolio.last_sync_ok is False
    assert portfolio.last_sync_reason == LIVE_SYNC_DEGRADED_REASON


def test_fake_trades_history_failure_degrades_portfolio_sync(tmp_path):
    client = FakeKrakenRESTClient(add_order_mode=ACCEPT)
    portfolio = _portfolio_service(client, tmp_path / "portfolio.db")
    client.fail_trades_history_reads()

    result = portfolio.sync()

    assert result == {"new_trades": 0, "new_cash_flows": 0}
    assert portfolio.last_sync_ok is False
    assert portfolio.last_sync_reason == LIVE_SYNC_TRADES_UNAVAILABLE_REASON


def test_fake_ledgers_failure_degrades_portfolio_sync(tmp_path):
    client = FakeKrakenRESTClient(add_order_mode=ACCEPT)
    portfolio = _portfolio_service(client, tmp_path / "portfolio.db")
    client.fail_ledger_reads()

    result = portfolio.sync()

    assert result == {"new_trades": 0, "new_cash_flows": 0}
    assert portfolio.last_sync_ok is False
    assert portfolio.last_sync_reason == LIVE_SYNC_LEDGERS_UNAVAILABLE_REASON


def test_fake_balance_stale_read_returns_prior_snapshot():
    client = FakeKrakenRESTClient(add_order_mode=ACCEPT)
    before = client.get_private("Balance")
    client.stale_balance_reads()

    response = client.add_order(
        {
            "pair": "XBTUSD",
            "type": "buy",
            "ordertype": "limit",
            "volume": "1.0",
            "price": "100.0",
            "cl_ord_id": "stale-balance-proof",
        }
    )
    txid = response["txid"][0]
    client.close_order(txid, price=100.0)

    stale = client.get_private("Balance")
    current = client.get_private("Balance")

    assert stale == before
    assert current != before
    assert current["XXBT"] == "1.00000000"
    assert current["ZUSD"] == "9900.00000000"


def test_stale_balance_after_fill_drifts_and_blocks_live_opening_risk(tmp_path):
    client = FakeKrakenRESTClient(add_order_mode=ACCEPT)
    db_path = tmp_path / "portfolio.db"
    store = SQLitePortfolioStore(str(db_path))
    service = _service(client, store)
    result = service.execute_plan(_plan(plan_id="plan-before-stale-balance"))
    order = result.orders[0]
    assert order.kraken_order_id is not None

    client.stale_balance_reads()
    client.close_order(order.kraken_order_id, price=100.0)

    portfolio = _portfolio_service(client, db_path)
    sync_result = portfolio.sync()

    assert sync_result["new_trades"] == 1
    assert portfolio.last_sync_ok is True
    assert portfolio.get_drift_status().drift_flag is True

    strategy_engine = StrategyEngine(
        _app_config(str(db_path)), _market_data(), portfolio
    )
    strategy_engine.refresh_runtime_snapshots()
    service_after_drift = _service_with_risk(
        client,
        portfolio.store,
        strategy_engine.get_risk_status,
    )

    blocked = service_after_drift.execute_plan(_plan(plan_id="plan-blocked-drift"))

    assert blocked.errors == [PORTFOLIO_DRIFT_ORDER_BLOCKED_MESSAGE]
    assert len(client.add_order_calls) == 1


def test_stale_ledgers_after_fill_drifts_and_blocks_live_opening_risk(tmp_path):
    client = FakeKrakenRESTClient(add_order_mode=ACCEPT)
    db_path = tmp_path / "portfolio.db"
    store = SQLitePortfolioStore(str(db_path))
    service = _service(client, store)
    result = service.execute_plan(_plan(plan_id="plan-before-stale-ledgers"))
    order = result.orders[0]
    assert order.kraken_order_id is not None

    client.stale_ledger_reads()
    client.close_order(order.kraken_order_id, price=100.0)

    portfolio = _portfolio_service(client, db_path)
    portfolio.sync()

    assert portfolio.last_sync_ok is True
    assert portfolio.get_drift_status().drift_flag is True

    strategy_engine = StrategyEngine(
        _app_config(str(db_path)), _market_data(), portfolio
    )
    strategy_engine.refresh_runtime_snapshots()
    service_after_drift = _service_with_risk(
        client,
        portfolio.store,
        strategy_engine.get_risk_status,
    )

    blocked = service_after_drift.execute_plan(
        _plan(plan_id="plan-blocked-ledger-drift")
    )

    assert blocked.errors == [PORTFOLIO_DRIFT_ORDER_BLOCKED_MESSAGE]
    assert len(client.add_order_calls) == 1


def test_trade_ledger_refs_without_matching_trades_stay_degraded_until_recovery(
    tmp_path,
):
    client = FakeKrakenRESTClient(add_order_mode=ACCEPT)
    client.set_clock(datetime.now(UTC).timestamp())
    db_path = tmp_path / "portfolio.db"
    store = SQLitePortfolioStore(str(db_path))
    service = _service(client, store)
    result = service.execute_plan(_plan(plan_id="plan-before-lagging-trades"))
    order = result.orders[0]
    assert order.kraken_order_id is not None

    client.stale_trades_history_reads(count=2)
    client.close_order(order.kraken_order_id, price=100.0)

    portfolio = _portfolio_service(client, db_path)
    sync_result = portfolio.sync()

    assert sync_result["new_trades"] == 0
    assert portfolio.last_sync_ok is False
    assert portfolio.last_sync_reason == LIVE_SYNC_TRADE_HISTORY_LAGGING_REASON

    second_result = portfolio.sync()

    assert second_result["new_trades"] == 0
    assert portfolio.last_sync_ok is False
    assert portfolio.last_sync_reason == LIVE_SYNC_TRADE_HISTORY_LAGGING_REASON

    recovered_result = portfolio.sync()

    assert recovered_result["new_trades"] == 1
    assert portfolio.last_sync_ok is True
    assert portfolio.last_sync_reason is None
    stored_trade_ids = {trade["id"] for trade in portfolio.store.get_trades()}
    ledger_refs = {
        entry.refid
        for entry in portfolio.store.get_ledger_entries()
        if entry.type == "trade"
    }
    assert ledger_refs <= stored_trade_ids


def test_backfilled_trade_ledger_ref_stays_degraded_until_recovery(tmp_path):
    client = FakeKrakenRESTClient(add_order_mode=ACCEPT)
    db_path = tmp_path / "portfolio.db"
    store = SQLitePortfolioStore(str(db_path))
    service = _service(client, store)
    result = service.execute_plan(_plan(plan_id="plan-before-backfilled-lag"))
    order = result.orders[0]
    assert order.kraken_order_id is not None

    verified_at = datetime(2026, 1, 2, 12, 10, tzinfo=UTC)
    portfolio = _portfolio_service(client, db_path, clock=lambda: verified_at)
    initial_sync = portfolio.sync()

    assert initial_sync["new_trades"] == 0
    assert portfolio.last_sync_ok is True
    assert portfolio.last_sync_at == verified_at

    client.stale_trades_history_reads(count=2)
    client.set_clock((verified_at - timedelta(minutes=5)).timestamp())
    client.close_order(order.kraken_order_id, price=100.0)

    first_lagged = portfolio.sync()

    assert first_lagged["new_trades"] == 0
    assert portfolio.last_sync_ok is False
    assert portfolio.last_sync_reason == LIVE_SYNC_TRADE_HISTORY_LAGGING_REASON
    assert portfolio.last_sync_at == verified_at

    second_lagged = portfolio.sync()

    assert second_lagged["new_trades"] == 0
    assert portfolio.last_sync_ok is False
    assert portfolio.last_sync_reason == LIVE_SYNC_TRADE_HISTORY_LAGGING_REASON

    recovered = portfolio.sync()

    assert recovered["new_trades"] == 1
    assert portfolio.last_sync_ok is True
    assert portfolio.last_sync_reason is None
    stored_trade_ids = {trade["id"] for trade in portfolio.store.get_trades()}
    ledger_refs = {
        entry.refid
        for entry in portfolio.store.get_ledger_entries()
        if entry.type == "trade"
    }
    assert ledger_refs <= stored_trade_ids


def test_reviewed_trade_ledger_refs_unblock_only_reviewed_refs(tmp_path):
    client = FakeKrakenRESTClient(add_order_mode=ACCEPT)
    db_path = tmp_path / "portfolio.db"
    store = SQLitePortfolioStore(str(db_path))
    service = _service(client, store)

    first_order = service.execute_plan(
        _plan(plan_id="plan-before-reviewed-lag-1")
    ).orders[0]
    second_order = service.execute_plan(
        _plan(plan_id="plan-before-reviewed-lag-2")
    ).orders[0]
    assert first_order.kraken_order_id is not None
    assert second_order.kraken_order_id is not None

    client.stale_trades_history_reads(count=4)
    client.close_order(first_order.kraken_order_id, price=100.0)
    client.close_order(second_order.kraken_order_id, price=100.0)

    portfolio = _portfolio_service(client, db_path)
    first_sync = portfolio.sync()

    assert first_sync["new_trades"] == 0
    assert portfolio.last_sync_ok is False
    assert portfolio.last_sync_reason is not None
    assert "trade" in portfolio.last_sync_reason.lower()

    unmatched_refs = sorted(portfolio.store.get_unmatched_trade_ledger_ref_times())
    assert len(unmatched_refs) == 2

    first_review_exit = cli.main(
        [
            "db-mark-trade-ref-reviewed",
            unmatched_refs[0],
            "--db-path",
            str(db_path),
            "--reviewed-by",
            "ops",
            "--reason",
            "Verified manually in Kraken",
            "--confirm",
            f"MARK {unmatched_refs[0]} REVIEWED",
        ]
    )

    assert first_review_exit == 0

    second_sync = portfolio.sync()

    assert second_sync["new_trades"] == 0
    assert portfolio.last_sync_ok is False
    assert portfolio.last_sync_reason is not None
    assert "trade" in portfolio.last_sync_reason.lower()
    assert sorted(portfolio.store.get_unmatched_trade_ledger_ref_times()) == [
        unmatched_refs[1]
    ]

    second_review_exit = cli.main(
        [
            "db-mark-trade-ref-reviewed",
            unmatched_refs[1],
            "--db-path",
            str(db_path),
            "--reviewed-by",
            "ops",
            "--reason",
            "Verified manually in Kraken",
            "--confirm",
            f"MARK {unmatched_refs[1]} REVIEWED",
        ]
    )

    assert second_review_exit == 0

    recovered_sync = portfolio.sync()

    assert recovered_sync["new_trades"] == 0
    assert portfolio.last_sync_ok is True
    assert portfolio.last_sync_reason is None
    assert portfolio.store.get_unmatched_trade_ledger_ref_times() == {}


def test_real_account_truth_provider_gates_live_opening_risk(tmp_path):
    healthy_client = FakeKrakenRESTClient(add_order_mode=ACCEPT)
    healthy_db = tmp_path / "healthy.db"
    healthy_portfolio = _portfolio_service(healthy_client, healthy_db)
    healthy_portfolio.sync()
    healthy_balance_reads_after_sync = healthy_client.balance_read_count
    healthy_service = _service_with_account_truth(
        healthy_client,
        cast(SQLitePortfolioStore, healthy_portfolio.store),
        healthy_portfolio,
    )

    healthy_result = healthy_service.execute_plan(_plan("plan-healthy-provider"))

    assert healthy_result.success is True
    assert len(healthy_client.add_order_calls) == 1
    assert healthy_client.balance_read_count == healthy_balance_reads_after_sync

    multi_client = FakeKrakenRESTClient(add_order_mode=ACCEPT)
    multi_db = tmp_path / "multi.db"
    multi_portfolio = _portfolio_service(multi_client, multi_db)
    multi_portfolio.sync()
    multi_portfolio._last_balance_reconcile_at = datetime.now(UTC) - timedelta(
        seconds=10
    )
    multi_balance_reads_before_plan = multi_client.balance_read_count
    multi_service = _service_with_account_truth(
        multi_client,
        cast(SQLitePortfolioStore, multi_portfolio.store),
        multi_portfolio,
    )

    multi_result = multi_service.execute_plan(
        ExecutionPlan(
            plan_id="plan-multi-provider",
            generated_at=datetime.now(UTC),
            actions=[
                _action(pair="XBTUSD"),
                _action(pair="ETHUSD"),
            ],
            metadata={"order_type": "limit"},
        )
    )

    assert multi_result.success is True
    assert len(multi_client.add_order_calls) == 2
    assert multi_client.balance_read_count == multi_balance_reads_before_plan + 1

    drift_client = FakeKrakenRESTClient(add_order_mode=ACCEPT)
    drift_db = tmp_path / "drift.db"
    drift_store = SQLitePortfolioStore(str(drift_db))
    drift_seed_service = _service(drift_client, drift_store)
    drift_seed_order = drift_seed_service.execute_plan(
        _plan("plan-seed-drift-provider")
    ).orders[0]
    assert drift_seed_order.kraken_order_id is not None
    drift_client.close_order(drift_seed_order.kraken_order_id, price=100.0)
    drift_portfolio = _portfolio_service(drift_client, drift_db)
    drift_portfolio.sync()
    assert drift_portfolio.get_drift_status().drift_flag is False
    drift_client._balances["ZUSD"] = Decimal("9889.0")
    drift_portfolio._last_balance_reconcile_at = datetime.now(UTC) - timedelta(
        seconds=10
    )
    drift_service = _service_with_account_truth(
        drift_client,
        cast(SQLitePortfolioStore, drift_portfolio.store),
        drift_portfolio,
    )

    drift_result = drift_service.execute_plan(_plan("plan-drift-provider"))

    assert drift_result.success is False
    assert drift_result.errors == [PORTFOLIO_DRIFT_ORDER_BLOCKED_MESSAGE]
    assert len(drift_client.add_order_calls) == 1

    unavailable_client = FakeKrakenRESTClient(add_order_mode=ACCEPT)
    unavailable_db = tmp_path / "unavailable.db"
    unavailable_portfolio = _portfolio_service(unavailable_client, unavailable_db)
    unavailable_portfolio.sync()
    unavailable_client.fail_balance_reads(count=1)
    unavailable_portfolio._last_balance_reconcile_at = datetime.now(UTC) - timedelta(
        seconds=10
    )
    unavailable_service = _service_with_account_truth(
        unavailable_client,
        cast(SQLitePortfolioStore, unavailable_portfolio.store),
        unavailable_portfolio,
    )

    unavailable_result = unavailable_service.execute_plan(
        _plan("plan-unavailable-provider")
    )

    assert unavailable_result.success is False
    assert len(unavailable_client.add_order_calls) == 0

    unmatched_client = FakeKrakenRESTClient(add_order_mode=ACCEPT)
    unmatched_db = tmp_path / "unmatched.db"
    unmatched_store = SQLitePortfolioStore(str(unmatched_db))
    seed_service = _service(unmatched_client, unmatched_store)
    seed_order = seed_service.execute_plan(_plan("plan-seed-unmatched")).orders[0]
    assert seed_order.kraken_order_id is not None
    unmatched_client.stale_trades_history_reads(count=2)
    unmatched_client.close_order(seed_order.kraken_order_id, price=100.0)
    unmatched_portfolio = _portfolio_service(unmatched_client, unmatched_db)
    unmatched_portfolio.sync()
    unmatched_service = _service_with_account_truth(
        unmatched_client,
        cast(SQLitePortfolioStore, unmatched_portfolio.store),
        unmatched_portfolio,
    )

    unmatched_result = unmatched_service.execute_plan(_plan("plan-unmatched-provider"))

    assert unmatched_result.success is False
    assert unmatched_result.errors == [PORTFOLIO_SYNC_ORDER_BLOCKED_MESSAGE]
    assert len(unmatched_client.add_order_calls) == 1

    reducing_plan = ExecutionPlan(
        plan_id="plan-reduce-close-provider",
        generated_at=datetime.now(UTC),
        actions=[
            _action(
                action_type="reduce",
                current_base_size=1.0,
                target_base_size=0.5,
                target_notional_usd=50.0,
            ),
            _action(
                action_type="close",
                current_base_size=1.0,
                target_base_size=0.0,
                target_notional_usd=0.0,
            ),
        ],
        metadata={"order_type": "limit"},
    )

    reducing_result = unmatched_service.execute_plan(reducing_plan)

    assert reducing_result.success is True
    assert len(unmatched_client.add_order_calls) == 3


def test_strategy_generated_rs_rotation_plan_exercises_live_account_truth_gate(
    tmp_path,
):
    client = FakeKrakenRESTClient(add_order_mode=ACCEPT)
    portfolio, strategy_engine, execution_service = _decision_loop_services(
        client, tmp_path / "decision-loop-live.db"
    )

    plan = strategy_engine.run_cycle(now=datetime.now(UTC))

    assert strategy_engine.last_cycle_intents
    assert plan.actions
    opening_actions = [
        action
        for action in plan.actions
        if action.strategy_id == "rs_rotation"
        and action.action_type in {"open", "increase"}
    ]
    assert len(opening_actions) == 1
    assert opening_actions[0].blocked is False

    portfolio._last_balance_reconcile_at = datetime.now(UTC) - timedelta(seconds=10)
    balance_reads_before_execute = client.balance_read_count

    execution = execution_service.execute_plan(plan)

    assert execution.success is True
    assert len(client.add_order_calls) == 1
    assert client.balance_read_count == balance_reads_before_execute + 1
    order = execution.orders[0]
    assert order.strategy_id == "rs_rotation"
    assert order.kraken_order_id is not None
    assert order.raw_request["cl_ord_id"] == order.local_id
    forced_balance_index = max(
        index
        for index, call in enumerate(client.call_log)
        if call.get("event") == "get_private" and call.get("endpoint") == "Balance"
    )
    add_order_index = next(
        index
        for index, call in enumerate(client.call_log)
        if call.get("event") == "add_order"
    )
    assert forced_balance_index < add_order_index

    client.close_order(order.kraken_order_id, price=100.0)
    execution_service.refresh_open_orders()
    execution_service.reconcile_orders()
    sync_result = portfolio.sync()

    assert sync_result["new_trades"] == 1
    assert portfolio.last_sync_ok is True
    assert portfolio.store.get_trades(limit=10)
    assert any(entry.type == "trade" for entry in portfolio.store.get_ledger_entries())
    assert any(
        position.strategy_tag == "rs_rotation" for position in portfolio.get_positions()
    )

    blocked_client = FakeKrakenRESTClient(add_order_mode=ACCEPT)
    blocked_portfolio, blocked_strategy_engine, blocked_execution_service = (
        _decision_loop_services(blocked_client, tmp_path / "decision-loop-blocked.db")
    )
    blocked_plan = blocked_strategy_engine.run_cycle(now=datetime.now(UTC))
    assert blocked_plan.actions
    blocked_client.fail_balance_reads(count=1)
    blocked_portfolio._last_balance_reconcile_at = datetime.now(UTC) - timedelta(
        seconds=10
    )

    blocked_execution = blocked_execution_service.execute_plan(blocked_plan)

    assert blocked_execution.success is False
    assert blocked_execution.errors == [PORTFOLIO_SYNC_ORDER_BLOCKED_MESSAGE]
    assert len(blocked_client.add_order_calls) == 0
    assert blocked_portfolio.last_sync_ok is False


def test_real_account_truth_timeout_recovery_requires_successful_balance_reconcile(
    tmp_path,
):
    client = FakeKrakenRESTClient(add_order_mode=ACCEPT)
    db_path = tmp_path / "timeout-recovery.db"
    portfolio = _portfolio_service(client, db_path)
    portfolio.sync()
    portfolio._last_balance_reconcile_at = datetime.now(UTC) - timedelta(seconds=10)
    portfolio._set_last_sync_state(
        ok=False,
        reason=LIVE_ACCOUNT_TRUTH_REFRESH_TIMEOUT_REASON,
    )
    client.fail_balance_reads(count=1)
    service = _service_with_account_truth(
        client,
        cast(SQLitePortfolioStore, portfolio.store),
        portfolio,
    )

    failed_refresh = service.execute_plan(_plan("plan-timeout-recovery-fails-closed"))

    assert failed_refresh.success is False
    assert failed_refresh.errors == [PORTFOLIO_SYNC_ORDER_BLOCKED_MESSAGE]
    assert len(client.add_order_calls) == 0
    assert portfolio.last_sync_ok is False
    assert portfolio.last_sync_reason == LIVE_SYNC_DEGRADED_REASON

    portfolio._last_balance_reconcile_at = datetime.now(UTC) - timedelta(seconds=10)
    portfolio._set_last_sync_state(
        ok=False,
        reason=LIVE_ACCOUNT_TRUTH_REFRESH_TIMEOUT_REASON,
    )

    recovered = service.execute_plan(_plan("plan-timeout-recovery-succeeds"))

    assert recovered.success is True
    assert len(client.add_order_calls) == 1
    assert portfolio.last_sync_ok is True
    assert portfolio.last_sync_reason is None


def test_ambiguous_migrated_review_blocks_until_re_reviewed(tmp_path):
    client = FakeKrakenRESTClient(add_order_mode=ACCEPT)
    db_path = tmp_path / "ambiguous-review.db"
    store = SQLitePortfolioStore(str(db_path))
    store.close()

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO reviewed_trade_ledger_ref_entries (
                refid,
                ledger_entry_id,
                reviewed_at,
                reviewed_by,
                reason,
                context_json,
                review_event_id
            ) VALUES (
                'T-FUTURE',
                'L-future',
                '2026-01-02T03:04:05+00:00',
                'ops',
                'legacy future id',
                '{}',
                NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO meta (key, value)
            VALUES ('schema_version', '14')
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """
        )

    portfolio = _portfolio_service(client, db_path)
    portfolio.store.save_ledger_entry(
        LedgerEntry(
            id="L-future",
            time=10.0,
            type="trade",
            subtype="",
            aclass="currency",
            asset="USD",
            amount=Decimal("10000"),
            fee=Decimal("0"),
            balance=None,
            refid="T-FUTURE",
            misc=None,
            raw={},
        )
    )

    portfolio.sync()
    blocked_service = _service_with_account_truth(
        client,
        cast(SQLitePortfolioStore, portfolio.store),
        portfolio,
    )
    blocked = blocked_service.execute_plan(_plan("plan-ambiguous-review-blocked"))

    assert blocked.success is False
    assert blocked.errors == [PORTFOLIO_SYNC_ORDER_BLOCKED_MESSAGE]
    assert len(client.add_order_calls) == 0
    assert portfolio.store.get_unmatched_trade_ledger_ref_times() == {"T-FUTURE": 10.0}

    review = portfolio.store.mark_trade_ledger_ref_reviewed(
        refid="T-FUTURE",
        reviewed_by="ops",
        reason="review after migration cleanup",
        ledger_entry_ids=["L-future"],
        context={"source": "test"},
    )
    portfolio._bootstrapped = False
    recovered_sync = portfolio.sync()
    allowed = blocked_service.execute_plan(_plan("plan-ambiguous-review-allowed"))

    assert review.ledger_entry_ids == ["L-future"]
    assert recovered_sync["new_trades"] == 0
    assert portfolio.last_sync_ok is True
    assert allowed.success is True
    assert len(client.add_order_calls) == 1


def test_old_trade_ledger_ref_lag_escalates_and_alerts_once(tmp_path):
    client = FakeKrakenRESTClient(add_order_mode=ACCEPT)
    db_path = tmp_path / "portfolio.db"
    store = SQLitePortfolioStore(str(db_path))
    service = _service(client, store)
    result = service.execute_plan(_plan(plan_id="plan-before-escalated-lag"))
    order = result.orders[0]
    assert order.kraken_order_id is not None

    client.stale_trades_history_reads(count=2)
    client.close_order(order.kraken_order_id, price=100.0)
    alerts = _RecordingAlerts()
    portfolio = _portfolio_service(client, db_path, alert_notifier=alerts)

    portfolio.sync()
    portfolio.sync()

    expected_reason = live_sync_trade_history_lag_escalated_reason(600)
    assert portfolio.last_sync_ok is False
    assert portfolio.last_sync_reason == expected_reason
    assert len(alerts.events) == 1
    assert alerts.events[0]["event"] == "portfolio_trade_history_lag_escalated"
    assert alerts.events[0]["title"] == LIVE_SYNC_TRADE_HISTORY_LAG_ALERT_TITLE
    assert alerts.events[0]["message"] == expected_reason


def test_fake_kraken_trade_ledgers_refid_matches_trades_history_id(tmp_path):
    client = FakeKrakenRESTClient(add_order_mode=ACCEPT)
    store = SQLitePortfolioStore(str(tmp_path / "portfolio.db"))
    service = _service(client, store)
    result = service.execute_plan(_plan(plan_id="plan-refid-proof"))
    order = result.orders[0]
    assert order.kraken_order_id is not None

    client.partial_fill_order(order.kraken_order_id, price=100.0, volume=0.25)
    client.close_order(order.kraken_order_id, price=110.0)

    trades = client.get_private("TradesHistory")["trades"]
    ledgers = client.get_ledgers()["ledger"]
    trade_ids = set(trades)
    trade_refids = {
        str(entry["refid"])
        for entry in ledgers.values()
        if entry.get("type") == "trade"
    }

    assert len(trade_ids) == 2
    assert trade_refids == trade_ids

    portfolio = _portfolio_service(client, tmp_path / "portfolio.db")
    portfolio.sync()

    assert portfolio.last_sync_ok is True
    assert {trade["id"] for trade in portfolio.store.get_trades()} == trade_ids


def test_live_opening_risk_blocked_when_portfolio_sync_degraded(tmp_path):
    client = FakeKrakenRESTClient(add_order_mode=ACCEPT)
    store = SQLitePortfolioStore(str(tmp_path / "portfolio.db"))
    service = _service_with_risk(client, store, _degraded_risk)

    result = service.execute_plan(_plan(plan_id="plan-sync-block"))

    assert result.errors
    assert len(client.add_order_calls) == 0
    assert result.orders
    assert result.orders[0].status == "rejected"
    assert result.orders[0].last_error == PORTFOLIO_SYNC_ORDER_BLOCKED_MESSAGE
    assert result.errors == [PORTFOLIO_SYNC_ORDER_BLOCKED_MESSAGE]
    assert "API Down" not in (result.orders[0].last_error or "")
    assert "boundary" not in (result.orders[0].last_error or "")
    assert "opening risk" not in (result.orders[0].last_error or "")
    assert "account truth" not in (result.orders[0].last_error or "")


def test_live_opening_risk_blocked_by_strategy_engine_cached_portfolio_sync(
    tmp_path,
):
    client = FakeKrakenRESTClient(add_order_mode=ACCEPT)
    db_path = tmp_path / "portfolio.db"
    portfolio = _portfolio_service(client, db_path)
    portfolio._last_sync_ok = False
    portfolio._last_sync_reason = "Live balance reconciliation unavailable: API Down"
    portfolio._last_sync_at = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)
    strategy_engine = StrategyEngine(
        _app_config(str(db_path)),
        _market_data(),
        portfolio,
    )
    strategy_engine.refresh_runtime_snapshots()
    service = _service_with_risk(
        client,
        portfolio.store,
        strategy_engine.get_risk_status,
    )

    result = service.execute_plan(_plan(plan_id="plan-real-provider-sync-block"))

    assert strategy_engine.get_risk_status().portfolio_sync_ok is False
    assert result.errors
    assert len(client.add_order_calls) == 0
    assert result.orders
    assert result.orders[0].status == "rejected"
    assert result.orders[0].last_error == PORTFOLIO_SYNC_ORDER_BLOCKED_MESSAGE
    assert result.errors == [PORTFOLIO_SYNC_ORDER_BLOCKED_MESSAGE]
    assert "API Down" not in (result.orders[0].last_error or "")


def test_live_opening_risk_blocked_by_strategy_engine_stale_portfolio_sync(
    tmp_path,
):
    client = FakeKrakenRESTClient(add_order_mode=ACCEPT)
    db_path = tmp_path / "portfolio.db"
    portfolio = _portfolio_service(client, db_path)
    portfolio._last_sync_ok = True
    portfolio._last_sync_reason = None
    portfolio._last_sync_at = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)
    strategy_engine = StrategyEngine(
        _app_config(str(db_path)),
        _market_data(),
        portfolio,
    )
    strategy_engine.refresh_runtime_snapshots()
    service = _service_with_risk(
        client,
        portfolio.store,
        strategy_engine.get_risk_status,
    )

    result = service.execute_plan(_plan(plan_id="plan-real-provider-stale-sync-block"))

    status = strategy_engine.get_risk_status()
    assert status.portfolio_sync_ok is False
    assert status.portfolio_sync_reason == live_sync_stale_reason(600)
    assert result.errors == [PORTFOLIO_SYNC_ORDER_BLOCKED_MESSAGE]
    assert len(client.add_order_calls) == 0


def test_live_opening_risk_blocked_when_portfolio_drift_detected(tmp_path):
    client = FakeKrakenRESTClient(add_order_mode=ACCEPT)
    store = SQLitePortfolioStore(str(tmp_path / "portfolio.db"))
    service = _service_with_risk(client, store, _drift_risk)

    result = service.execute_plan(_plan(plan_id="plan-drift-block"))

    assert result.errors == [PORTFOLIO_DRIFT_ORDER_BLOCKED_MESSAGE]
    assert len(client.add_order_calls) == 0
    assert result.orders[0].status == "rejected"


def test_live_risk_reducing_actions_not_blocked_by_portfolio_drift(tmp_path):
    client = FakeKrakenRESTClient(add_order_mode=ACCEPT)
    store = SQLitePortfolioStore(str(tmp_path / "portfolio.db"))
    service = _service_with_risk(client, store, _drift_risk)

    plan = ExecutionPlan(
        plan_id="plan-reduce-drift",
        generated_at=datetime.now(UTC),
        actions=[
            _action(
                action_type="close",
                current_base_size=1.0,
                target_base_size=0.0,
                target_notional_usd=0.0,
            ),
            _action(
                action_type="reduce",
                current_base_size=1.0,
                target_base_size=0.5,
                target_notional_usd=50.0,
            ),
        ],
        metadata={"order_type": "limit"},
        emergency_reduce_only=True,
    )

    result = service.execute_plan(plan)

    assert not result.errors
    assert len(client.add_order_calls) == 2
    assert [order.status for order in result.orders] == ["open", "open"]


def test_cancel_all_reaches_fake_kraken_when_portfolio_drift_detected(tmp_path):
    client = FakeKrakenRESTClient(add_order_mode=ACCEPT)
    store = SQLitePortfolioStore(str(tmp_path / "portfolio.db"))
    service = _service_with_risk(client, store, _drift_risk)

    service.cancel_all()

    assert client.cancel_all_calls == 1


def test_live_risk_reducing_actions_not_blocked_by_degraded_portfolio_sync(tmp_path):
    client = FakeKrakenRESTClient(add_order_mode=ACCEPT)
    store = SQLitePortfolioStore(str(tmp_path / "portfolio.db"))
    service = _service_with_risk(client, store, _degraded_risk)

    plan = ExecutionPlan(
        plan_id="plan-reduce-sync-degraded",
        generated_at=datetime.now(UTC),
        actions=[
            _action(
                action_type="close",
                current_base_size=1.0,
                target_base_size=0.0,
                target_notional_usd=0.0,
            ),
            _action(
                action_type="reduce",
                current_base_size=1.0,
                target_base_size=0.5,
                target_notional_usd=50.0,
            ),
        ],
        metadata={"order_type": "limit"},
        emergency_reduce_only=True,
    )

    result = service.execute_plan(plan)

    assert not result.errors
    assert len(client.add_order_calls) == 2
    assert [order.status for order in result.orders] == ["open", "open"]


def test_cancel_all_reaches_fake_kraken_when_portfolio_sync_degraded(tmp_path):
    client = FakeKrakenRESTClient(add_order_mode=ACCEPT)
    store = SQLitePortfolioStore(str(tmp_path / "portfolio.db"))
    service = _service_with_risk(client, store, _degraded_risk)

    service.cancel_all()

    assert client.cancel_all_calls == 1
