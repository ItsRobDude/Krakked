import sqlite3

import pytest

from kraken_bot.portfolio.exceptions import PortfolioSchemaError
from kraken_bot.portfolio.store import CURRENT_SCHEMA_VERSION, assert_portfolio_schema


def _seed_schema_version(db_path: str, version: int) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)"
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
            (version,),
        )
        conn.commit()


def test_assert_portfolio_schema_raises_for_future_version(tmp_path) -> None:
    db_path = tmp_path / "future.db"
    _seed_schema_version(db_path.as_posix(), CURRENT_SCHEMA_VERSION + 1)

    with pytest.raises(PortfolioSchemaError):
        assert_portfolio_schema(db_path.as_posix())


def test_assert_portfolio_schema_raises_for_old_version(tmp_path) -> None:
    db_path = tmp_path / "old.db"
    _seed_schema_version(db_path.as_posix(), CURRENT_SCHEMA_VERSION - 1)

    with pytest.raises(PortfolioSchemaError):
        assert_portfolio_schema(db_path.as_posix())


def test_assert_portfolio_schema_initializes_missing_schema(tmp_path) -> None:
    db_path = tmp_path / "fresh.db"

    status = assert_portfolio_schema(db_path.as_posix())

    assert status.version == CURRENT_SCHEMA_VERSION

    with sqlite3.connect(db_path.as_posix()) as conn:
        row = conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()

    assert row is not None
    assert int(row[0]) == CURRENT_SCHEMA_VERSION
