from __future__ import annotations

from typing import Any
from types import SimpleNamespace
import sqlite3

import pytest

from kraken_bot import cli
from kraken_bot.secrets import CredentialResult, CredentialStatus
from kraken_bot.portfolio.store import CURRENT_SCHEMA_VERSION
from kraken_bot.portfolio.exceptions import PortfolioSchemaError


class _DummyClient:
    def __init__(self, **_: Any) -> None:
        self.called = False

    def get_private(self, endpoint: str) -> None:  # noqa: ARG002
        self.called = True


def _seed_schema_version(db_path: str, version: int) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('schema_version', ?)",
            (str(version),),
        )
        conn.commit()


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


def test_run_once_forces_paper_and_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    original_config = SimpleNamespace(
        execution=SimpleNamespace(mode="live", validate_only=False, allow_live_trading=True),
        market_data=SimpleNamespace(backfill_timeframes=["1h"]),
    )
    captured_execution_config: dict[str, Any] = {}

    def fake_bootstrap(*_: Any, **__: Any) -> tuple[object, SimpleNamespace, object]:
        return object(), original_config, object()

    class _DummyMarketData:
        def __init__(self, config: Any) -> None:
            self.config = config

        def refresh_universe(self) -> None:
            self._universe = ["BTC/USD"]

        def get_universe(self) -> list[str]:
            return ["BTC/USD"]

        def backfill_ohlc(self, pair: str, timeframe: str) -> None:  # noqa: ARG002
            return None

    class _DummyPortfolio:
        def __init__(self, config: Any, market_data: Any) -> None:
            self.config = config
            self.market_data = market_data
            self.rest_client = None

        def initialize(self) -> None:
            return None

    class _DummyPlan:
        plan_id = "plan-1"

    class _DummyStrategyEngine:
        def __init__(self, config: Any, market_data: Any, portfolio: Any) -> None:
            self.config = config
            self.market_data = market_data
            self.portfolio = portfolio

        def initialize(self) -> None:
            return None

        def run_cycle(self) -> _DummyPlan:
            return _DummyPlan()

    class _DummyResult:
        success = True
        errors: list[str] = []

    class _DummyExecutionService:
        def __init__(self, client: Any, config: Any) -> None:  # noqa: ARG002
            captured_execution_config["config"] = config

        def execute_plan(self, plan: Any) -> _DummyResult:  # noqa: ARG002
            return _DummyResult()

    monkeypatch.setattr(cli.run_strategy_once, "bootstrap", fake_bootstrap)
    monkeypatch.setattr(cli.run_strategy_once, "MarketDataAPI", _DummyMarketData)
    monkeypatch.setattr(cli.run_strategy_once, "PortfolioService", _DummyPortfolio)
    monkeypatch.setattr(cli.run_strategy_once, "StrategyEngine", _DummyStrategyEngine)
    monkeypatch.setattr(cli.run_strategy_once, "ExecutionService", _DummyExecutionService)

    exit_code = cli.main(["run-once"])

    assert exit_code == 0
    safe_execution_config = captured_execution_config["config"]
    assert safe_execution_config.mode == "paper"
    assert safe_execution_config.validate_only is True
    assert safe_execution_config.allow_live_trading is False
    assert original_config.execution.mode == "live"
    assert original_config.execution.validate_only is False
    assert original_config.execution.allow_live_trading is True


def test_migrate_db_subcommand_upgrades_outdated_schema(tmp_path, capsys: Any) -> None:
    db_path = tmp_path / "upgrade_cli.db"
    _seed_schema_version(str(db_path), CURRENT_SCHEMA_VERSION - 1)

    exit_code = cli.main(["migrate-db", "--db-path", str(db_path)])

    assert exit_code == 0
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()

    assert row is not None
    assert int(row[0]) == CURRENT_SCHEMA_VERSION
    output = capsys.readouterr().out
    assert "Migration completed successfully" in output


def test_db_schema_version_reports_missing_meta(tmp_path, capsys: Any) -> None:
    db_path = tmp_path / "missing_meta.db"

    exit_code = cli.main(["db-schema-version", "--db-path", str(db_path)])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Schema version not set" in output


def test_db_schema_version_reports_missing_row(tmp_path, capsys: Any) -> None:
    db_path = tmp_path / "missing_row.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.commit()

    exit_code = cli.main(["db-schema-version", "--db-path", str(db_path)])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Schema version not set" in output


def test_migrate_db_subcommand_errors_on_newer_schema(tmp_path, capsys: Any) -> None:
    db_path = tmp_path / "ahead_cli.db"
    _seed_schema_version(str(db_path), CURRENT_SCHEMA_VERSION + 1)

    exit_code = cli.main(["migrate-db", "--db-path", str(db_path)])

    assert exit_code == 1
    output = capsys.readouterr().out
    assert "incompatible" in output


def test_migrate_db_subcommand_handles_migration_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys: Any
) -> None:
    db_path = tmp_path / "migration_failure.db"
    _seed_schema_version(str(db_path), CURRENT_SCHEMA_VERSION - 1)

    def _failing_migrate(db_path: str) -> None:  # noqa: ARG001
        raise PortfolioSchemaError(found="bad", expected=CURRENT_SCHEMA_VERSION)

    monkeypatch.setattr(cli, "run_migrate_db", _failing_migrate)

    exit_code = cli.main(["migrate-db", "--db-path", str(db_path)])

    assert exit_code == 1
    output = capsys.readouterr().out
    assert "Migration failed" in output
