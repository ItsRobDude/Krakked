from types import SimpleNamespace
from typing import Any, Optional, cast
from unittest.mock import MagicMock

import pytest

from krakked.config import ExecutionConfig
from krakked.execution import admin_cli
from krakked.execution.adapter import ExecutionAdapter
from krakked.execution.models import LocalOrder
from krakked.market_data.models import PairMetadata
from krakked.portfolio.store import PortfolioStore
from krakked.strategy.models import RiskStatus


def test_panic_cli_triggers_cancel_all(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    calls: list[str] = []

    class _DummyService:
        def cancel_all(self) -> None:
            calls.append("cancel_all")

    dummy_service = _DummyService()
    monkeypatch.setattr(
        admin_cli,
        "_build_service",
        lambda db_path, allow_interactive_setup: dummy_service,
    )

    exit_code = admin_cli.main(["panic"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Panic cancel-all issued." in captured.out
    assert calls == ["cancel_all"]


def test_panic_cli_reconciles_and_persists(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    events: list[str] = []

    class _FakeClient:
        def cancel_all_orders(self) -> None:
            events.append("cancel_all_orders")

        def get_open_orders(self, params: Any = None) -> dict:
            events.append("get_open_orders")
            return {"open": {"OIDOPEN": {"userref": 99, "status": "open"}}}

        def get_closed_orders(self) -> dict:
            events.append("get_closed_orders")
            return {"closed": {"OIDOPEN": {"userref": 99, "status": "canceled"}}}

    class _FakeAdapter:
        def __init__(self) -> None:
            self.config = ExecutionConfig(validate_only=True)
            self.client = _FakeClient()

        def cancel_all_orders(self) -> None:
            self.client.cancel_all_orders()

        def submit_order(
            self, order: LocalOrder
        ) -> LocalOrder:  # pragma: no cover - protocol stub
            return order

        def cancel_order(
            self, order: LocalOrder
        ) -> None:  # pragma: no cover - protocol stub
            return None

    class _FakeStore:
        def __init__(self) -> None:
            self.events: list[str] = []

        def update_order_status(
            self,
            *,
            local_id: str,
            status: str,
            kraken_order_id: Optional[str] = None,
            **_: Any,
        ) -> None:
            self.events.append(f"persist:{local_id}:{status}")
            events.append(f"persist:{status}")

    def fake_risk_status() -> RiskStatus:
        return RiskStatus(
            kill_switch_active=False,
            daily_drawdown_pct=0.0,
            drift_flag=False,
            drift_info=None,
            total_exposure_pct=0.0,
            manual_exposure_pct=0.0,
            per_asset_exposure_pct={},
            per_strategy_exposure_pct={},
        )

    adapter = _FakeAdapter()
    store = _FakeStore()
    market_data = MagicMock()
    market_data.get_pair_metadata_or_raise.return_value = PairMetadata(
        canonical="ETHUSD",
        base="ETH",
        quote="USD",
        rest_symbol="ETH/USD",
        ws_symbol="ETH/USD",
        raw_name="ETHUSD",
        price_decimals=1,
        volume_decimals=8,
        lot_size=0.00000001,
        min_order_size=0.0001,
        status="online",
    )
    market_data.get_best_bid_ask.return_value = None
    service = admin_cli.ExecutionService(
        adapter=cast(ExecutionAdapter, adapter),
        store=cast(PortfolioStore, store),
        market_data=market_data,
        risk_status_provider=fake_risk_status,
    )
    assert service is not None

    order = LocalOrder(
        local_id="local-1",
        plan_id="plan",
        strategy_id="strategy",
        pair="ETHUSD",
        side="buy",
        order_type="limit",
        userref=99,
        requested_base_size=1.0,
        requested_price=10.0,
        status="open",
    )
    service.register_order(order)

    monkeypatch.setattr(
        admin_cli, "_build_service", lambda db_path, allow_interactive_setup: service
    )

    exit_code = admin_cli.main(["panic"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Panic cancel-all issued." in captured.out
    assert events == [
        "cancel_all_orders",
        "get_open_orders",
        "persist:open",
        "get_closed_orders",
        "persist:canceled",
    ]


def test_admin_cli_builds_service_with_risk_provider(tmp_path, monkeypatch):
    db_path = tmp_path / "portfolio.db"

    config = SimpleNamespace(
        execution=ExecutionConfig(mode="live", validate_only=True),
        portfolio=SimpleNamespace(auto_migrate_schema=False),
    )

    monkeypatch.setattr(admin_cli, "load_config", lambda: config)
    monkeypatch.setattr(
        admin_cli, "bootstrap", lambda allow_interactive_setup: (None, config, None)
    )

    captured_provider: list[Any] = []

    class _RecordingService:
        def __init__(
            self,
            *,
            risk_status_provider: Any,
            **_: Any,
        ) -> None:
            captured_provider.append(risk_status_provider)

        def load_open_orders_from_store(self) -> None:
            return None

    monkeypatch.setattr(admin_cli, "ExecutionService", _RecordingService)

    service = admin_cli._build_service(str(db_path), allow_interactive_setup=False)
    assert service is not None

    assert (
        captured_provider and captured_provider[0] is admin_cli._admin_cli_risk_status
    )
    status = captured_provider[0]()
    assert status == RiskStatus(
        kill_switch_active=False,
        daily_drawdown_pct=0.0,
        drift_flag=False,
        total_exposure_pct=0.0,
        manual_exposure_pct=0.0,
        per_asset_exposure_pct={},
        per_strategy_exposure_pct={},
        drift_info={"source": "admin_cli"},
    )


class _SubmitIntentStore:
    def __init__(self, order: LocalOrder) -> None:
        self.orders = {order.local_id: order}

    def get_open_orders(self, *args: Any, **kwargs: Any) -> list[LocalOrder]:
        return [
            order
            for order in self.orders.values()
            if order.status in {"pending_submit", "submit_unknown", "open"}
        ]

    def update_order_status(
        self,
        *,
        local_id: str,
        status: str,
        kraken_order_id: Optional[str] = None,
        last_error: Optional[str] = None,
        **_: Any,
    ) -> None:
        order = self.orders[local_id]
        order.status = status
        if kraken_order_id:
            order.kraken_order_id = kraken_order_id
        if last_error:
            order.last_error = last_error


class _SubmitIntentClient:
    def __init__(
        self,
        *,
        open_matches: Optional[dict] = None,
        closed_matches: Optional[dict] = None,
    ) -> None:
        self.open_matches = open_matches or {}
        self.closed_matches = closed_matches or {}
        self.add_order_calls: list[dict] = []
        self.open_order_calls: list[dict] = []
        self.closed_order_calls: list[dict] = []

    def get_open_orders(self, params: Optional[dict] = None) -> dict:
        self.open_order_calls.append(dict(params or {}))
        return {"open": self.open_matches}

    def get_closed_orders(self, params: Optional[dict] = None) -> dict:
        self.closed_order_calls.append(dict(params or {}))
        return {"closed": self.closed_matches}

    def add_order(self, payload: dict) -> dict:
        self.add_order_calls.append(dict(payload))
        return {"error": []}


class _SubmitIntentService:
    def __init__(
        self, store: _SubmitIntentStore, client: _SubmitIntentClient
    ) -> None:
        self.store = store
        self.adapter = SimpleNamespace(client=client)
        self.open_orders: dict[str, LocalOrder] = {
            order.local_id: order for order in store.orders.values()
        }

    def _sync_remote_order(
        self,
        kraken_id: str,
        payload: dict,
        *,
        is_closed: bool,
        client_order_id: Optional[str] = None,
    ) -> None:
        order = next(iter(self.store.orders.values()))
        status = payload.get("status") or ("closed" if is_closed else "open")
        self.store.update_order_status(
            local_id=order.local_id,
            status=status,
            kraken_order_id=kraken_id,
        )


def _submit_unknown_order() -> LocalOrder:
    return LocalOrder(
        local_id="local-1",
        plan_id="plan",
        strategy_id="strategy",
        pair="XBTUSD",
        side="buy",
        order_type="limit",
        status="submit_unknown",
        raw_request={"cl_ord_id": "client-1"},
    )


def test_reconcile_submit_intents_recovers_exact_match(monkeypatch, capsys):
    order = _submit_unknown_order()
    store = _SubmitIntentStore(order)
    client = _SubmitIntentClient(
        open_matches={"OID123": {"status": "open", "cl_ord_id": "client-1"}}
    )
    service = _SubmitIntentService(store, client)
    monkeypatch.setattr(
        admin_cli, "_build_service", lambda db_path, allow_interactive_setup: service
    )

    exit_code = admin_cli.main(["reconcile-submit-intents", "--local-id", "local-1"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "recovered via OpenOrders" in captured.out
    assert order.status == "open"
    assert order.kraken_order_id == "OID123"


def test_clear_submit_unknown_refuses_when_kraken_match_exists(monkeypatch, capsys):
    order = _submit_unknown_order()
    store = _SubmitIntentStore(order)
    client = _SubmitIntentClient(
        open_matches={"OID123": {"status": "open", "cl_ord_id": "client-1"}}
    )
    service = _SubmitIntentService(store, client)
    monkeypatch.setattr(
        admin_cli, "_build_service", lambda db_path, allow_interactive_setup: service
    )

    exit_code = admin_cli.main(
        ["clear-submit-unknown", "--local-id", "local-1", "--confirmed-absent"]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Refusing to clear" in captured.out
    assert order.status == "submit_unknown"


def test_clear_submit_unknown_marks_absent_after_confirmed_no_match(
    monkeypatch, capsys
):
    order = _submit_unknown_order()
    store = _SubmitIntentStore(order)
    client = _SubmitIntentClient()
    service = _SubmitIntentService(store, client)
    monkeypatch.setattr(
        admin_cli, "_build_service", lambda db_path, allow_interactive_setup: service
    )

    exit_code = admin_cli.main(
        ["clear-submit-unknown", "--local-id", "local-1", "--confirmed-absent"]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "marked submit_absent" in captured.out
    assert order.status == "submit_absent"
    assert "confirmed absence" in (order.last_error or "")


def test_probe_client_order_id_uses_validate_only_payload(monkeypatch, capsys):
    order = _submit_unknown_order()
    store = _SubmitIntentStore(order)
    client = _SubmitIntentClient()
    service = _SubmitIntentService(store, client)
    monkeypatch.setattr(
        admin_cli, "_build_service", lambda db_path, allow_interactive_setup: service
    )

    exit_code = admin_cli.main(
        [
            "probe-cl-ord-id",
            "--pair",
            "XBTUSD",
            "--volume",
            "0.001",
            "--price",
            "50000",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "validate-only probe passed" in captured.out
    assert client.add_order_calls
    payload = client.add_order_calls[0]
    assert payload["validate"] == 1
    assert payload["cl_ord_id"]
    assert client.open_order_calls == [{"cl_ord_id": payload["cl_ord_id"]}]
    assert client.closed_order_calls == [{"cl_ord_id": payload["cl_ord_id"]}]
