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

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Dict, cast
from unittest.mock import MagicMock

from krakked import cli
from krakked.config import (
    AppConfig,
    ExecutionConfig,
    MarketDataConfig,
    PortfolioConfig,
    RegionCapabilities,
    RegionProfile,
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
from krakked.market_data.models import PairMetadata
from krakked.portfolio.manager import PortfolioService
from krakked.portfolio.store import SQLitePortfolioStore
from krakked.portfolio.sync_status import (
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
    healthy_service = _service_with_account_truth(
        healthy_client,
        cast(SQLitePortfolioStore, healthy_portfolio.store),
        healthy_portfolio,
    )

    healthy_result = healthy_service.execute_plan(_plan("plan-healthy-provider"))

    assert healthy_result.success is True
    assert len(healthy_client.add_order_calls) == 1

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
