import sqlite3

from kraken_bot.portfolio import migrations
from kraken_bot.portfolio.store import CURRENT_SCHEMA_VERSION, SQLitePortfolioStore


def test_sqlite_portfolio_store_not_abstract():
    assert not SQLitePortfolioStore.__abstractmethods__


def test_current_schema_version_is_latest():
    assert CURRENT_SCHEMA_VERSION == 7


def test_run_migrations_reaches_latest_and_creates_ml_tables(tmp_path):
    db_path = tmp_path / "migration_check.db"
    conn = sqlite3.connect(db_path)

    try:
        migrations._ensure_meta_table(conn)
        migrations._set_schema_version(conn, 5)

        migrations.run_migrations(conn, 5, CURRENT_SCHEMA_VERSION)

        row = conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()
        assert row is not None
        assert int(row[0]) == CURRENT_SCHEMA_VERSION

        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='ml_training_examples'"
        )
        assert cursor.fetchone() is not None

        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='ml_models'"
        )
        assert cursor.fetchone() is not None
    finally:
        conn.close()
