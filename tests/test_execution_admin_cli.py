from typing import Any, Optional

import pytest

from kraken_bot.config import ExecutionConfig
from kraken_bot.execution import admin_cli
from kraken_bot.execution.models import LocalOrder


def test_panic_cli_triggers_cancel_all(monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    calls: list[str] = []

    class _DummyService:
        def cancel_all(self) -> None:
            calls.append("cancel_all")

    dummy_service = _DummyService()
    monkeypatch.setattr(
        admin_cli, "_build_service", lambda db_path, allow_interactive_setup: dummy_service
    )

    exit_code = admin_cli.main(["panic"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Panic cancel-all issued." in captured.out
    assert calls == ["cancel_all"]


def test_panic_cli_reconciles_and_persists(monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
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

    class _FakeStore:
        def __init__(self) -> None:
            self.events: list[str] = []

        def update_order_status(self, *, local_id: str, status: str, kraken_order_id: Optional[str] = None, **_: Any) -> None:
            self.events.append(f"persist:{local_id}:{status}")
            events.append(f"persist:{status}")

    adapter = _FakeAdapter()
    store = _FakeStore()
    service = admin_cli.ExecutionService(adapter=adapter, store=store)

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

    monkeypatch.setattr(admin_cli, "_build_service", lambda db_path, allow_interactive_setup: service)

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
