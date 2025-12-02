from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Iterator

import pytest

from kraken_bot import cli
from kraken_bot.portfolio.store import CURRENT_SCHEMA_VERSION


def _create_sample_db(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('schema_version', ?)",
            (str(CURRENT_SCHEMA_VERSION),),
        )
        conn.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY, value REAL)")
        conn.executemany(
            "INSERT INTO trades (value) VALUES (?)",
            [(1.0,), (2.0,)],
        )
        conn.execute("CREATE TABLE cash_flows (id INTEGER PRIMARY KEY, amount REAL)")
        conn.execute("INSERT INTO cash_flows (amount) VALUES (10.5)")
        conn.commit()


def _fixed_datetimes(values: list[datetime]) -> Iterator[datetime]:
    for value in values:
        yield value


def test_db_info_reports_schema_and_counts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_path = tmp_path / "portfolio.db"
    _create_sample_db(db_path)

    exit_code = cli.main(["db-info", "--db-path", str(db_path)])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert f"DB path: {db_path.resolve()}" in output
    assert f"Schema version: {CURRENT_SCHEMA_VERSION}" in output
    assert "trades: 2 rows" in output
    assert "cash_flows: 1 rows" in output


def test_db_info_handles_missing_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_path = tmp_path / "missing.db"

    exit_code = cli.main(["db-info", "--db-path", str(db_path)])

    output = capsys.readouterr().out
    assert exit_code != 0
    assert f"DB file not found: {db_path.resolve()}" in output


def test_db_check_reports_integrity_status(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_path = tmp_path / "portfolio.db"
    _create_sample_db(db_path)

    exit_code = cli.main(["db-check", "--db-path", str(db_path)])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "PRAGMA integrity_check: ok" in output


def test_db_check_handles_invalid_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_path = tmp_path / "not_a_db.db"
    db_path.write_bytes(b"this is not sqlite")

    exit_code = cli.main(["db-check", "--db-path", str(db_path)])

    output = capsys.readouterr().out
    assert exit_code != 0
    assert "Failed to run integrity check" in output


def test_db_backup_creates_file_and_prunes_old_backups(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "portfolio.db"
    _create_sample_db(db_path)

    timestamps = _fixed_datetimes(
        [
            datetime(2024, 1, 1, 12, 30),
            datetime(2024, 1, 1, 12, 31),
            datetime(2024, 1, 1, 12, 32),
            datetime(2024, 1, 1, 12, 33),
        ]
    )

    class _PatchedDateTime(datetime):
        @classmethod
        def now(cls) -> datetime:  # type: ignore[override]
            return next(timestamps)

    monkeypatch.setattr(cli, "datetime", _PatchedDateTime)

    first_exit = cli.main(["db-backup", "--db-path", str(db_path)])
    first_backup = db_path.with_name("portfolio.db.202401011230.bak")

    second_exit = cli.main(["db-backup", "--db-path", str(db_path)])
    third_exit = cli.main(["db-backup", "--db-path", str(db_path)])

    initial_backups = sorted(db_path.parent.glob("portfolio.db.*.bak"))
    assert len(initial_backups) == 3
    assert first_backup in initial_backups

    assert first_exit == 0
    assert second_exit == 0
    assert third_exit == 0
    assert first_backup.exists()
    keep_exit = cli.main(["db-backup", "--db-path", str(db_path), "--keep", "2"])
    backups = sorted(db_path.parent.glob("portfolio.db.*.bak"))

    assert keep_exit == 0
    assert len(backups) == 2
    assert first_backup not in backups
    assert {backup.name for backup in backups} == {
        "portfolio.db.202401011232.bak",
        "portfolio.db.202401011233.bak",
    }


def test_db_backup_reports_missing_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_path = tmp_path / "missing.db"

    exit_code = cli.main(["db-backup", "--db-path", str(db_path)])

    output = capsys.readouterr().out
    assert exit_code != 0
    assert f"DB file not found: {db_path.resolve()}" in output
