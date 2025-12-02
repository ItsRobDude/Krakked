import sqlite3

import pytest

from kraken_bot.cli import print_schema_version, run_migrate_db
from kraken_bot.portfolio.exceptions import PortfolioSchemaError
from kraken_bot.portfolio.store import CURRENT_SCHEMA_VERSION


def seed_schema_version(db_path: str, version: int) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('schema_version', ?)",
            (str(version),),
        )
        conn.commit()


def test_run_migrate_db_initializes_blank_db(tmp_path):
    db_path = tmp_path / "new.db"

    status = run_migrate_db(str(db_path))

    assert status.initialized is True
    assert status.migrated is False
    assert status.version == CURRENT_SCHEMA_VERSION

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()

    assert row is not None
    assert int(row[0]) == CURRENT_SCHEMA_VERSION


def test_run_migrate_db_upgrades_outdated_schema(tmp_path):
    db_path = tmp_path / "upgrade.db"
    prior_version = CURRENT_SCHEMA_VERSION - 1
    seed_schema_version(str(db_path), prior_version)

    status = run_migrate_db(str(db_path))

    assert status.migrated is True
    assert status.initialized is False
    assert status.version == CURRENT_SCHEMA_VERSION

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()

    assert row is not None
    assert int(row[0]) == CURRENT_SCHEMA_VERSION


def test_print_schema_version_reports_existing_version(tmp_path):
    db_path = tmp_path / "inspect.db"
    prior_version = CURRENT_SCHEMA_VERSION - 2
    seed_schema_version(str(db_path), prior_version)

    status = print_schema_version(str(db_path))

    assert status.version == prior_version
    assert status.migrated is False
    assert status.initialized is False

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()

    assert row is not None
    assert int(row[0]) == prior_version


def test_print_schema_version_initializes_missing_meta(tmp_path):
    db_path = tmp_path / "fresh_inspect.db"

    status = print_schema_version(str(db_path))

    assert status.initialized is True
    assert status.version == CURRENT_SCHEMA_VERSION

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()

    assert row is not None
    assert int(row[0]) == CURRENT_SCHEMA_VERSION


def test_print_schema_version_raises_on_newer_schema(tmp_path):
    db_path = tmp_path / "ahead.db"
    seed_schema_version(str(db_path), CURRENT_SCHEMA_VERSION + 1)

    with pytest.raises(PortfolioSchemaError):
        print_schema_version(str(db_path))
