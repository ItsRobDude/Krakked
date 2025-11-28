from __future__ import annotations

from typing import Any

import pytest

from kraken_bot import cli
from kraken_bot.secrets import CredentialResult, CredentialStatus


class _DummyClient:
    def __init__(self, **_: Any) -> None:
        self.called = False

    def get_private(self, endpoint: str) -> None:  # noqa: ARG002
        self.called = True


def test_setup_runs_interactive(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    def _fake_setup() -> CredentialResult:
        nonlocal called
        called = True
        return CredentialResult("key", "secret", CredentialStatus.LOADED)

    monkeypatch.setattr(cli.secrets, "_interactive_setup", _fake_setup)

    exit_code = cli.main(["setup"])

    assert called is True
    assert exit_code == 0


def test_smoke_test_uses_credentials_and_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cli.secrets,
        "load_api_keys",
        lambda allow_interactive_setup=False: CredentialResult(  # noqa: ARG005
            "key",
            "secret",
            CredentialStatus.LOADED,
        ),
    )

    dummy_client = _DummyClient()
    monkeypatch.setattr(cli, "KrakenRESTClient", lambda **kwargs: dummy_client)

    exit_code = cli.main(["smoke-test"])

    assert exit_code == 0
    assert dummy_client.called is True


def test_smoke_test_handles_missing_credentials(monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    monkeypatch.setattr(
        cli.secrets,
        "load_api_keys",
        lambda allow_interactive_setup=False: CredentialResult(  # noqa: ARG005
            None,
            None,
            CredentialStatus.NOT_FOUND,
        ),
    )

    exit_code = cli.main(["smoke-test"])

    captured = capsys.readouterr()
    assert "Credentials not available" in captured.out
    assert exit_code == 1
