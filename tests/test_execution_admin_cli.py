from typing import Any

import pytest

from kraken_bot.execution import admin_cli


def test_panic_cli_refreshes_and_cancels(monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    calls: list[str] = []

    class _DummyService:
        def refresh_open_orders(self) -> None:
            calls.append("refresh")

        def reconcile_orders(self) -> None:
            calls.append("reconcile")

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
    assert calls == ["refresh", "reconcile", "cancel_all"]
