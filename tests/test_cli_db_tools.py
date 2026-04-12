from __future__ import annotations

import json
import sqlite3
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Iterator

import pytest

from krakked import cli
from krakked.portfolio.store import CURRENT_SCHEMA_VERSION


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


def test_db_backup_captures_wal_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "portfolio.db"
    _create_sample_db(db_path)

    # Put the DB in WAL mode and disable auto-checkpointing so recent commits live in -wal.
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA wal_autocheckpoint=0")
    conn.execute("INSERT INTO trades (value) VALUES (3.0)")
    conn.commit()

    wal_path = Path(str(db_path) + "-wal")
    assert wal_path.exists()

    timestamps = _fixed_datetimes([datetime(2024, 1, 1, 12, 34)])

    class _PatchedDateTime(datetime):
        @classmethod
        def now(cls) -> datetime:  # type: ignore[override]
            return next(timestamps)

    monkeypatch.setattr(cli, "datetime", _PatchedDateTime)

    exit_code = cli.main(["db-backup", "--db-path", str(db_path)])
    backup_path = db_path.with_name("portfolio.db.202401011234.bak")

    assert exit_code == 0
    assert backup_path.exists()

    with sqlite3.connect(backup_path) as backup_conn:
        row_count = backup_conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        assert row_count == 3

    conn.close()


def test_db_backup_reports_missing_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_path = tmp_path / "missing.db"

    exit_code = cli.main(["db-backup", "--db-path", str(db_path)])

    output = capsys.readouterr().out
    assert exit_code != 0
    assert f"DB file not found: {db_path.resolve()}" in output


def test_export_install_creates_archive_with_manifest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    db_path = tmp_path / "portfolio.db"
    archive_path = tmp_path / "krakked-export.zip"

    config_dir.mkdir()
    data_dir.mkdir()
    (config_dir / "config.yaml").write_text("execution:\n  mode: paper\n")
    (config_dir / "accounts.yaml").write_text("version: 1\naccounts: []\n")
    (data_dir / "metadata.json").write_text('{"hello":"world"}')
    _create_sample_db(db_path)

    exit_code = cli.main(
        [
            "export-install",
            "--output",
            str(archive_path),
            "--config-dir",
            str(config_dir),
            "--db-path",
            str(db_path),
            "--data-dir",
            str(data_dir),
            "--include-data",
        ]
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert f"Export created at {archive_path.resolve()}" in output
    assert archive_path.exists()

    with zipfile.ZipFile(archive_path) as archive:
        names = set(archive.namelist())
        assert "manifest.json" in names
        assert "config/config.yaml" in names
        assert "config/accounts.yaml" in names
        assert "state/portfolio.db" in names
        assert "data/metadata.json" in names

        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["includes"]["config"] is True
        assert manifest["includes"]["database"] is True
        assert manifest["includes"]["data"] is True
        assert manifest["db_schema_version"] == CURRENT_SCHEMA_VERSION


def test_import_install_restores_archive_contents(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source_config = tmp_path / "source-config"
    source_data = tmp_path / "source-data"
    source_db = tmp_path / "source.db"
    archive_path = tmp_path / "krakked-export.zip"

    source_config.mkdir()
    source_data.mkdir()
    (source_config / "config.yaml").write_text("ui:\n  enabled: true\n")
    (source_data / "metadata.json").write_text('{"fresh":true}')
    _create_sample_db(source_db)

    export_exit = cli.main(
        [
            "export-install",
            "--output",
            str(archive_path),
            "--config-dir",
            str(source_config),
            "--db-path",
            str(source_db),
            "--data-dir",
            str(source_data),
            "--include-data",
        ]
    )
    assert export_exit == 0

    target_config = tmp_path / "target-config"
    target_data = tmp_path / "target-data"
    target_db = tmp_path / "target.db"

    import_exit = cli.main(
        [
            "import-install",
            "--input",
            str(archive_path),
            "--config-dir",
            str(target_config),
            "--db-path",
            str(target_db),
            "--data-dir",
            str(target_data),
        ]
    )

    output = capsys.readouterr().out
    assert import_exit == 0
    assert f"Imported archive from {archive_path.resolve()}" in output
    assert (target_config / "config.yaml").read_text() == "ui:\n  enabled: true\n"
    assert (target_data / "metadata.json").read_text() == '{"fresh":true}'

    with sqlite3.connect(target_db) as conn:
        count = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    assert count == 2


def test_import_install_requires_force_before_overwrite(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source_config = tmp_path / "source-config"
    source_db = tmp_path / "source.db"
    archive_path = tmp_path / "krakked-export.zip"

    source_config.mkdir()
    (source_config / "config.yaml").write_text("ui:\n  enabled: true\n")
    _create_sample_db(source_db)

    export_exit = cli.main(
        [
            "export-install",
            "--output",
            str(archive_path),
            "--config-dir",
            str(source_config),
            "--db-path",
            str(source_db),
        ]
    )
    assert export_exit == 0

    target_config = tmp_path / "target-config"
    target_config.mkdir()
    (target_config / "config.yaml").write_text("ui:\n  enabled: false\n")

    target_db = tmp_path / "target.db"
    _create_sample_db(target_db)

    import_exit = cli.main(
        [
            "import-install",
            "--input",
            str(archive_path),
            "--config-dir",
            str(target_config),
            "--db-path",
            str(target_db),
        ]
    )

    output = capsys.readouterr().out
    assert import_exit == 1
    assert "Re-run with --force" in output
    assert (target_config / "config.yaml").read_text() == "ui:\n  enabled: false\n"


def test_import_install_force_overwrite_creates_backups(
    tmp_path: Path,
) -> None:
    source_config = tmp_path / "source-config"
    source_db = tmp_path / "source.db"
    archive_path = tmp_path / "krakked-export.zip"

    source_config.mkdir()
    (source_config / "config.yaml").write_text("ui:\n  enabled: true\n")
    _create_sample_db(source_db)

    export_exit = cli.main(
        [
            "export-install",
            "--output",
            str(archive_path),
            "--config-dir",
            str(source_config),
            "--db-path",
            str(source_db),
        ]
    )
    assert export_exit == 0

    target_config = tmp_path / "target-config"
    target_config.mkdir()
    target_config_file = target_config / "config.yaml"
    target_config_file.write_text("ui:\n  enabled: false\n")

    target_db = tmp_path / "target.db"
    _create_sample_db(target_db)

    import_exit = cli.main(
        [
            "import-install",
            "--input",
            str(archive_path),
            "--config-dir",
            str(target_config),
            "--db-path",
            str(target_db),
            "--force",
        ]
    )

    assert import_exit == 0
    assert target_config_file.read_text() == "ui:\n  enabled: true\n"
    assert list(target_config.glob("config.yaml.*.bak"))
    assert list(target_db.parent.glob("target.db.*.bak"))
