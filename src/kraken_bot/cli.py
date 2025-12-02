"""Command line interface for kraken_bot utilities."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import shutil
import sqlite3
import sys
from typing import Callable

from kraken_bot import secrets
from kraken_bot.connection.exceptions import (
    AuthError,
    KrakenAPIError,
    RateLimitError,
    ServiceUnavailableError,
)
from kraken_bot.connection.rest_client import KrakenRESTClient
from kraken_bot.main import run as run_orchestrator
from kraken_bot.portfolio.exceptions import PortfolioSchemaError
from kraken_bot.portfolio.store import (
    CURRENT_SCHEMA_VERSION,
    SchemaStatus,
    ensure_portfolio_schema,
    ensure_portfolio_tables,
)
from kraken_bot.secrets import CredentialResult, CredentialStatus
from kraken_bot.scripts import run_strategy_once

DEFAULT_DB_PATH = "portfolio.db"


def _add_db_path_argument(subparser: argparse.ArgumentParser) -> None:
    """Attach the standard --db-path argument to a subparser."""

    subparser.add_argument(
        "--db-path",
        default=DEFAULT_DB_PATH,
        help=f"Path to the SQLite portfolio store (defaults to {DEFAULT_DB_PATH})",
    )


def _db_path_exists(db_path: str) -> bool:
    """Return whether the given DB path exists on disk."""

    return Path(db_path).expanduser().resolve().exists()


def _print_error(message: str) -> int:
    """Print an error message and return a non-zero exit code."""

    print(message)
    return 1


def _setup_command(_: argparse.Namespace) -> int:
    """Run the interactive setup flow for API secrets."""

    result: CredentialResult = secrets._interactive_setup()  # noqa: SLF001
    return 0 if result.status == CredentialStatus.LOADED else 1


def _smoke_test_command(args: argparse.Namespace) -> int:
    """Perform a simple authenticated request against Kraken's API."""

    credential_result = secrets.load_api_keys(
        allow_interactive_setup=args.allow_interactive_setup
    )

    if credential_result.status == CredentialStatus.MISSING_PASSWORD:
        print(
            credential_result.validation_error
            or "Encrypted credentials are locked; set KRAKEN_BOT_SECRET_PW to the master password."
        )
        return 1

    if credential_result.status != CredentialStatus.LOADED:
        print("Credentials not available; run `krakked setup` first.")
        return 1

    client = KrakenRESTClient(
        api_key=credential_result.api_key,
        api_secret=credential_result.api_secret,
    )

    try:
        client.get_private("Balance")
        print("Smoke test succeeded: authenticated request completed.")
        return 0
    except (AuthError, RateLimitError, ServiceUnavailableError, KrakenAPIError) as exc:
        print(f"Smoke test failed: {exc}")
        return 1


def _run_once_command(_: argparse.Namespace) -> int:
    """Run a single strategy + execution cycle in safe mode."""

    run_strategy_once.run_strategy_once()
    return 0


def _run_command(args: argparse.Namespace) -> int:
    """Start the long-running orchestrator with UI and scheduler loops."""

    return run_orchestrator(allow_interactive_setup=args.allow_interactive_setup)


def _get_schema_version(db_path: str) -> int | None:
    """Fetch the stored schema version from the portfolio meta table, if present."""

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='meta'"
    )
    has_meta = cursor.fetchone() is not None

    if not has_meta:
        conn.close()
        return None

    cursor.execute("SELECT value FROM meta WHERE key = 'schema_version'")
    row = cursor.fetchone()
    conn.close()

    if row is None:
        return None

    try:
        return int(row[0])
    except (TypeError, ValueError):
        raise PortfolioSchemaError(found=row[0], expected=CURRENT_SCHEMA_VERSION)


def run_migrate_db(db_path: str) -> SchemaStatus:
    """Run migrations for the SQLite portfolio store at ``db_path``."""

    with sqlite3.connect(db_path) as conn:
        status = ensure_portfolio_schema(
            conn, CURRENT_SCHEMA_VERSION, migrate=True
        )
        ensure_portfolio_tables(conn)
        conn.commit()

    return status


def print_schema_version(db_path: str) -> SchemaStatus:
    """Ensure metadata exists and return the stored portfolio schema version."""

    with sqlite3.connect(db_path) as conn:
        status = ensure_portfolio_schema(
            conn, CURRENT_SCHEMA_VERSION, migrate=False
        )
        conn.commit()

    return status


def _migrate_db_command(args: argparse.Namespace) -> int:
    """Run portfolio schema migrations for the SQLite store at --db-path."""

    print(f"Starting migration for {args.db_path}")

    try:
        stored_version = _get_schema_version(args.db_path)
    except PortfolioSchemaError as exc:
        return _print_error(
            "Migration failed: "
            f"stored schema version value {exc.found} is incompatible with expected {exc.expected}."
        )
    except Exception as exc:  # noqa: BLE001
        return _print_error(f"Migration failed: {exc}")

    version_text = stored_version if stored_version is not None else "unknown"
    print(
        f"Stored schema version: {version_text}; target version: {CURRENT_SCHEMA_VERSION}"
    )

    try:
        status = run_migrate_db(args.db_path)
    except PortfolioSchemaError as exc:
        return _print_error(
            "Migration failed: "
            f"stored schema version {exc.found} is incompatible with expected {exc.expected}."
        )
    except Exception as exc:  # noqa: BLE001
        return _print_error(f"Migration failed: {exc}")

    print(f"Migration completed successfully to version {status.version}.")
    return 0


def _schema_version_command(args: argparse.Namespace) -> int:
    """Display the current portfolio schema version stored at --db-path."""

    resolved_path = Path(args.db_path).expanduser().resolve()

    try:
        status = print_schema_version(resolved_path.as_posix())
    except PortfolioSchemaError as exc:
        return _print_error(
            "Failed to read schema version: "
            f"stored value {exc.found} is incompatible with expected {exc.expected}."
        )
    except Exception as exc:  # noqa: BLE001
        return _print_error(f"Failed to read schema version: {exc}")

    if status.initialized:
        print("Schema version not set; meta table or schema_version row is missing.")
        return 0

    print(f"Schema version: {status.version}")
    return 0


def _db_backup_command(args: argparse.Namespace) -> int:
    """Create a timestamped backup of the portfolio database at --db-path."""

    db_path = Path(args.db_path).expanduser().resolve()

    if not _db_path_exists(db_path.as_posix()):
        return _print_error(f"DB file not found: {db_path}")

    timestamp = datetime.now().strftime("%Y%m%d%H%M")
    backup_path = db_path.with_name(f"{db_path.name}.{timestamp}.bak")

    try:
        shutil.copy2(db_path, backup_path)
    except Exception as exc:  # noqa: BLE001
        return _print_error(f"Failed to create backup: {exc}")

    print(f"Backup created at {backup_path}")

    if args.keep is None or args.keep <= 0:
        return 0

    prefix = f"{db_path.name}."
    try:
        backups = []
        for candidate in db_path.parent.glob(f"{db_path.name}.*.bak"):
            name = candidate.name
            if not name.startswith(prefix) or not name.endswith(".bak"):
                continue

            timestamp_part = name[len(prefix) : -4]
            if len(timestamp_part) != 12 or not timestamp_part.isdigit():
                continue

            backups.append((timestamp_part, candidate))

        backups.sort(key=lambda item: item[0], reverse=True)
        removals = backups[args.keep :]

        if not removals:
            print("No old backups removed.")
            return 0

        print("Removed old backups:")
        for _, backup in removals:
            try:
                backup.unlink()
                print(f"- {backup}")
            except Exception as exc:  # noqa: BLE001
                return _print_error(f"Failed to remove old backup {backup}: {exc}")

        return 0
    except Exception as exc:  # noqa: BLE001
        return _print_error(f"Failed to prune backups: {exc}")


def _db_info_command(args: argparse.Namespace) -> int:
    """Display information about the portfolio database at --db-path."""

    resolved_path = Path(args.db_path).expanduser().resolve()

    if not _db_path_exists(resolved_path.as_posix()):
        return _print_error(f"DB file not found: {resolved_path}")

    try:
        schema_version = _get_schema_version(resolved_path.as_posix())
    except PortfolioSchemaError as exc:
        return _print_error(
            "Failed to read schema version: "
            f"stored value {exc.found} is incompatible with expected {exc.expected}."
        )
    except sqlite3.OperationalError as exc:
        return _print_error(f"Failed to read schema version: {exc}")
    except Exception as exc:  # noqa: BLE001
        return _print_error(f"Failed to read schema version: {exc}")

    tables = [
        "meta",
        "trades",
        "cash_flows",
        "snapshots",
        "decisions",
        "execution_plans",
        "execution_orders",
        "execution_order_events",
        "execution_results",
    ]

    try:
        with sqlite3.connect(resolved_path.as_posix()) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            existing_tables = {row[0] for row in cursor.fetchall()}

            version_text = schema_version if schema_version is not None else "unknown"
            print(f"DB path: {resolved_path}")
            print(f"Schema version: {version_text}")

            for table in tables:
                if table not in existing_tables:
                    print(f"{table}: (missing)")
                    continue

                try:
                    cursor.execute(f"SELECT COUNT(*) FROM {table}")
                    count = cursor.fetchone()[0]
                    print(f"{table}: {count} rows")
                except sqlite3.OperationalError as exc:
                    return _print_error(f"{table}: error reading rows ({exc})")

        return 0
    except sqlite3.OperationalError as exc:
        return _print_error(f"Failed to read DB info: {exc}")
    except Exception as exc:  # noqa: BLE001
        return _print_error(f"Failed to read DB info: {exc}")


def _db_check_command(args: argparse.Namespace) -> int:
    """Run PRAGMA integrity_check against the portfolio database at --db-path."""

    resolved_path = Path(args.db_path).expanduser().resolve()

    if not _db_path_exists(resolved_path.as_posix()):
        return _print_error(f"DB file not found: {resolved_path}")

    try:
        with sqlite3.connect(resolved_path.as_posix()) as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA integrity_check")
            row = cursor.fetchone()
    except Exception as exc:  # noqa: BLE001
        return _print_error(f"Failed to run integrity check: {exc}")

    result = row[0] if row else None
    print(f"PRAGMA integrity_check: {result}")

    return 0 if result == "ok" else 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="krakked", description="Kraken bot utilities")
    subparsers = parser.add_subparsers(dest="command", required=True)

    setup_parser = subparsers.add_parser("setup", help="Run interactive API key setup")
    setup_parser.set_defaults(func=_setup_command)

    smoke_parser = subparsers.add_parser(
        "smoke-test", help="Validate credentials by calling a private Kraken endpoint"
    )
    smoke_parser.add_argument(
        "--allow-interactive-setup",
        action="store_true",
        help="Prompt for credentials if they are not already configured",
    )
    smoke_parser.set_defaults(func=_smoke_test_command)

    run_once_parser = subparsers.add_parser(
        "run-once",
        help="Run a single strategy cycle with paper trading and validation guardrails",
    )
    run_once_parser.set_defaults(func=_run_once_command)

    run_parser = subparsers.add_parser(
        "run",
        help="Start the orchestrator with market data, scheduler, execution, and UI",
    )
    run_parser.add_argument(
        "--allow-interactive-setup",
        action="store_true",
        help="Prompt for credentials if they are not already configured",
    )
    run_parser.set_defaults(func=_run_command)

    migrate_parser = subparsers.add_parser(
        "migrate-db",
        help="Run portfolio DB migrations against the SQLite store",
    )
    _add_db_path_argument(migrate_parser)
    migrate_parser.set_defaults(func=_migrate_db_command)

    version_parser = subparsers.add_parser(
        "db-schema-version",
        help="Show the stored schema version for the SQLite portfolio DB",
    )
    _add_db_path_argument(version_parser)
    version_parser.set_defaults(func=_schema_version_command)

    backup_parser = subparsers.add_parser(
        "db-backup", help="Create a timestamped backup of the SQLite portfolio DB"
    )
    _add_db_path_argument(backup_parser)
    backup_parser.add_argument(
        "--keep",
        type=int,
        help="Retain only the N most recent backups (older backups will be deleted)",
    )
    backup_parser.set_defaults(func=_db_backup_command)

    db_info_parser = subparsers.add_parser(
        "db-info",
        help="Show schema version and row counts for the SQLite portfolio DB",
    )
    _add_db_path_argument(db_info_parser)
    db_info_parser.set_defaults(func=_db_info_command)

    db_check_parser = subparsers.add_parser(
        "db-check",
        help="Run PRAGMA integrity_check against the SQLite portfolio DB",
    )
    _add_db_path_argument(db_check_parser)
    db_check_parser.set_defaults(func=_db_check_command)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point for the `krakked` console script."""

    parser = _build_parser()
    args = parser.parse_args(argv)
    command: Callable[[argparse.Namespace], int] = getattr(args, "func")
    return command(args)


if __name__ == "__main__":
    sys.exit(main())
