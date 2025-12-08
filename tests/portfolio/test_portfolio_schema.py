import sqlite3
from types import SimpleNamespace
from unittest.mock import patch

from kraken_bot import cli, main
from kraken_bot.portfolio.store import CURRENT_SCHEMA_VERSION


def test_live_mode_disallows_auto_migrate_schema(monkeypatch):
    config = SimpleNamespace(
        portfolio=SimpleNamespace(auto_migrate_schema=True, db_path=":memory:"),
        execution=SimpleNamespace(mode="live"),
    )

    def fake_bootstrap(**_):
        return "client", config, None

    monkeypatch.setattr(main, "bootstrap", fake_bootstrap)

    exit_code = main.run()

    assert exit_code == 1


def test_portfolio_migrate_cli_calls_ensure_schema(tmp_path):
    db = tmp_path / "portfolio.db"

    with (
        patch("kraken_bot.cli.ensure_portfolio_schema") as ensure_mock,
        patch("kraken_bot.cli.ensure_portfolio_tables") as ensure_tables_mock,
    ):
        ensure_mock.return_value = SimpleNamespace(
            version=CURRENT_SCHEMA_VERSION, migrated=True, initialized=False
        )

        result = cli.main(["portfolio-migrate", "--db", str(db)])

    assert result == 0
    ensure_mock.assert_called_once_with(str(db), CURRENT_SCHEMA_VERSION, migrate=True)
    ensure_tables_mock.assert_called_once()
    args, _ = ensure_tables_mock.call_args
    assert isinstance(args[0], sqlite3.Connection)
