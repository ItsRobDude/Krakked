"""Admin CLI tests for tri-state clear and operator force-resolve."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import MagicMock

import pytest

from krakked.config import ExecutionConfig
from krakked.execution import admin_cli
from krakked.execution.adapter import KrakenExecutionAdapter
from krakked.execution.models import LocalOrder
from krakked.execution.oms import ExecutionService
from krakked.market_data.models import PairMetadata
from krakked.portfolio.store import SQLitePortfolioStore


class _FakeStore:
    def __init__(self, orders: list[LocalOrder]) -> None:
        self._orders = orders
        self.updates: list[dict[str, Any]] = []

    def get_open_orders(
        self, plan_id: Optional[str] = None, strategy_id: Optional[str] = None
    ) -> list[LocalOrder]:
        return list(self._orders)

    def update_order_status(
        self,
        *,
        local_id: str,
        status: str,
        kraken_order_id: Optional[str] = None,
        last_error: Optional[str] = None,
        raw_response: Any = None,
        event_message: Optional[str] = None,
        **_: Any,
    ) -> None:
        self.updates.append(
            {
                "local_id": local_id,
                "status": status,
                "kraken_order_id": kraken_order_id,
                "last_error": last_error,
                "raw_response": raw_response,
            }
        )


def _order(**overrides: Any) -> LocalOrder:
    base: dict[str, Any] = dict(
        local_id="L1",
        plan_id="p",
        strategy_id="s",
        pair="XBTUSD",
        side="buy",
        order_type="limit",
        status="submit_unknown",
        raw_request={"cl_ord_id": "L1"},
    )
    base.update(overrides)
    return LocalOrder(**base)


def _service(orders: list[LocalOrder], client: Any = None) -> Any:
    store = _FakeStore(orders)
    return SimpleNamespace(
        adapter=SimpleNamespace(client=client),
        store=store,
        open_orders={o.local_id: o for o in orders},
    )


def _patch(monkeypatch: pytest.MonkeyPatch, service: Any) -> None:
    monkeypatch.setattr(
        admin_cli, "_build_service", lambda db_path, allow_interactive_setup: service
    )


def test_force_clear_requires_reason(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    service = _service([_order()])
    _patch(monkeypatch, service)

    exit_code = admin_cli.main(
        ["force-clear-submit-unknown", "--local-id", "L1", "--reason", "   "]
    )

    assert exit_code == 1
    assert "without --reason" in capsys.readouterr().out
    assert service.store.updates == []


def test_force_clear_records_audit_and_marks_absent(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    service = _service([_order()])
    _patch(monkeypatch, service)

    exit_code = admin_cli.main(
        [
            "force-clear-submit-unknown",
            "--local-id",
            "L1",
            "--reason",
            "manual after kraken inspection",
        ]
    )

    assert exit_code == 0
    update = service.store.updates[-1]
    assert update["status"] == "submit_absent"
    assert "manual after kraken inspection" in update["last_error"]
    audit = update["raw_response"]["force_resolve"]
    assert audit["command"] == "force-clear-submit-unknown"
    assert audit["reason"] == "manual after kraken inspection"
    assert "L1" not in service.open_orders


def test_force_link_requires_reason(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    service = _service([_order()])
    _patch(monkeypatch, service)

    exit_code = admin_cli.main(
        [
            "force-link-submit-unknown",
            "--local-id",
            "L1",
            "--kraken-id",
            "OTXID",
            "--reason",
            "",
        ]
    )

    assert exit_code == 1
    assert "without --reason" in capsys.readouterr().out
    assert service.store.updates == []


def test_force_link_refuses_missing_txid(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    # No client -> txid cannot be verified -> fail closed by default.
    service = _service([_order()], client=None)
    _patch(monkeypatch, service)

    exit_code = admin_cli.main(
        [
            "force-link-submit-unknown",
            "--local-id",
            "L1",
            "--kraken-id",
            "OTXID",
            "--reason",
            "guessing",
        ]
    )

    assert exit_code == 1
    assert "Refusing to force-link" in capsys.readouterr().out
    assert service.store.updates == []


def test_force_link_allow_unverified_txid_records_audit(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    service = _service([_order()], client=None)
    _patch(monkeypatch, service)

    exit_code = admin_cli.main(
        [
            "force-link-submit-unknown",
            "--local-id",
            "L1",
            "--kraken-id",
            "OTXID",
            "--reason",
            "operator override",
            "--allow-unverified-txid",
        ]
    )

    assert exit_code == 0
    update = service.store.updates[-1]
    assert update["kraken_order_id"] == "OTXID"
    audit = update["raw_response"]["force_resolve"]
    assert audit["command"] == "force-link-submit-unknown"
    assert audit["reason"] == "operator override"


def _real_service(tmp_path: Any, client: Any) -> ExecutionService:
    md = MagicMock()

    def _meta(pair: str) -> PairMetadata:
        return PairMetadata(
            canonical=pair,
            base=pair[:3],
            quote=pair[3:],
            rest_symbol=f"{pair[:3]}/{pair[3:]}",
            ws_symbol=f"{pair[:3]}/{pair[3:]}",
            raw_name=pair,
            price_decimals=1,
            volume_decimals=8,
            lot_size=0.00000001,
            min_order_size=0.0001,
            status="online",
        )

    md.get_pair_metadata_or_raise.side_effect = _meta
    config = ExecutionConfig(mode="paper", validate_only=False)
    adapter = KrakenExecutionAdapter(client=client, config=config)
    store = SQLitePortfolioStore(str(tmp_path / "portfolio.db"))
    service = ExecutionService(
        adapter=adapter, store=store, config=config, market_data=md
    )
    return service


def test_force_link_found_closed_payload_syncs_fills(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    closed_payload = {
        "status": "closed",
        "vol_exec": "0.5",
        "price": "21.0",
        "cl_ord_id": "L1",
        "descr": {"pair": "XBTUSD"},
    }
    client = SimpleNamespace(
        get_open_orders=lambda params=None: {"open": {}},
        get_closed_orders=lambda params=None: {"closed": {"OTXID": closed_payload}},
    )
    service = _real_service(tmp_path, client)
    store = service.store
    assert store is not None
    store.save_order(_order(status="submit_unknown"))
    service.load_open_orders_from_store()
    _patch(monkeypatch, service)

    exit_code = admin_cli.main(
        [
            "force-link-submit-unknown",
            "--local-id",
            "L1",
            "--kraken-id",
            "OTXID",
            "--reason",
            "verified closed fill",
        ]
    )

    assert exit_code == 0
    linked = store.get_order_by_reference(kraken_order_id="OTXID")
    assert linked is not None
    assert linked.cumulative_base_filled == 0.5
    assert linked.status == "closed"
    # Structured audit is attached without clobbering the synced remote payload.
    assert linked.raw_response is not None
    assert (
        linked.raw_response.get("force_resolve", {}).get("command")
        == "force-link-submit-unknown"
    )
    assert linked.raw_response.get("vol_exec") == "0.5"


def test_clear_submit_unknown_refuses_unverified_candidate(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    # Exchange returns a single OPEN order that does NOT echo cl_ord_id.
    client = SimpleNamespace(
        get_open_orders=lambda params=None: {"open": {"OABC": {"status": "open"}}},
        get_closed_orders=lambda params=None: {"closed": {}},
    )
    service = _service([_order()], client=client)
    _patch(monkeypatch, service)

    exit_code = admin_cli.main(
        ["clear-submit-unknown", "--local-id", "L1", "--confirmed-absent"]
    )

    assert exit_code == 1
    assert "unverifiable" in capsys.readouterr().out.lower()
    assert service.store.updates == []
