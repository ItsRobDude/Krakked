"""Command line interface for kraken_bot utilities."""

from __future__ import annotations

import argparse
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

    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point for the `krakked` console script."""

    parser = _build_parser()
    args = parser.parse_args(argv)
    command: Callable[[argparse.Namespace], int] = getattr(args, "func")
    return command(args)


if __name__ == "__main__":
    sys.exit(main())
