"""Command line interface for Krakked utilities."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tempfile
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from krakked import APP_VERSION, secrets
from krakked.backtest import (
    BacktestPreflightResult,
    BacktestResult,
    build_backtest_preflight,
    load_backtest_report,
    publish_latest_backtest_report,
    run_backtest,
    write_backtest_report,
)
from krakked.connection.exceptions import (
    AuthError,
    KrakenAPIError,
    RateLimitError,
    ServiceUnavailableError,
)
from krakked.config import AppConfig, get_config_dir, get_default_ohlc_store_config, load_config
from krakked.connection.rest_client import KrakenRESTClient
from krakked.credentials import CredentialResult, CredentialStatus
from krakked.main import run as run_orchestrator
from krakked.portfolio.exceptions import PortfolioSchemaError
from krakked.portfolio.store import (
    CURRENT_SCHEMA_VERSION,
    SchemaStatus,
    ensure_portfolio_schema,
    ensure_portfolio_tables,
)
from krakked.scripts import run_strategy_once
from krakked.utils.io import backup_file

DEFAULT_DB_PATH = "portfolio.db"
EXPORT_MANIFEST_NAME = "manifest.json"
WINDOWS_FILE_RETRY_ATTEMPTS = 30
WINDOWS_FILE_RETRY_DELAY_SECONDS = 0.2


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


def _default_data_dir() -> Path:
    """Infer the default data directory from the OHLC store configuration."""

    root_dir = Path(get_default_ohlc_store_config()["root_dir"]).expanduser().resolve()
    return root_dir.parent


def _ensure_safe_archive_member(member_name: str) -> Path:
    """Normalize and validate an archive member path."""

    normalized = Path(member_name)
    if normalized.is_absolute() or ".." in normalized.parts:
        raise ValueError(f"Unsafe archive member: {member_name}")
    return normalized


def _backup_sqlite_database(source_path: Path, destination_path: Path) -> None:
    """Write a SQLite-consistent copy of ``source_path`` to ``destination_path``."""

    source = sqlite3.connect(source_path.as_posix())
    destination = sqlite3.connect(destination_path.as_posix())
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()


def _make_timestamped_backup_path(path: Path) -> Path:
    """Return a timestamped backup path next to ``path``."""

    return path.with_name(f"{path.name}.{int(time.time())}.bak")


def _iter_files_for_archive(base_dir: Path) -> list[Path]:
    """Return regular files under ``base_dir`` while skipping temp artefacts."""

    return sorted(
        path
        for path in base_dir.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and not path.name.endswith(".tmp")
        and not path.name.endswith(".pyc")
    )


def _write_archive_file(
    archive: zipfile.ZipFile, source_path: Path, archive_path: Path
) -> None:
    """Add a file to the export archive using a stable relative path."""

    archive.write(source_path, archive_path.as_posix())


def _restore_archive_bytes(
    target_path: Path, payload: bytes, *, overwrite: bool = False
) -> None:
    """Write extracted archive bytes to disk, optionally backing up existing files."""

    target_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target_path.with_suffix(target_path.suffix + ".tmp")
    temp_path.write_bytes(payload)

    if target_path.exists():
        if not overwrite:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass
            raise FileExistsError(str(target_path))

        try:
            if target_path.suffix == ".db":
                backup_path = _make_timestamped_backup_path(target_path)
                _backup_sqlite_database(target_path, backup_path)

                for attempt in range(WINDOWS_FILE_RETRY_ATTEMPTS):
                    try:
                        _backup_sqlite_database(temp_path, target_path)
                        temp_path.unlink()
                        return
                    except (PermissionError, sqlite3.Error):
                        if attempt == WINDOWS_FILE_RETRY_ATTEMPTS - 1:
                            raise
                        time.sleep(WINDOWS_FILE_RETRY_DELAY_SECONDS)
            else:
                backup_file(target_path)
                for attempt in range(WINDOWS_FILE_RETRY_ATTEMPTS):
                    try:
                        target_path.unlink()
                        break
                    except PermissionError:
                        if attempt == WINDOWS_FILE_RETRY_ATTEMPTS - 1:
                            raise
                        time.sleep(WINDOWS_FILE_RETRY_DELAY_SECONDS)
        except Exception:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass
            raise

    for attempt in range(WINDOWS_FILE_RETRY_ATTEMPTS):
        try:
            temp_path.replace(target_path)
            break
        except PermissionError:
            if attempt == WINDOWS_FILE_RETRY_ATTEMPTS - 1:
                raise
            time.sleep(WINDOWS_FILE_RETRY_DELAY_SECONDS)


def _format_schema_version_mismatch(
    prefix: str, exc: PortfolioSchemaError, *, include_value_word: bool = False
) -> str:
    value_word = " value" if include_value_word else ""
    return (
        f"{prefix}: stored schema version{value_word} {exc.found} "
        f"is incompatible with expected {exc.expected}."
    )


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
            or (
                "Encrypted credentials are locked; set KRAKKED_SECRET_PW to the "
                "master password."
            )
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


def _parse_datetime_arg(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"

    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _load_backtest_config(args: argparse.Namespace) -> AppConfig:
    config_path = Path(args.config).expanduser().resolve() if args.config else None
    config = load_config(config_path=config_path, env="paper")

    if args.pair:
        requested_pairs = [str(pair) for pair in args.pair]
        requested_set = set(requested_pairs)
        config.universe.include_pairs = requested_pairs
        for strat_cfg in config.strategies.configs.values():
            params = strat_cfg.params or {}
            pair_values = params.get("pairs")
            if isinstance(pair_values, list):
                params["pairs"] = [
                    pair for pair in pair_values if pair in requested_set
                ]
                strat_cfg.params = params

    return config


def _print_backtest_summary(
    result: BacktestResult,
    *,
    persist_db_path: str | None = None,
    report_path: str | None = None,
) -> None:
    summary = result.summary
    if summary is None:
        print("Backtest completed, but no summary was generated.")
        return

    print("Backtest completed.")
    print(
        f"Window: {summary.start.isoformat()} -> {summary.end.isoformat()} "
        f"({summary.total_cycles} replay cycles)"
    )
    print(
        f"Pairs: {', '.join(summary.pairs)} | Timeframes: {', '.join(summary.timeframes)}"
    )
    print(
        f"Wallet: start ${summary.starting_cash_usd:,.2f} -> "
        f"end ${summary.ending_equity_usd:,.2f} "
        f"({summary.absolute_pnl_usd:+,.2f}, {summary.return_pct:+.2f}%)"
    )
    print(
        f"PnL: realized ${summary.realized_pnl_usd:,.2f} | "
        f"unrealized ${summary.unrealized_pnl_usd:,.2f} | "
        f"max drawdown {summary.max_drawdown_pct:.2f}%"
    )
    print(f"Replay trust: {summary.trust_note}")
    print(f"Actions: {summary.total_actions} total, {summary.blocked_actions} blocked")
    print(
        f"Orders: {summary.total_orders} total, {summary.filled_orders} filled, "
        f"{summary.rejected_orders} rejected"
    )
    print(f"Execution errors: {summary.execution_errors}")
    print(
        f"Cost model: {summary.slippage_bps:.0f} bps slippage + "
        f"{summary.fee_bps:.2f} bps taker fee"
    )

    if summary.missing_series:
        print("Missing OHLC series:")
        for series in summary.missing_series:
            print(f"- {series}")
    if summary.partial_series:
        print("Partial-window OHLC series:")
        for series in summary.partial_series:
            print(f"- {series}")
    if summary.notable_warnings:
        print("Important warnings:")
        for warning in summary.notable_warnings:
            print(f"- {warning}")
    if summary.blocked_reason_counts:
        top_reason, count = next(iter(summary.blocked_reason_counts.items()))
        print(f"Top blocked reason: {top_reason} ({count})")

    print("Simulation limits:")
    for assumption in summary.assumptions:
        print(f"- {assumption}")

    if persist_db_path:
        print(f"SQLite output: {persist_db_path}")
    if report_path:
        print(f"Saved report: {report_path}")


def _write_backtest_report(payload: dict[str, Any], report_path: str) -> str:
    return str(write_backtest_report(payload, report_path))


def _load_backtest_report(report_path: str) -> dict[str, Any]:
    return load_backtest_report(report_path)


def _print_backtest_preflight(result: BacktestPreflightResult) -> None:
    preflight = result.preflight
    print("Backtest preflight")
    print(
        f"Window: {result.start.isoformat()} -> {result.end.isoformat()} "
        f"| Pairs: {', '.join(result.pairs)} | Timeframes: {', '.join(result.timeframes)}"
    )
    print(
        f"Coverage status: {preflight.status} "
        f"({preflight.usable_series_count} usable, "
        f"{len(preflight.partial_series)} partial, {len(preflight.missing_series)} missing)"
    )
    print(f"Replay readiness: {preflight.summary_note}")
    if preflight.warnings:
        print("Warnings:")
        for warning in preflight.warnings:
            print(f"- {warning}")
    print("Series coverage:")
    for item in preflight.coverage:
        first_bar = item.first_bar_at.isoformat() if item.first_bar_at else "none"
        last_bar = item.last_bar_at.isoformat() if item.last_bar_at else "none"
        print(
            f"- {item.series_key}: {item.status}, {item.bar_count} bars, "
            f"first {first_bar}, last {last_bar}"
        )


def _format_delta(label: str, baseline: float, candidate: float, suffix: str = "") -> str:
    delta = candidate - baseline
    return (
        f"{label}: {baseline:,.2f}{suffix} -> {candidate:,.2f}{suffix} "
        f"({delta:+,.2f}{suffix})"
    )


def _compare_backtests_command(args: argparse.Namespace) -> int:
    try:
        baseline = _load_backtest_report(args.baseline)
        candidate = _load_backtest_report(args.candidate)
    except ValueError as exc:
        return _print_error(f"Compare-backtests failed: {exc}")

    baseline_summary = baseline["summary"]
    candidate_summary = candidate["summary"]

    print("Backtest comparison")
    print(f"Baseline: {Path(args.baseline).expanduser().resolve()}")
    print(f"Candidate: {Path(args.candidate).expanduser().resolve()}")
    print(
        _format_delta(
            "Ending equity USD",
            float(baseline_summary.get("ending_equity_usd", 0.0)),
            float(candidate_summary.get("ending_equity_usd", 0.0)),
        )
    )
    print(
        _format_delta(
            "Total return pct",
            float(baseline_summary.get("return_pct", 0.0)),
            float(candidate_summary.get("return_pct", 0.0)),
            suffix="%",
        )
    )
    print(
        _format_delta(
            "Max drawdown pct",
            float(baseline_summary.get("max_drawdown_pct", 0.0)),
            float(candidate_summary.get("max_drawdown_pct", 0.0)),
            suffix="%",
        )
    )
    print(
        _format_delta(
            "Filled orders",
            float(baseline_summary.get("filled_orders", 0.0)),
            float(candidate_summary.get("filled_orders", 0.0)),
        )
    )
    print(
        _format_delta(
            "Blocked actions",
            float(baseline_summary.get("blocked_actions", 0.0)),
            float(candidate_summary.get("blocked_actions", 0.0)),
        )
    )
    print(
        _format_delta(
            "Execution errors",
            float(baseline_summary.get("execution_errors", 0.0)),
            float(candidate_summary.get("execution_errors", 0.0)),
        )
    )

    baseline_per_strategy = baseline_summary.get("per_strategy") or {}
    candidate_per_strategy = candidate_summary.get("per_strategy") or {}
    overlapping = sorted(set(baseline_per_strategy) & set(candidate_per_strategy))
    if overlapping:
        print("Per-strategy realized PnL delta:")
        for strategy_id in overlapping:
            baseline_pnl = float(
                (baseline_per_strategy.get(strategy_id) or {}).get(
                    "realized_pnl_usd", 0.0
                )
            )
            candidate_pnl = float(
                (candidate_per_strategy.get(strategy_id) or {}).get(
                    "realized_pnl_usd", 0.0
                )
            )
            delta = candidate_pnl - baseline_pnl
            print(
                f"- {strategy_id}: {baseline_pnl:,.2f} -> "
                f"{candidate_pnl:,.2f} ({delta:+,.2f})"
            )

    return 0


def _backtest_preflight_command(args: argparse.Namespace) -> int:
    try:
        start = _parse_datetime_arg(args.start)
        end = _parse_datetime_arg(args.end)
    except ValueError as exc:
        return _print_error(f"Invalid backtest datetime: {exc}")

    try:
        config = _load_backtest_config(args)
        result = build_backtest_preflight(
            config,
            start=start,
            end=end,
            timeframes=args.timeframe,
        )
    except Exception as exc:  # noqa: BLE001
        return _print_error(f"Backtest preflight failed: {exc}")

    if args.strict_data and (
        result.preflight.missing_series or result.preflight.partial_series
    ):
        return _print_error(
            "Backtest preflight failed in strict mode: "
            + "; ".join(
                part
                for part in [
                    (
                        "missing: " + ", ".join(result.preflight.missing_series)
                        if result.preflight.missing_series
                        else ""
                    ),
                    (
                        "partial: " + ", ".join(result.preflight.partial_series)
                        if result.preflight.partial_series
                        else ""
                    ),
                ]
                if part
            )
        )

    payload = result.to_dict()
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        _print_backtest_preflight(result)
    return 0


def _backtest_command(args: argparse.Namespace) -> int:
    """Replay stored OHLC data through the strategy/risk/execution stack."""

    try:
        start = _parse_datetime_arg(args.start)
        end = _parse_datetime_arg(args.end)
    except ValueError as exc:
        return _print_error(f"Invalid backtest datetime: {exc}")

    try:
        config = _load_backtest_config(args)
        result = run_backtest(
            config,
            start=start,
            end=end,
            timeframes=args.timeframe,
            starting_cash_usd=float(args.starting_cash_usd),
            fee_bps=float(args.fee_bps),
            db_path=args.db_path,
            strict_data=bool(args.strict_data),
        )
    except Exception as exc:  # noqa: BLE001
        return _print_error(f"Backtest failed: {exc}")

    payload = result.to_report_dict()
    if result.summary is not None:
        payload["summary"]["replay_inputs"]["config_path"] = (
            str(Path(args.config).expanduser().resolve()) if args.config else None
        )
    payload["provenance"] = {
        "app_version": APP_VERSION,
        "config_path": (
            str(Path(args.config).expanduser().resolve()) if args.config else None
        ),
        "generated_by": "krakked backtest",
    }
    if args.db_path:
        payload["sqlite_output"] = str(Path(args.db_path).expanduser().resolve())

    saved_report_path: str | None = None
    if args.save_report:
        try:
            saved_report_path = _write_backtest_report(payload, args.save_report)
        except Exception as exc:  # noqa: BLE001
            return _print_error(f"Backtest report write failed: {exc}")

    published_report_path: str | None = None
    if args.publish_latest:
        try:
            published_report_path = str(
                publish_latest_backtest_report(payload, config_dir=get_config_dir())
            )
        except Exception as exc:  # noqa: BLE001
            return _print_error(f"Backtest latest-report publish failed: {exc}")

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        persist_db_path = (
            str(Path(args.db_path).expanduser().resolve()) if args.db_path else None
        )
        _print_backtest_summary(
            result,
            persist_db_path=persist_db_path,
            report_path=saved_report_path or published_report_path,
        )
        if saved_report_path and published_report_path:
            print(f"Published latest replay: {published_report_path}")

    return 0


def _get_schema_version(db_path: str) -> int | None:
    """Fetch the stored schema version from the portfolio meta table, if present."""

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='meta'")
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
        status = ensure_portfolio_schema(conn, CURRENT_SCHEMA_VERSION, migrate=True)
        ensure_portfolio_tables(conn)
        conn.commit()

    return status


def print_schema_version(db_path: str) -> SchemaStatus:
    """Ensure metadata exists and return the stored portfolio schema version."""

    with sqlite3.connect(db_path) as conn:
        status = ensure_portfolio_schema(conn, CURRENT_SCHEMA_VERSION, migrate=False)
        conn.commit()

    return status


def _migrate_command(args: argparse.Namespace) -> int:
    """Run portfolio schema migrations for the SQLite store at --db-path."""

    # db_path might come from --db-path (default) or --db (legacy alias if present)
    path_arg = (
        getattr(args, "db_path", None) or getattr(args, "db", None) or DEFAULT_DB_PATH
    )
    db_path = Path(path_arg).expanduser().resolve().as_posix()

    print(f"Starting migration for {db_path}")

    try:
        stored_version = _get_schema_version(db_path)
    except PortfolioSchemaError as exc:
        return _print_error(
            f"Migration failed: stored schema version value {exc.found} "
            f"is incompatible with expected {exc.expected}."
        )
    except Exception as exc:  # noqa: BLE001
        return _print_error(f"Migration failed: {exc}")

    version_text = stored_version if stored_version is not None else "unknown"
    print(
        "Stored schema version: "
        f"{version_text}; target version: {CURRENT_SCHEMA_VERSION}"
    )

    try:
        status = run_migrate_db(db_path)
    except PortfolioSchemaError as exc:
        return _print_error(
            f"Migration failed: stored schema version {exc.found} "
            f"is incompatible with expected {exc.expected}."
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
            f"Failed to read schema version: stored value {exc.found} "
            f"is incompatible with expected {exc.expected}."
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

    temp_backup_path = backup_path.with_suffix(backup_path.suffix + ".tmp")

    try:
        if temp_backup_path.exists():
            temp_backup_path.unlink()

        src = sqlite3.connect(db_path.as_posix())
        dst = sqlite3.connect(temp_backup_path.as_posix())
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()

        temp_backup_path.replace(backup_path)
    except Exception as exc:  # noqa: BLE001
        try:
            if temp_backup_path.exists():
                temp_backup_path.unlink()
        except Exception:  # noqa: BLE001
            pass
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
            f"Failed to read schema version: stored value {exc.found} "
            f"is incompatible with expected {exc.expected}."
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


def _export_install_command(args: argparse.Namespace) -> int:
    """Export a self-hosted Krakked install into a single zip archive."""

    config_dir = Path(args.config_dir).expanduser().resolve()
    db_path = Path(args.db_path).expanduser().resolve()
    data_dir = Path(args.data_dir).expanduser().resolve()

    if not config_dir.exists():
        return _print_error(f"Config directory not found: {config_dir}")
    if not _db_path_exists(db_path.as_posix()):
        return _print_error(f"DB file not found: {db_path}")
    if args.include_data and not data_dir.exists():
        return _print_error(f"Data directory not found: {data_dir}")

    archive_path = (
        Path(args.output).expanduser().resolve()
        if args.output
        else Path.cwd() / f"krakked-export-{datetime.now().strftime('%Y%m%d%H%M')}.zip"
    )
    archive_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        schema_version = _get_schema_version(db_path.as_posix())
    except Exception:
        schema_version = None

    manifest: dict[str, Any] = {
        "format_version": 1,
        "created_at": datetime.utcnow().isoformat() + "Z",
        "app_version": APP_VERSION,
        "db_schema_version": schema_version,
        "includes": {
            "config": True,
            "database": True,
            "data": bool(args.include_data),
        },
        "paths": {
            "config_dir": str(config_dir),
            "db_path": str(db_path),
            "data_dir": str(data_dir) if args.include_data else None,
        },
    }

    try:
        with tempfile.TemporaryDirectory(prefix="krakked-export-") as temp_dir_raw:
            temp_dir = Path(temp_dir_raw)
            db_copy_path = temp_dir / "portfolio.db"
            _backup_sqlite_database(db_path, db_copy_path)

            with zipfile.ZipFile(
                archive_path, mode="w", compression=zipfile.ZIP_DEFLATED
            ) as archive:
                archive.writestr(
                    EXPORT_MANIFEST_NAME, json.dumps(manifest, indent=2, sort_keys=True)
                )

                for config_file in _iter_files_for_archive(config_dir):
                    archive_member = Path("config") / config_file.relative_to(
                        config_dir
                    )
                    _write_archive_file(archive, config_file, archive_member)

                _write_archive_file(archive, db_copy_path, Path("state/portfolio.db"))

                if args.include_data:
                    for data_file in _iter_files_for_archive(data_dir):
                        archive_member = Path("data") / data_file.relative_to(data_dir)
                        _write_archive_file(archive, data_file, archive_member)
    except Exception as exc:  # noqa: BLE001
        return _print_error(f"Failed to export install: {exc}")

    print(f"Export created at {archive_path}")
    print(f"- Config source: {config_dir}")
    print(f"- Database source: {db_path}")
    if args.include_data:
        print(f"- Data source: {data_dir}")
    return 0


def _import_install_command(args: argparse.Namespace) -> int:
    """Import a previously exported Krakked self-hosted install archive."""

    archive_path = Path(args.input).expanduser().resolve()
    config_dir = Path(args.config_dir).expanduser().resolve()
    db_path = Path(args.db_path).expanduser().resolve()
    data_dir = Path(args.data_dir).expanduser().resolve()

    if not archive_path.exists():
        return _print_error(f"Archive not found: {archive_path}")

    try:
        with zipfile.ZipFile(archive_path, mode="r") as archive:
            names = archive.namelist()
            if EXPORT_MANIFEST_NAME not in names:
                return _print_error("Archive is missing manifest.json")

            manifest = json.loads(archive.read(EXPORT_MANIFEST_NAME))
            if not isinstance(manifest, dict):
                return _print_error("Archive manifest is invalid")

            for member_name in names:
                _ensure_safe_archive_member(member_name)

            existing_conflicts: list[Path] = []
            for member_name in names:
                if member_name == EXPORT_MANIFEST_NAME or member_name.endswith("/"):
                    continue

                member_path = Path(member_name)
                if member_path.parts[0] == "config":
                    target_path = config_dir.joinpath(*member_path.parts[1:])
                elif member_path.parts[0] == "state":
                    target_path = db_path
                elif member_path.parts[0] == "data":
                    if args.skip_data:
                        continue
                    target_path = data_dir.joinpath(*member_path.parts[1:])
                else:
                    continue

                if target_path.exists() and not args.force:
                    existing_conflicts.append(target_path)

            if existing_conflicts:
                conflict_lines = "\n".join(
                    f"- {path}" for path in existing_conflicts[:10]
                )
                return _print_error(
                    "Import would overwrite existing files. Re-run with --force.\n"
                    + conflict_lines
                )

            for member_name in names:
                if member_name == EXPORT_MANIFEST_NAME or member_name.endswith("/"):
                    continue

                member_path = Path(member_name)
                payload = archive.read(member_name)

                if member_path.parts[0] == "config":
                    target_path = config_dir.joinpath(*member_path.parts[1:])
                elif member_path.parts[0] == "state":
                    target_path = db_path
                elif member_path.parts[0] == "data":
                    if args.skip_data:
                        continue
                    target_path = data_dir.joinpath(*member_path.parts[1:])
                else:
                    continue

                _restore_archive_bytes(target_path, payload, overwrite=args.force)
    except Exception as exc:  # noqa: BLE001
        return _print_error(f"Failed to import install: {exc}")

    print(f"Imported archive from {archive_path}")
    print(f"- Config restored to: {config_dir}")
    print(f"- Database restored to: {db_path}")
    if not args.skip_data:
        print(f"- Data restored to: {data_dir}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="krakked", description="Krakked utilities")
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

    backtest_preflight_parser = subparsers.add_parser(
        "backtest-preflight",
        help="Check local historical coverage for an offline replay without running strategies",
    )
    backtest_preflight_parser.add_argument(
        "--start",
        required=True,
        help="Preflight start time in ISO-8601 form",
    )
    backtest_preflight_parser.add_argument(
        "--end",
        required=True,
        help="Preflight end time in ISO-8601 form",
    )
    backtest_preflight_parser.add_argument(
        "--config",
        help="Optional path to the base config.yaml to inspect",
    )
    backtest_preflight_parser.add_argument(
        "--pair",
        action="append",
        help="Limit the preflight to one pair; repeat to include multiple pairs",
    )
    backtest_preflight_parser.add_argument(
        "--timeframe",
        action="append",
        help="Limit the preflight to one timeframe; repeat to include multiple timeframes",
    )
    backtest_preflight_parser.add_argument(
        "--strict-data",
        action="store_true",
        help="Fail if any requested pair/timeframe is missing or only partially covered",
    )
    backtest_preflight_parser.add_argument(
        "--json",
        action="store_true",
        help="Print the preflight payload as JSON",
    )
    backtest_preflight_parser.set_defaults(func=_backtest_preflight_command)

    backtest_parser = subparsers.add_parser(
        "backtest",
        help="Replay stored OHLC data offline through the strategy, risk, and execution layers",
    )
    backtest_parser.add_argument(
        "--start",
        required=True,
        help="Backtest start time in ISO-8601 form (for example 2026-04-01 or 2026-04-01T00:00:00Z)",
    )
    backtest_parser.add_argument(
        "--end",
        required=True,
        help="Backtest end time in ISO-8601 form (for example 2026-04-20 or 2026-04-20T00:00:00Z)",
    )
    backtest_parser.add_argument(
        "--config",
        help="Optional path to the base config.yaml to use for the replay",
    )
    backtest_parser.add_argument(
        "--pair",
        action="append",
        help="Limit the replay to one pair; repeat to include multiple pairs",
    )
    backtest_parser.add_argument(
        "--timeframe",
        action="append",
        help="Limit the replay to one timeframe; repeat to include multiple timeframes",
    )
    backtest_parser.add_argument(
        "--starting-cash-usd",
        type=float,
        default=10_000.0,
        help="Synthetic starting USD wallet balance for the offline replay",
    )
    backtest_parser.add_argument(
        "--fee-bps",
        type=float,
        default=25.0,
        help="Flat taker fee in basis points applied to simulated fills",
    )
    backtest_parser.add_argument(
        "--db-path",
        help="Optional SQLite path to persist decisions, orders, and execution results",
    )
    backtest_parser.add_argument(
        "--save-report",
        help="Optional JSON path for a durable backtest report artifact",
    )
    backtest_parser.add_argument(
        "--publish-latest",
        action="store_true",
        help="Publish the validated replay summary to the canonical latest-report path for the operator UI",
    )
    backtest_parser.add_argument(
        "--strict-data",
        action="store_true",
        help="Fail the run if any requested pair/timeframe is missing or only partially covered",
    )
    backtest_parser.add_argument(
        "--json",
        action="store_true",
        help="Print the backtest summary as JSON",
    )
    backtest_parser.set_defaults(func=_backtest_command)

    compare_backtests_parser = subparsers.add_parser(
        "compare-backtests",
        help="Compare two saved backtest JSON reports without rerunning simulations",
    )
    compare_backtests_parser.add_argument(
        "--baseline",
        required=True,
        help="Path to the baseline saved report JSON",
    )
    compare_backtests_parser.add_argument(
        "--candidate",
        required=True,
        help="Path to the candidate saved report JSON",
    )
    compare_backtests_parser.set_defaults(func=_compare_backtests_command)

    # Consolidated Migration Command
    migrate_parser = subparsers.add_parser(
        "migrate",
        help="Migrate the portfolio DB schema to the current code version",
    )
    _add_db_path_argument(migrate_parser)
    migrate_parser.set_defaults(func=_migrate_command)

    # Legacy aliases (hidden/deprecated)
    legacy_migrate_parser = subparsers.add_parser(
        "migrate-db",
        help=argparse.SUPPRESS,  # Hidden from help
    )
    _add_db_path_argument(legacy_migrate_parser)
    legacy_migrate_parser.set_defaults(func=_migrate_command)

    legacy_portfolio_migrate_parser = subparsers.add_parser(
        "portfolio-migrate",
        help=argparse.SUPPRESS,  # Hidden from help
    )
    legacy_portfolio_migrate_parser.add_argument(
        "--db", type=str, help="Path to portfolio SQLite DB"
    )
    legacy_portfolio_migrate_parser.set_defaults(func=_migrate_command)

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

    export_parser = subparsers.add_parser(
        "export-install",
        help="Export config, database, and optional data files into a zip archive",
    )
    export_parser.add_argument(
        "--output",
        help="Destination zip path (defaults to ./krakked-export-<timestamp>.zip)",
    )
    export_parser.add_argument(
        "--config-dir",
        default=str(get_config_dir()),
        help="Configuration directory to export",
    )
    export_parser.add_argument(
        "--db-path",
        default=DEFAULT_DB_PATH,
        help="SQLite database path to export",
    )
    export_parser.add_argument(
        "--data-dir",
        default=str(_default_data_dir()),
        help="Data directory to export when --include-data is set",
    )
    export_parser.add_argument(
        "--include-data",
        action="store_true",
        help="Include cached market data and metadata files in the archive",
    )
    export_parser.set_defaults(func=_export_install_command)

    import_parser = subparsers.add_parser(
        "import-install",
        help="Import a previously exported install archive",
    )
    import_parser.add_argument(
        "--input",
        required=True,
        help="Path to an archive created by `krakked export-install`",
    )
    import_parser.add_argument(
        "--config-dir",
        default=str(get_config_dir()),
        help="Configuration directory to restore into",
    )
    import_parser.add_argument(
        "--db-path",
        default=DEFAULT_DB_PATH,
        help="SQLite database path to restore into",
    )
    import_parser.add_argument(
        "--data-dir",
        default=str(_default_data_dir()),
        help="Data directory to restore into",
    )
    import_parser.add_argument(
        "--skip-data",
        action="store_true",
        help="Skip restoring any archived data/ files",
    )
    import_parser.add_argument(
        "--force",
        action="store_true",
        help="Allow overwriting existing files (existing targets are backed up first)",
    )
    import_parser.set_defaults(func=_import_install_command)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point for the `krakked` console script."""

    parser = _build_parser()
    args = parser.parse_args(argv)
    command: Callable[[argparse.Namespace], int] = getattr(args, "func")
    return command(args)


if __name__ == "__main__":
    sys.exit(main())
