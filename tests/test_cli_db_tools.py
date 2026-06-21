from __future__ import annotations

import json
import sqlite3
import zipfile
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Iterator

import pytest

from krakked import cli
from krakked.portfolio.models import LedgerEntry
from krakked.portfolio.store import CURRENT_SCHEMA_VERSION, SQLitePortfolioStore


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


def _seed_unmatched_trade_ref_db(db_path: Path) -> None:
    store = SQLitePortfolioStore(str(db_path))
    try:
        for ledger_id, refid, timestamp, asset, amount in [
            ("L-missing-1", "T-MISSING", 10.0, "XXBT", "1.0"),
            ("L-missing-2", "T-MISSING", 11.0, "ZUSD", "-100.0"),
            ("L-matched", "T-MATCHED", 12.0, "XXBT", "1.0"),
        ]:
            store.save_ledger_entry(
                LedgerEntry(
                    id=ledger_id,
                    time=timestamp,
                    type="trade",
                    subtype="",
                    aclass="currency",
                    asset=asset,
                    amount=Decimal(amount),
                    fee=Decimal("0"),
                    balance=None,
                    refid=refid,
                    misc=None,
                    raw={"ledger_id": ledger_id},
                )
            )
        store.save_trades(
            [
                {
                    "id": "T-MATCHED",
                    "pair": "XBTUSD",
                    "time": 12.0,
                    "type": "buy",
                    "price": "100",
                    "cost": "100",
                    "fee": "0",
                    "vol": "1",
                }
            ]
        )
    finally:
        store.close()


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


def test_db_unmatched_trade_refs_reports_human_and_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_path = tmp_path / "portfolio.db"
    _seed_unmatched_trade_ref_db(db_path)

    exit_code = cli.main(["db-unmatched-trade-refs", "--db-path", str(db_path)])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "Unmatched trade ledger refs:" in output
    assert "T-MISSING" in output
    assert "L-missing-1" in output
    assert "T-MATCHED" not in output

    json_exit = cli.main(
        ["db-unmatched-trade-refs", "--db-path", str(db_path), "--json"]
    )

    payload = json.loads(capsys.readouterr().out)
    assert json_exit == 0
    assert [item["refid"] for item in payload["unmatched_trade_ledger_refs"]] == [
        "T-MISSING"
    ]
    assert payload["unmatched_trade_ledger_refs"][0]["ledger_count"] == 2
    assert payload["unmatched_trade_ledger_refs"][0]["reviewed_ledger_count"] == 0
    assert payload["unmatched_trade_ledger_refs"][0]["unreviewed_ledger_count"] == 2


def test_db_unmatched_trade_refs_reports_missing_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_path = tmp_path / "missing.db"

    exit_code = cli.main(["db-unmatched-trade-refs", "--db-path", str(db_path)])

    output = capsys.readouterr().out
    assert exit_code != 0
    assert f"DB file not found: {db_path.resolve()}" in output


def test_db_unmatched_trade_refs_is_read_only_and_requires_review_schema(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_path = tmp_path / "portfolio.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO meta (key, value) VALUES ('schema_version', '12')")
        conn.execute(
            """
            CREATE TABLE ledger_entries (
                id TEXT PRIMARY KEY,
                time REAL,
                type TEXT,
                subtype TEXT,
                aclass TEXT,
                asset TEXT,
                amount TEXT,
                fee TEXT,
                balance TEXT,
                refid TEXT,
                misc TEXT,
                raw_json TEXT
            )
            """
        )
        conn.execute("CREATE TABLE trades (id TEXT PRIMARY KEY)")

    exit_code = cli.main(["db-unmatched-trade-refs", "--db-path", str(db_path)])

    output = capsys.readouterr().out
    assert exit_code != 0
    assert "poetry run krakked migrate --db-path" in output

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()
        tables = {item[0] for item in conn.execute("SELECT name FROM sqlite_master")}

    assert row == ("12",)
    assert "reviewed_trade_ledger_ref_entries" not in tables


def test_db_mark_trade_ref_reviewed_requires_exact_confirmation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_path = tmp_path / "portfolio.db"
    _seed_unmatched_trade_ref_db(db_path)

    exit_code = cli.main(
        [
            "db-mark-trade-ref-reviewed",
            "T-MISSING",
            "--db-path",
            str(db_path),
            "--reviewed-by",
            "ops",
            "--reason",
            "Verified in Kraken",
            "--confirm",
            "wrong",
        ]
    )

    output = capsys.readouterr().out
    assert exit_code != 0
    assert 'Re-run with --confirm "MARK T-MISSING REVIEWED"' in output


def test_db_mark_trade_ref_reviewed_rejects_matched_ref(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_path = tmp_path / "portfolio.db"
    _seed_unmatched_trade_ref_db(db_path)

    exit_code = cli.main(
        [
            "db-mark-trade-ref-reviewed",
            "T-MATCHED",
            "--db-path",
            str(db_path),
            "--reviewed-by",
            "ops",
            "--reason",
            "Verified in Kraken",
            "--confirm",
            "MARK T-MATCHED REVIEWED",
        ]
    )

    output = capsys.readouterr().out
    assert exit_code != 0
    assert "Refusing to review a ref that is absent or already matched" in output


def test_db_mark_trade_ref_reviewed_creates_backup_and_unblocks_ref(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db_path = tmp_path / "portfolio.db"
    _seed_unmatched_trade_ref_db(db_path)
    monkeypatch.setattr(cli.time, "time", lambda: 1_700_000_000)

    exit_code = cli.main(
        [
            "db-mark-trade-ref-reviewed",
            "T-MISSING",
            "--db-path",
            str(db_path),
            "--reviewed-by",
            "ops",
            "--reason",
            "Verified in Kraken",
            "--confirm",
            "MARK T-MISSING REVIEWED",
        ]
    )

    output = capsys.readouterr().out
    backup_path = db_path.with_name("portfolio.db.1700000000.bak")
    assert exit_code == 0
    assert backup_path.exists()
    assert f"Backup created at {backup_path.resolve()}" in output
    assert "does not synthesize missing TradesHistory rows" in output

    store = SQLitePortfolioStore(str(db_path))
    try:
        assert store.get_unmatched_trade_ledger_ref_times() == {}
        store.save_ledger_entry(
            LedgerEntry(
                id="L-missing-new",
                time=13.0,
                type="trade",
                subtype="",
                aclass="currency",
                asset="XXBT",
                amount=Decimal("0.5"),
                fee=Decimal("0"),
                balance=None,
                refid="T-MISSING",
                misc=None,
                raw={"ledger_id": "L-missing-new"},
            )
        )
        assert store.get_unmatched_trade_ledger_ref_times() == {"T-MISSING": 13.0}
    finally:
        store.close()

    mixed_exit = cli.main(
        [
            "db-unmatched-trade-refs",
            "--db-path",
            str(db_path),
            "--include-reviewed",
            "--json",
        ]
    )
    mixed_payload = json.loads(capsys.readouterr().out)
    assert mixed_exit == 0
    assert mixed_payload["unmatched_trade_ledger_refs"][0]["reviewed"] is False
    assert mixed_payload["unmatched_trade_ledger_refs"][0]["reviewed_ledger_count"] == 2
    assert (
        mixed_payload["unmatched_trade_ledger_refs"][0]["unreviewed_ledger_count"] == 1
    )

    append_exit = cli.main(
        [
            "db-mark-trade-ref-reviewed",
            "T-MISSING",
            "--db-path",
            str(db_path),
            "--reviewed-by",
            "ops",
            "--reason",
            "Verified again",
            "--confirm",
            "MARK T-MISSING REVIEWED",
        ]
    )

    append_output = capsys.readouterr().out
    assert append_exit == 0
    assert "Reviewed unmatched trade ledger ref T-MISSING" in append_output

    include_reviewed_exit = cli.main(
        [
            "db-unmatched-trade-refs",
            "--db-path",
            str(db_path),
            "--include-reviewed",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert include_reviewed_exit == 0
    assert payload["unmatched_trade_ledger_refs"][0]["reviewed"] is True
    assert payload["unmatched_trade_ledger_refs"][0]["reviewed_ledger_count"] == 3
    assert payload["unmatched_trade_ledger_refs"][0]["unreviewed_ledger_count"] == 0
    reviewed_ledgers = payload["unmatched_trade_ledger_refs"][0]["ledger_entries"]
    assert {entry["id"] for entry in reviewed_ledgers if entry["reviewed"]} == {
        "L-missing-1",
        "L-missing-2",
        "L-missing-new",
    }
    assert {entry["id"] for entry in reviewed_ledgers if not entry["reviewed"]} == set()


def test_db_revoke_trade_ref_review_creates_backup_and_restores_blocker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db_path = tmp_path / "portfolio.db"
    _seed_unmatched_trade_ref_db(db_path)
    monkeypatch.setattr(cli.time, "time", lambda: 1_700_000_002)

    mark_exit = cli.main(
        [
            "db-mark-trade-ref-reviewed",
            "T-MISSING",
            "--db-path",
            str(db_path),
            "--reviewed-by",
            "ops",
            "--reason",
            "Verified in Kraken",
            "--confirm",
            "MARK T-MISSING REVIEWED",
        ]
    )
    capsys.readouterr()
    assert mark_exit == 0

    wrong_confirm_exit = cli.main(
        [
            "db-revoke-trade-ref-review",
            "T-MISSING",
            "--db-path",
            str(db_path),
            "--revoked-by",
            "ops",
            "--reason",
            "Mistaken review",
            "--confirm",
            "wrong",
        ]
    )
    assert wrong_confirm_exit != 0
    assert "REVOKE T-MISSING REVIEW" in capsys.readouterr().out

    revoke_exit = cli.main(
        [
            "db-revoke-trade-ref-review",
            "T-MISSING",
            "--db-path",
            str(db_path),
            "--revoked-by",
            "ops",
            "--reason",
            "Mistaken review",
            "--confirm",
            "REVOKE T-MISSING REVIEW",
        ]
    )

    output = capsys.readouterr().out
    backup_path = db_path.with_name("portfolio.db.1700000002.bak")
    assert revoke_exit == 0
    assert backup_path.exists()
    assert f"Backup created at {backup_path.resolve()}" in output
    assert "live blocker is restored" in output

    store = SQLitePortfolioStore(str(db_path))
    try:
        assert store.get_unmatched_trade_ledger_ref_times() == {"T-MISSING": 10.0}
    finally:
        store.close()

    with sqlite3.connect(db_path) as conn:
        events = conn.execute(
            """
            SELECT event_type, actor, reason
            FROM trade_ledger_ref_review_events
            ORDER BY id
            """
        ).fetchall()

    assert events == [
        ("review", "ops", "Verified in Kraken"),
        ("revoke", "ops", "Mistaken review"),
    ]

    duplicate_revoke_exit = cli.main(
        [
            "db-revoke-trade-ref-review",
            "T-MISSING",
            "--db-path",
            str(db_path),
            "--revoked-by",
            "ops",
            "--reason",
            "Again",
            "--confirm",
            "REVOKE T-MISSING REVIEW",
        ]
    )
    assert duplicate_revoke_exit != 0
    assert "not active" in capsys.readouterr().out


def test_db_mark_trade_ref_reviewed_creates_backup_before_audit_insert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db_path = tmp_path / "portfolio.db"
    _seed_unmatched_trade_ref_db(db_path)
    backup_path = db_path.with_name("portfolio.db.1700000001.bak")
    monkeypatch.setattr(cli.time, "time", lambda: 1_700_000_001)

    def fail_after_backup(
        self: SQLitePortfolioStore,  # noqa: ARG001
        **kwargs: object,  # noqa: ARG001
    ) -> object:
        assert backup_path.exists()
        raise RuntimeError("audit insert failed")

    monkeypatch.setattr(
        SQLitePortfolioStore, "mark_trade_ledger_ref_reviewed", fail_after_backup
    )

    exit_code = cli.main(
        [
            "db-mark-trade-ref-reviewed",
            "T-MISSING",
            "--db-path",
            str(db_path),
            "--reviewed-by",
            "ops",
            "--reason",
            "Verified in Kraken",
            "--confirm",
            "MARK T-MISSING REVIEWED",
        ]
    )

    output = capsys.readouterr().out
    assert exit_code != 0
    assert backup_path.exists()
    assert "audit insert failed" in output

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT refid FROM reviewed_trade_ledger_ref_entries"
        ).fetchall()

    assert rows == []


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
