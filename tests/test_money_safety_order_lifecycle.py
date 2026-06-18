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

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, Dict, cast
from unittest.mock import MagicMock

from krakked.config import ExecutionConfig
from krakked.connection.rest_client import KrakenRESTClient
from krakked.execution.adapter import KrakenExecutionAdapter
from krakked.execution.oms import ExecutionService
from krakked.market_data.models import PairMetadata
from krakked.portfolio.store import SQLitePortfolioStore
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
    return SimpleNamespace(kill_switch_active=False)


def _live_config() -> ExecutionConfig:
    return ExecutionConfig(
        mode="live",
        validate_only=False,
        allow_live_trading=True,
        paper_tests_completed=True,
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
        base, quote = pair[:3], pair[3:]
        rest_symbol = f"{base}/{quote}"
        return PairMetadata(
            canonical=pair,
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
    md.get_best_bid_ask.return_value = {"bid": mid_price - 0.5, "ask": mid_price + 0.5}
    return md


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


def _service(client: FakeKrakenRESTClient, store: SQLitePortfolioStore) -> ExecutionService:
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
