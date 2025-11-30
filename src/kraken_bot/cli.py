"""Command line interface for kraken_bot utilities."""

from __future__ import annotations

import argparse
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
from kraken_bot.portfolio.store import CURRENT_SCHEMA_VERSION, SQLitePortfolioStore
from kraken_bot.secrets import CredentialResult, CredentialStatus
from scripts import run_strategy_once


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


def _migrate_db_command(args: argparse.Namespace) -> int:
    """Run portfolio schema migrations for the SQLite store at --db-path."""

    print(f"Starting migration for {args.db_path}")

    try:
        stored_version = _get_schema_version(args.db_path)
    except PortfolioSchemaError as exc:
        print(
            "Migration failed: "
            f"stored schema version value {exc.found} is incompatible with expected {exc.expected}."
        )
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"Migration failed: {exc}")
        return 1

    version_text = stored_version if stored_version is not None else "unknown"
    print(
        f"Stored schema version: {version_text}; target version: {CURRENT_SCHEMA_VERSION}"
    )

    try:
        SQLitePortfolioStore(db_path=args.db_path)
    except PortfolioSchemaError as exc:
        print(
            "Migration failed: "
            f"stored schema version {exc.found} is incompatible with expected {exc.expected}."
        )
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"Migration failed: {exc}")
        return 1

    print(
        f"Migration completed successfully to version {CURRENT_SCHEMA_VERSION}."
    )
    return 0


def _schema_version_command(args: argparse.Namespace) -> int:
    """Display the current portfolio schema version stored at --db-path."""

    try:
        stored_version = _get_schema_version(args.db_path)
    except PortfolioSchemaError as exc:
        print(
            "Failed to read schema version: "
            f"stored value {exc.found} is incompatible with expected {exc.expected}."
        )
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"Failed to read schema version: {exc}")
        return 1

    if stored_version is None:
        print("Schema version not set; meta table or schema_version row is missing.")
        return 0

    print(f"Schema version: {stored_version}")
    return 0


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
    migrate_parser.add_argument(
        "--db-path",
        default="portfolio.db",
        help="Path to the SQLite portfolio store (defaults to portfolio.db)",
    )
    migrate_parser.set_defaults(func=_migrate_db_command)

    version_parser = subparsers.add_parser(
        "db-schema-version",
        help="Show the stored schema version for the SQLite portfolio DB",
    )
    version_parser.add_argument(
        "--db-path",
        default="portfolio.db",
        help="Path to the SQLite portfolio store (defaults to portfolio.db)",
    )
    version_parser.set_defaults(func=_schema_version_command)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point for the `krakked` console script."""

    parser = _build_parser()
    args = parser.parse_args(argv)
    command: Callable[[argparse.Namespace], int] = getattr(args, "func")
    return command(args)


if __name__ == "__main__":
    sys.exit(main())
