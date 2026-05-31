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
    DEFAULT_EXPOSURE_OVERLAY_MODES,
    DEFAULT_EXPOSURE_SCENARIOS,
    DEFAULT_STRATEGY_ACTIVITY_GROUP_IDS,
    STRATEGY_ACTIVITY_WINDOW_SETS,
    BacktestPreflightResult,
    BacktestResult,
    MarketRegimeExposureScenarioParams,
    MarketRegimeOverlayParams,
    RSRotationV2ResearchParams,
    backtest_strict_data_details,
    build_backtest_preflight,
    default_rs_rotation_v2_allocation_pct,
    default_rs_rotation_v2_lookback_bars,
    default_rs_rotation_v2_timeframe,
    default_rs_rotation_v2_top_n,
    load_backtest_report,
    publish_latest_backtest_report,
    publish_latest_ml_walk_forward_report,
    run_backtest,
    run_market_regime_exposure_research,
    run_market_regime_overlay_backtest,
    run_market_regime_research,
    run_market_regime_throttle_backtest,
    run_ml_walk_forward,
    run_rs_rotation_v2_research,
    run_strategy_activity_sweep,
    write_backtest_report,
    write_ml_walk_forward_report,
)
from krakked.backtest.ml_feature_ablation_summary import (
    render_ml_feature_ablation_summary,
    summarize_ml_feature_ablation,
)
from krakked.backtest.ml_report_compare import (
    compare_ml_reports,
    render_ml_report_comparison,
)
from krakked.backtest.strategy_activity import (
    apply_strategy_activity_override,
    build_strategy_activity_groups,
)
from krakked.config import (
    AppConfig,
    get_config_dir,
    get_default_ohlc_store_config,
    load_config,
)
from krakked.connection.exceptions import (
    AuthError,
    KrakenAPIError,
    RateLimitError,
    ServiceUnavailableError,
)
from krakked.connection.rest_client import KrakenRESTClient
from krakked.credentials import CredentialResult, CredentialStatus
from krakked.main import run as run_orchestrator
from krakked.market_data.api import MarketDataAPI
from krakked.portfolio.exceptions import PortfolioSchemaError
from krakked.portfolio.store import (
    CURRENT_SCHEMA_VERSION,
    SchemaStatus,
    SQLitePortfolioStore,
    ensure_portfolio_schema,
    ensure_portfolio_tables,
)
from krakked.scripts import run_strategy_once
from krakked.strategy.features import ML_FEATURE_PROFILES
from krakked.strategy.ml_pruning import find_stale_ml_artifact_groups
from krakked.utils.io import backup_file

DEFAULT_DB_PATH = "portfolio.db"
EXPORT_MANIFEST_NAME = "manifest.json"
WINDOWS_FILE_RETRY_ATTEMPTS = 30
WINDOWS_FILE_RETRY_DELAY_SECONDS = 0.2
MARKET_REGIME_EXPOSURE_WINDOW_SETS = {
    "recent_20d": [
        (
            "20260321-20260410",
            "2026-03-21T00:00:00Z",
            "2026-04-10T00:00:00Z",
        ),
        (
            "20260410-20260430",
            "2026-04-10T00:00:00Z",
            "2026-04-30T00:00:00Z",
        ),
        (
            "20260430-20260520",
            "2026-04-30T00:00:00Z",
            "2026-05-20T00:00:00Z",
        ),
        (
            "20260505-20260525",
            "2026-05-05T00:00:00Z",
            "2026-05-25T00:00:00Z",
        ),
        (
            "20260510-20260530",
            "2026-05-10T00:00:00Z",
            "2026-05-30T00:00:00Z",
        ),
    ],
    "long_4h": [
        (
            "20251221-20260120",
            "2025-12-21T00:00:00Z",
            "2026-01-20T00:00:00Z",
        ),
        (
            "20260120-20260219",
            "2026-01-20T00:00:00Z",
            "2026-02-19T00:00:00Z",
        ),
        (
            "20260219-20260321",
            "2026-02-19T00:00:00Z",
            "2026-03-21T00:00:00Z",
        ),
        (
            "20260321-20260420",
            "2026-03-21T00:00:00Z",
            "2026-04-20T00:00:00Z",
        ),
        (
            "20260420-20260520",
            "2026-04-20T00:00:00Z",
            "2026-05-20T00:00:00Z",
        ),
        (
            "20260430-20260530",
            "2026-04-30T00:00:00Z",
            "2026-05-30T00:00:00Z",
        ),
    ],
}


def _add_db_path_argument(subparser: argparse.ArgumentParser) -> None:
    """Attach the standard --db-path argument to a subparser."""

    subparser.add_argument(
        "--db-path",
        default=DEFAULT_DB_PATH,
        help=f"Path to the SQLite portfolio store (defaults to {DEFAULT_DB_PATH})",
    )


def _add_market_regime_research_arguments(
    subparser: argparse.ArgumentParser,
    *,
    include_overlay_backtest_args: bool = False,
    include_exposure_research_args: bool = False,
) -> None:
    subparser.add_argument(
        "--start",
        required=True,
        help="Research window start time in ISO-8601 form",
    )
    subparser.add_argument(
        "--end",
        required=True,
        help="Research window end time in ISO-8601 form",
    )
    subparser.add_argument(
        "--config",
        help="Optional path to the base config.yaml to use",
    )
    subparser.add_argument(
        "--pair",
        action="append",
        help="Limit to one pair; repeat to include multiple pairs",
    )
    subparser.add_argument(
        "--timeframe",
        default="4h",
        help="OHLC timeframe for market-regime features",
    )
    subparser.add_argument(
        "--benchmark-pair",
        default="BTC/USD",
        help="Benchmark pair used for market-state decisions",
    )
    subparser.add_argument(
        "--momentum-lookback-bars",
        type=int,
        default=42,
        help="Benchmark momentum lookback bars",
    )
    subparser.add_argument(
        "--basket-momentum-lookback-bars",
        type=int,
        default=42,
        help="Starter-basket momentum lookback bars",
    )
    subparser.add_argument(
        "--volatility-lookback-bars",
        type=int,
        default=42,
        help="Benchmark realized-volatility lookback bars",
    )
    subparser.add_argument(
        "--drawdown-lookback-bars",
        type=int,
        default=42,
        help="Benchmark drawdown lookback bars",
    )
    subparser.add_argument(
        "--neutral-allocation-multiplier",
        type=float,
        default=0.5,
        help="Exposure multiplier for neutral states",
    )
    subparser.add_argument(
        "--risk-off-allocation-multiplier",
        type=float,
        default=0.0,
        help="Exposure multiplier for risk-off states",
    )
    subparser.add_argument(
        "--neutral-benchmark-momentum-bps",
        type=float,
        default=150.0,
        help="Benchmark momentum below this value marks neutral",
    )
    subparser.add_argument(
        "--neutral-basket-momentum-bps",
        type=float,
        default=100.0,
        help="Basket momentum below this value marks neutral",
    )
    subparser.add_argument(
        "--risk-off-benchmark-momentum-bps",
        type=float,
        default=0.0,
        help="Benchmark momentum below this value can mark risk-off",
    )
    subparser.add_argument(
        "--risk-off-basket-momentum-bps",
        type=float,
        default=0.0,
        help="Basket momentum below this value can mark risk-off",
    )
    subparser.add_argument(
        "--neutral-benchmark-drawdown-pct",
        type=float,
        default=4.0,
        help="Benchmark drawdown percentage that marks neutral",
    )
    subparser.add_argument(
        "--risk-off-benchmark-drawdown-pct",
        type=float,
        default=8.0,
        help="Benchmark drawdown percentage that marks risk-off",
    )
    subparser.add_argument(
        "--neutral-volatility-pct",
        type=float,
        default=2.5,
        help="Benchmark realized volatility percentage that marks neutral",
    )
    subparser.add_argument(
        "--risk-off-volatility-pct",
        type=float,
        default=4.0,
        help="Benchmark realized volatility percentage that marks risk-off",
    )
    if include_overlay_backtest_args:
        subparser.add_argument(
            "--replay-timeframe",
            action="append",
            help="Limit replay to one timeframe; repeat to include multiple",
        )
        subparser.add_argument(
            "--starting-cash-usd",
            type=float,
            default=10_000.0,
            help="Synthetic starting USD wallet balance for the comparison",
        )
        subparser.add_argument(
            "--fee-bps",
            type=float,
            default=25.0,
            help="Flat taker fee in basis points applied to simulated fills",
        )
    if include_exposure_research_args:
        subparser.add_argument(
            "--scenario",
            action="append",
            choices=sorted(DEFAULT_EXPOSURE_SCENARIOS),
            help=(
                "Controlled exposure scenario to run; repeat to include multiple "
                f"(defaults to {', '.join(DEFAULT_EXPOSURE_SCENARIOS)})"
            ),
        )
        subparser.add_argument(
            "--overlay-mode",
            action="append",
            choices=sorted(DEFAULT_EXPOSURE_OVERLAY_MODES),
            help=(
                "Overlay mode to compare against each baseline scenario; repeat to "
                f"include multiple (defaults to {', '.join(DEFAULT_EXPOSURE_OVERLAY_MODES)})"
            ),
        )
        subparser.add_argument(
            "--allocation-pct",
            type=float,
            default=20.0,
            help="Total synthetic exposure allocation percentage for each scenario",
        )
        subparser.add_argument(
            "--rebalance-interval-bars",
            type=int,
            default=6,
            help="Rebalance cadence in bars for controlled exposure scenarios",
        )
        subparser.add_argument(
            "--starting-cash-usd",
            type=float,
            default=10_000.0,
            help="Synthetic starting USD wallet balance for exposure scenarios",
        )
        subparser.add_argument(
            "--fee-bps",
            type=float,
            default=25.0,
            help="Flat taker fee in basis points applied to simulated scenario trades",
        )
        subparser.add_argument(
            "--target-lookback-bars",
            type=int,
            default=63,
            help=(
                "Bars used by dynamic target scenarios such as trend_proxy "
                "and trend_rank_proxy"
            ),
        )
        subparser.add_argument(
            "--min-momentum-bps",
            type=float,
            default=150.0,
            help="Minimum momentum required for trend_proxy eligibility",
        )
        subparser.add_argument(
            "--max-target-pairs",
            type=int,
            default=4,
            help="Maximum number of dynamic target-scenario pairs to target",
        )
    subparser.add_argument(
        "--save-report",
        help="Optional JSON path for a durable market-regime report artifact",
    )
    subparser.add_argument(
        "--strict-data",
        action="store_true",
        help="Fail if any requested pair/timeframe is missing or partially covered",
    )
    subparser.add_argument(
        "--json",
        action="store_true",
        help="Print the market-regime report as JSON",
    )


def _add_market_regime_throttle_backtest_arguments(
    subparser: argparse.ArgumentParser,
) -> None:
    subparser.add_argument(
        "--start",
        required=True,
        help="Replay window start time in ISO-8601 form",
    )
    subparser.add_argument(
        "--end",
        required=True,
        help="Replay window end time in ISO-8601 form",
    )
    subparser.add_argument(
        "--config",
        help="Optional path to the base config.yaml to use",
    )
    subparser.add_argument(
        "--pair",
        action="append",
        help="Limit the replay and throttle universe to one pair; repeat to include multiple pairs",
    )
    subparser.add_argument(
        "--strategy",
        action="append",
        help=(
            "Temporarily limit replay to one strategy; repeat to test a candidate "
            "strategy pack without changing config"
        ),
    )
    subparser.add_argument(
        "--timeframe",
        default="4h",
        help="OHLC timeframe for runtime market-regime throttle features",
    )
    subparser.add_argument(
        "--benchmark-pair",
        default="BTC/USD",
        help="Benchmark pair used for market-state decisions",
    )
    subparser.add_argument(
        "--momentum-lookback-bars",
        type=int,
        default=63,
        help="Benchmark momentum lookback bars",
    )
    subparser.add_argument(
        "--basket-momentum-lookback-bars",
        type=int,
        default=63,
        help="Starter-basket momentum lookback bars",
    )
    subparser.add_argument(
        "--volatility-lookback-bars",
        type=int,
        default=63,
        help="Benchmark realized-volatility lookback bars",
    )
    subparser.add_argument(
        "--drawdown-lookback-bars",
        type=int,
        default=63,
        help="Benchmark drawdown lookback bars",
    )
    subparser.add_argument(
        "--neutral-allocation-multiplier",
        type=float,
        default=0.75,
        help="Runtime target multiplier for neutral states",
    )
    subparser.add_argument(
        "--risk-off-allocation-multiplier",
        type=float,
        default=0.25,
        help="Runtime target multiplier for risk-off states",
    )
    subparser.add_argument(
        "--neutral-benchmark-momentum-bps",
        type=float,
        default=150.0,
        help="Benchmark momentum below this value marks neutral",
    )
    subparser.add_argument(
        "--neutral-basket-momentum-bps",
        type=float,
        default=100.0,
        help="Basket momentum below this value marks neutral",
    )
    subparser.add_argument(
        "--risk-off-benchmark-momentum-bps",
        type=float,
        default=0.0,
        help="Benchmark momentum below this value can mark risk-off",
    )
    subparser.add_argument(
        "--risk-off-basket-momentum-bps",
        type=float,
        default=0.0,
        help="Basket momentum below this value can mark risk-off",
    )
    subparser.add_argument(
        "--neutral-benchmark-drawdown-pct",
        type=float,
        default=4.0,
        help="Benchmark drawdown percentage that marks neutral",
    )
    subparser.add_argument(
        "--risk-off-benchmark-drawdown-pct",
        type=float,
        default=8.0,
        help="Benchmark drawdown percentage that marks risk-off",
    )
    subparser.add_argument(
        "--neutral-volatility-pct",
        type=float,
        default=2.5,
        help="Benchmark realized volatility percentage that marks neutral",
    )
    subparser.add_argument(
        "--risk-off-volatility-pct",
        type=float,
        default=4.0,
        help="Benchmark realized volatility percentage that marks risk-off",
    )
    subparser.add_argument(
        "--unavailable-policy",
        choices=["block_new_risk", "allow"],
        default="block_new_risk",
        help="Runtime throttle behavior when market-regime data is unavailable",
    )
    subparser.add_argument(
        "--replay-timeframe",
        action="append",
        help="Limit replay to one timeframe; repeat to include multiple",
    )
    subparser.add_argument(
        "--starting-cash-usd",
        type=float,
        default=10_000.0,
        help="Synthetic starting USD wallet balance for the comparison",
    )
    subparser.add_argument(
        "--fee-bps",
        type=float,
        default=25.0,
        help="Flat taker fee in basis points applied to simulated fills",
    )
    subparser.add_argument(
        "--warmup-days",
        type=float,
        default=None,
        help=(
            "Days of cached OHLC before --start to expose for replay warmup; "
            "defaults to the configured strategy/risk lookback requirement"
        ),
    )
    subparser.add_argument(
        "--save-report",
        help="Optional JSON path for a durable runtime-throttle report artifact",
    )
    subparser.add_argument(
        "--strict-data",
        action="store_true",
        help="Fail if any requested pair/timeframe is missing or partially covered",
    )
    subparser.add_argument(
        "--json",
        action="store_true",
        help="Print the runtime-throttle report as JSON",
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


def _parse_since_arg(value: str) -> int:
    stripped = value.strip()
    try:
        if "." in stripped:
            parsed_epoch = int(float(stripped))
        else:
            parsed_epoch = int(stripped, 10)
    except ValueError:
        parsed_epoch = int(_parse_datetime_arg(stripped).timestamp())

    if parsed_epoch < 0:
        raise ValueError("since must be a non-negative epoch or datetime")
    return parsed_epoch


def _print_ohlc_refresh_summary(payload: dict[str, Any]) -> None:
    print("OHLC tail refresh completed.")
    print(
        f"Series: {len(payload['series'])} | "
        f"Fetched bars: {payload['fetched_bars']} | "
        f"Failed: {payload['failed_count']}"
    )
    for item in payload["series"]:
        error_suffix = f" | error: {item['error']}" if item.get("error") else ""
        print(
            f"- {item['pair']}@{item['timeframe']}: {item['status']}, "
            f"fetched {item['fetched_bars']}, "
            f"prior {item['prior_latest_timestamp']}, "
            f"new {item['new_latest_timestamp']}{error_suffix}"
        )


def _refresh_ohlc_command(args: argparse.Namespace) -> int:
    try:
        since = _parse_since_arg(args.since) if args.since else None
    except ValueError as exc:
        return _print_error(f"Invalid refresh since value: {exc}")

    market_data: MarketDataAPI | None = None
    try:
        config_path = Path(args.config).expanduser().resolve() if args.config else None
        config = load_config(config_path=config_path)
        market_data = MarketDataAPI(config)
        market_data.refresh_universe()
        result = market_data.refresh_ohlc_tails(
            pairs=args.pair,
            timeframes=args.timeframe,
            since=since,
        )
    except Exception as exc:  # noqa: BLE001
        return _print_error(f"OHLC tail refresh failed: {exc}")
    finally:
        if market_data is not None:
            market_data.shutdown()

    payload = result.to_dict()
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        _print_ohlc_refresh_summary(payload)

    if result.failed_count:
        return 1
    return 0


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


def _backtest_config_provenance(args: argparse.Namespace) -> dict[str, Any]:
    config_arg = getattr(args, "config", None)
    resolved_config_path = (
        Path(config_arg).expanduser().resolve()
        if config_arg
        else (get_config_dir() / "config.yaml").expanduser().resolve()
    )
    return {
        "config_source": "config_file" if config_arg else "default_paper_config",
        "resolved_config_path": str(resolved_config_path),
        "config_arg_supplied": bool(config_arg),
    }


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
        f"Warmup: {summary.warmup_status} "
        f"({summary.warmup_days:g} days before replay start)"
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
    print(
        f"Actions: {summary.total_actions} total, {summary.blocked_actions} blocked, "
        f"{summary.clamped_actions} clamped"
    )
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
    if summary.warmup_missing_series:
        print("Missing warmup OHLC series:")
        for series in summary.warmup_missing_series:
            print(f"- {series}")
    if summary.warmup_partial_series:
        print("Partial warmup OHLC series:")
        for series in summary.warmup_partial_series:
            print(f"- {series}")
    if summary.notable_warnings:
        print("Important warnings:")
        for warning in summary.notable_warnings:
            print(f"- {warning}")
    if summary.blocked_reason_counts:
        top_reason, count = next(iter(summary.blocked_reason_counts.items()))
        print(f"Top blocked reason: {top_reason} ({count})")
    if summary.clamped_reason_counts:
        top_reason, count = next(iter(summary.clamped_reason_counts.items()))
        print(f"Top clamped reason: {top_reason} ({count})")

    print("Simulation limits:")
    for assumption in summary.assumptions:
        print(f"- {assumption}")

    if persist_db_path:
        print(f"SQLite output: {persist_db_path}")
    if report_path:
        print(f"Saved report: {report_path}")


def _print_rs_rotation_v2_research_summary(
    payload: dict[str, Any], report_path: str | None
) -> None:
    summary = payload["summary"]
    print("RS rotation v2 research completed.")
    print(
        f"Status: {summary['status']} | "
        f"Window: {summary['start']} -> {summary['end']}"
    )
    print(
        f"Pairs: {', '.join(summary['pairs'])} | "
        f"Timeframe: {summary['timeframe']} | "
        f"Cycles: {summary['total_cycles']} "
        f"({summary['active_cycles']} active, {summary['cash_cycles']} cash)"
    )
    print(
        f"Wallet: start ${summary['starting_cash_usd']:,.2f} -> "
        f"end ${summary['ending_equity_usd']:,.2f} "
        f"({summary['absolute_pnl_usd']:+,.2f}, {summary['return_pct']:+.2f}%)"
    )
    print(
        f"Trades: {summary['trade_count']} | "
        f"Turnover ${summary['turnover_usd']:,.2f} | "
        f"Fees ${summary['fees_usd']:,.2f} | "
        f"Slippage estimate ${summary['slippage_estimate_usd']:,.2f}"
    )
    reference = summary.get("equal_weight_reference")
    if reference:
        print(
            "Equal-weight reference: "
            f"{float(reference['return_pct']):+.2f}% "
            f"({int(reference['pair_count'])} pairs at "
            f"{float(reference['allocation_pct']):.2f}% allocation)"
        )
    forward = summary.get("forward_diagnostics") or {}
    if forward.get("evaluated_cycles"):
        selected_forward = forward.get("mean_selected_forward_return_pct")
        universe_forward = forward.get("mean_universe_forward_return_pct")
        spread = forward.get("mean_selected_spread_pct")
        print(
            "Forward check: "
            f"selected {float(selected_forward):+.2f}% | "
            f"universe {float(universe_forward):+.2f}% | "
            f"spread {float(spread):+.2f}%"
        )
    print("Research gates:")
    for gate_name, gate in summary["gates"].items():
        outcome = "pass" if gate.get("passed") else "fail"
        print(f"- {gate_name}: {outcome}")
    if summary.get("warnings"):
        print("Warnings:")
        for warning in summary["warnings"]:
            print(f"- {warning}")
    if report_path:
        print(f"Saved report: {report_path}")


def _market_regime_params_from_args(
    args: argparse.Namespace,
) -> MarketRegimeOverlayParams:
    return MarketRegimeOverlayParams(
        timeframe=args.timeframe,
        benchmark_pair=args.benchmark_pair,
        momentum_lookback_bars=int(args.momentum_lookback_bars),
        basket_momentum_lookback_bars=int(args.basket_momentum_lookback_bars),
        volatility_lookback_bars=int(args.volatility_lookback_bars),
        drawdown_lookback_bars=int(args.drawdown_lookback_bars),
        neutral_allocation_multiplier=float(args.neutral_allocation_multiplier),
        risk_off_allocation_multiplier=float(args.risk_off_allocation_multiplier),
        neutral_benchmark_momentum_bps=float(args.neutral_benchmark_momentum_bps),
        neutral_basket_momentum_bps=float(args.neutral_basket_momentum_bps),
        risk_off_benchmark_momentum_bps=float(args.risk_off_benchmark_momentum_bps),
        risk_off_basket_momentum_bps=float(args.risk_off_basket_momentum_bps),
        neutral_benchmark_drawdown_pct=float(args.neutral_benchmark_drawdown_pct),
        risk_off_benchmark_drawdown_pct=float(args.risk_off_benchmark_drawdown_pct),
        neutral_volatility_pct=float(args.neutral_volatility_pct),
        risk_off_volatility_pct=float(args.risk_off_volatility_pct),
    )


def _market_regime_exposure_scenario_params_from_args(
    args: argparse.Namespace,
) -> MarketRegimeExposureScenarioParams:
    return MarketRegimeExposureScenarioParams(
        allocation_pct=float(args.allocation_pct),
        rebalance_interval_bars=int(args.rebalance_interval_bars),
        starting_cash_usd=float(args.starting_cash_usd),
        fee_bps=float(args.fee_bps),
        target_lookback_bars=int(args.target_lookback_bars),
        min_momentum_bps=float(args.min_momentum_bps),
        max_target_pairs=int(args.max_target_pairs),
    )


def _print_market_regime_research_summary(
    payload: dict[str, Any], report_path: str | None
) -> None:
    summary = payload["summary"]
    print("Market regime research completed.")
    print(f"Window: {summary['start']} -> {summary['end']}")
    print(
        f"Pairs: {', '.join(summary['pairs'])} | "
        f"Benchmark: {summary['benchmark_pair']} | "
        f"Timeframe: {summary['timeframe']}"
    )
    print(
        f"Cycles: {summary['total_cycles']} | "
        f"risk_on {summary['risk_on_cycles']} | "
        f"neutral {summary['neutral_cycles']} | "
        f"risk_off {summary['risk_off_cycles']}"
    )
    if summary.get("reason_counts"):
        print("Top regime reasons:")
        for reason, count in list(summary["reason_counts"].items())[:5]:
            print(f"- {reason}: {count}")
    if report_path:
        print(f"Saved report: {report_path}")


def _print_market_regime_exposure_research_summary(
    payload: dict[str, Any], report_path: str | None
) -> None:
    summary = payload["summary"]
    print("Market regime exposure research completed.")
    print(f"Window: {summary['start']} -> {summary['end']}")
    print(
        f"Scenarios: {', '.join(summary['scenarios'])} | "
        f"Overlay modes: {', '.join(summary['overlay_modes'])}"
    )
    print(
        f"Comparisons: {summary['comparison_count']} | "
        f"positive return {summary['positive_return_comparisons']} | "
        f"drawdown improved {summary['drawdown_improved_comparisons']} | "
        f"not cash-only {summary['not_cash_only_comparisons']}"
    )
    best = summary.get("best_by_return")
    if best:
        delta = best["delta"]
        print(
            "Best return delta: "
            f"{best['scenario_id']} / {best['overlay_mode']} "
            f"{float(delta['return_pct']):+.4f} pct pts"
        )
    if report_path:
        print(f"Saved report: {report_path}")


def _add_market_regime_exposure_sweep_arguments(
    subparser: argparse.ArgumentParser,
) -> None:
    subparser.add_argument(
        "--window-set",
        action="append",
        required=True,
        choices=sorted(MARKET_REGIME_EXPOSURE_WINDOW_SETS),
        help="Window set to run; repeat to include multiple sets",
    )
    subparser.add_argument(
        "--config",
        help="Optional path to the base config.yaml to use",
    )
    subparser.add_argument(
        "--pair",
        action="append",
        help="Limit to one pair; repeat to include multiple pairs",
    )
    subparser.add_argument(
        "--scenario",
        action="append",
        choices=sorted(DEFAULT_EXPOSURE_SCENARIOS),
        default=None,
        help="Controlled exposure scenario to run; repeat to include multiple",
    )
    subparser.add_argument(
        "--overlay-mode",
        action="append",
        choices=sorted(DEFAULT_EXPOSURE_OVERLAY_MODES),
        default=None,
        help="Overlay mode to compare against each baseline scenario",
    )
    subparser.add_argument(
        "--allocation-pct",
        action="append",
        type=float,
        default=None,
        help="Total synthetic allocation percentage; repeat to include multiple",
    )
    subparser.add_argument(
        "--save-dir",
        required=True,
        help="Directory for per-window reports plus aggregate.json",
    )
    subparser.add_argument(
        "--timeframe",
        default="4h",
        help="OHLC timeframe for market-regime and target features",
    )
    subparser.add_argument(
        "--benchmark-pair",
        default="BTC/USD",
        help="Benchmark pair used for market-state decisions",
    )
    subparser.add_argument(
        "--momentum-lookback-bars",
        type=int,
        default=63,
        help="Benchmark momentum lookback bars",
    )
    subparser.add_argument(
        "--basket-momentum-lookback-bars",
        type=int,
        default=63,
        help="Starter-basket momentum lookback bars",
    )
    subparser.add_argument(
        "--volatility-lookback-bars",
        type=int,
        default=63,
        help="Benchmark realized-volatility lookback bars",
    )
    subparser.add_argument(
        "--drawdown-lookback-bars",
        type=int,
        default=63,
        help="Benchmark drawdown lookback bars",
    )
    subparser.add_argument(
        "--neutral-allocation-multiplier",
        type=float,
        default=0.5,
        help="Exposure multiplier for neutral states",
    )
    subparser.add_argument(
        "--risk-off-allocation-multiplier",
        type=float,
        default=0.0,
        help="Exposure multiplier for risk-off states",
    )
    subparser.add_argument(
        "--neutral-benchmark-momentum-bps",
        type=float,
        default=150.0,
        help="Benchmark momentum below this value marks neutral",
    )
    subparser.add_argument(
        "--neutral-basket-momentum-bps",
        type=float,
        default=100.0,
        help="Basket momentum below this value marks neutral",
    )
    subparser.add_argument(
        "--risk-off-benchmark-momentum-bps",
        type=float,
        default=0.0,
        help="Benchmark momentum below this value can mark risk-off",
    )
    subparser.add_argument(
        "--risk-off-basket-momentum-bps",
        type=float,
        default=0.0,
        help="Basket momentum below this value can mark risk-off",
    )
    subparser.add_argument(
        "--neutral-benchmark-drawdown-pct",
        type=float,
        default=4.0,
        help="Benchmark drawdown percentage that marks neutral",
    )
    subparser.add_argument(
        "--risk-off-benchmark-drawdown-pct",
        type=float,
        default=8.0,
        help="Benchmark drawdown percentage that marks risk-off",
    )
    subparser.add_argument(
        "--neutral-volatility-pct",
        type=float,
        default=2.5,
        help="Benchmark realized volatility percentage that marks neutral",
    )
    subparser.add_argument(
        "--risk-off-volatility-pct",
        type=float,
        default=4.0,
        help="Benchmark realized volatility percentage that marks risk-off",
    )
    subparser.add_argument(
        "--rebalance-interval-bars",
        type=int,
        default=6,
        help="Rebalance cadence in bars for controlled exposure scenarios",
    )
    subparser.add_argument(
        "--starting-cash-usd",
        type=float,
        default=10_000.0,
        help="Synthetic starting USD wallet balance for exposure scenarios",
    )
    subparser.add_argument(
        "--fee-bps",
        type=float,
        default=25.0,
        help="Flat taker fee in basis points applied to simulated scenario trades",
    )
    subparser.add_argument(
        "--target-lookback-bars",
        type=int,
        default=63,
        help=(
            "Bars used by dynamic target scenarios such as trend_proxy "
            "and trend_rank_proxy"
        ),
    )
    subparser.add_argument(
        "--min-momentum-bps",
        type=float,
        default=150.0,
        help="Minimum momentum required for trend_proxy eligibility",
    )
    subparser.add_argument(
        "--max-target-pairs",
        type=int,
        default=4,
        help="Maximum number of dynamic target-scenario pairs to target",
    )
    subparser.add_argument(
        "--strict-data",
        action="store_true",
        help="Fail if any requested pair/timeframe is missing or partially covered",
    )
    subparser.add_argument(
        "--json",
        action="store_true",
        help="Print the sweep aggregate as JSON",
    )


def _add_strategy_activity_sweep_arguments(
    subparser: argparse.ArgumentParser,
) -> None:
    subparser.add_argument(
        "--config",
        help="Optional path to the base config.yaml to use",
    )
    subparser.add_argument(
        "--pair",
        action="append",
        help="Limit the replay universe to one pair; repeat to include multiple pairs",
    )
    subparser.add_argument(
        "--window-set",
        action="append",
        choices=sorted(STRATEGY_ACTIVITY_WINDOW_SETS),
        help="Activity window set to run; repeat to include multiple sets",
    )
    subparser.add_argument(
        "--group",
        action="append",
        choices=sorted(DEFAULT_STRATEGY_ACTIVITY_GROUP_IDS),
        help=(
            "Strategy group to run; repeat to include multiple groups "
            f"(defaults to {', '.join(DEFAULT_STRATEGY_ACTIVITY_GROUP_IDS)})"
        ),
    )
    subparser.add_argument(
        "--strategy",
        action="append",
        help="Optional custom strategy id; repeat to create one custom group",
    )
    subparser.add_argument(
        "--starting-cash-usd",
        type=float,
        default=10_000.0,
        help="Synthetic starting USD wallet balance for each replay",
    )
    subparser.add_argument(
        "--fee-bps",
        type=float,
        default=25.0,
        help="Flat taker fee in basis points applied to simulated fills",
    )
    subparser.add_argument(
        "--warmup-days",
        type=float,
        default=None,
        help=(
            "Days of cached OHLC before each window start to expose for replay warmup; "
            "defaults to the configured strategy/risk lookback requirement"
        ),
    )
    subparser.add_argument(
        "--save-dir",
        required=True,
        help="Directory where per-run reports and aggregate.json are written",
    )
    subparser.add_argument(
        "--strict-data",
        action="store_true",
        help="Fail if any requested pair/timeframe is missing or partially covered",
    )
    subparser.add_argument(
        "--json",
        action="store_true",
        help="Print the strategy activity aggregate as JSON",
    )


def _print_market_regime_exposure_sweep_summary(payload: dict[str, Any]) -> None:
    summary = payload["summary"]
    print("Market regime exposure sweep completed.")
    print(
        f"Window sets: {', '.join(summary['window_sets'])} | "
        f"Reports: {summary['report_count']} | "
        f"Aggregate groups: {len(summary['groups'])}"
    )
    for group in summary["groups"]:
        print(
            f"- {group['window_set']} {group['scenario_id']} "
            f"{group['overlay_mode']} alloc {float(group['allocation_pct']):g}%: "
            f"avg return {float(group['avg_delta_return_pct']):+.4f}, "
            f"positive {group['positive_return_windows']}/{group['window_count']}, "
            f"drawdown {group['drawdown_improved_windows']}/{group['window_count']}, "
            f"passed={group['promotion_gate']['passed']}"
        )
    print(f"Saved aggregate: {payload['summary']['aggregate_path']}")


def _print_strategy_activity_sweep_summary(payload: dict[str, Any]) -> None:
    summary = payload["summary"]
    print("Strategy activity sweep completed.")
    print(
        f"Window sets: {', '.join(summary['window_sets'])} | "
        f"Runs: {summary['run_count']} | Groups: {summary['group_count']}"
    )
    for group in summary["groups"]:
        candidate = "yes" if group["gate2_candidate"] else "no"
        print(
            f"- {group['group_id']} ({', '.join(group['strategies'])}): "
            f"ready {group['ready_windows']}/{group['window_count']}, "
            f"actions {group['action_windows']}/{group['window_count']}, "
            f"fills {group['fill_windows']}/{group['window_count']}, "
            f"gate2_candidate={candidate}"
        )
        if group.get("stage_counts"):
            stages = ", ".join(
                f"{stage} {count}" for stage, count in group["stage_counts"].items()
            )
            print(f"  stages: {stages}")
    if summary.get("best_gate2_candidate_group"):
        print(f"Best Gate 2 candidate: {summary['best_gate2_candidate_group']}")
    else:
        print("Best Gate 2 candidate: none")
    print(f"Saved aggregate: {summary['aggregate_path']}")


def _print_market_regime_overlay_backtest_summary(
    payload: dict[str, Any], report_path: str | None
) -> None:
    summary = payload["summary"]
    baseline = summary["baseline"]
    overlay = summary["overlay"]
    delta = summary["delta"]
    interventions = summary["overlay_interventions"]
    print("Market regime overlay backtest completed.")
    print(f"Window: {summary['start']} -> {summary['end']}")
    print(
        "Return: "
        f"baseline {float(baseline['return_pct']):+.2f}% -> "
        f"overlay {float(overlay['return_pct']):+.2f}% "
        f"({float(delta['return_pct']):+.2f} pct pts)"
    )
    print(
        "Max drawdown: "
        f"baseline {float(baseline['max_drawdown_pct']):.2f}% -> "
        f"overlay {float(overlay['max_drawdown_pct']):.2f}% "
        f"({float(delta['max_drawdown_pct']):+.2f} pct pts)"
    )
    print(
        f"Overlay interventions: {interventions['overlay_interventions']} "
        f"({interventions['overlay_blocked_actions']} blocked, "
        f"{interventions['overlay_clamped_actions']} clamped)"
    )
    if interventions.get("state_counts"):
        states = ", ".join(
            f"{state} {count}" for state, count in interventions["state_counts"].items()
        )
        print(f"Regime cycles: {states}")
    if interventions.get("reason_counts"):
        print("Top overlay reasons:")
        for reason, count in list(interventions["reason_counts"].items())[:5]:
            print(f"- {reason}: {count}")
    if report_path:
        print(f"Saved report: {report_path}")


def _print_market_regime_throttle_backtest_summary(
    payload: dict[str, Any], report_path: str | None
) -> None:
    summary = payload["summary"]
    baseline = summary["baseline"]
    throttle = summary["throttle"]
    delta = summary["delta"]
    interventions = summary["throttle_interventions"]
    checks = summary.get("promotion_checks") or {}
    print("Market regime throttle backtest completed.")
    print(f"Window: {summary['start']} -> {summary['end']}")
    print(
        "Return: "
        f"baseline {float(baseline['return_pct']):+.2f}% -> "
        f"throttle {float(throttle['return_pct']):+.2f}% "
        f"({float(delta['return_pct']):+.2f} pct pts)"
    )
    print(
        "Max drawdown: "
        f"baseline {float(baseline['max_drawdown_pct']):.2f}% -> "
        f"throttle {float(throttle['max_drawdown_pct']):.2f}% "
        f"({float(delta['max_drawdown_pct']):+.2f} pct pts)"
    )
    print(
        "Actions: "
        f"baseline {int(baseline['total_actions'])} -> "
        f"throttle {int(throttle['total_actions'])}; "
        f"fills {int(baseline['filled_orders'])} -> "
        f"{int(throttle['filled_orders'])}"
    )
    print(
        "Runtime throttle interventions: "
        f"{interventions['throttled_actions']} throttled "
        f"({interventions['blocked_actions']} blocked, "
        f"{interventions['clamped_actions']} clamped) across "
        f"{interventions['intervention_cycles']} cycles"
    )
    if interventions.get("state_counts"):
        states = ", ".join(
            f"{state} {count}" for state, count in interventions["state_counts"].items()
        )
        print(f"Regime cycles: {states}")
    if interventions.get("reason_counts"):
        print("Top throttle reasons:")
        for reason, count in list(interventions["reason_counts"].items())[:5]:
            print(f"- {reason}: {count}")
    if checks:
        outcome = "passed" if checks.get("passed") else "not passed"
        print(f"Gate 2 checks: {outcome}")
        for name, payload_item in checks.items():
            if name == "passed" or not isinstance(payload_item, dict):
                continue
            if not payload_item.get("passed"):
                print(f"- {name}: not passed")
    if report_path:
        print(f"Saved report: {report_path}")


def _print_ml_walk_forward_summary(
    payload: dict[str, Any], report_path: str | None
) -> None:
    summary = payload["summary"]
    metrics = summary["metrics"]
    print("ML walk-forward completed.")
    print(f"Strategy: {summary['strategy_id']} | Timeframe: {summary['timeframe']}")
    print(
        "Evaluation: "
        f"{summary.get('evaluation_mode', 'unknown')} | "
        "Edge scoring: "
        f"{summary.get('edge_scoring_mode', 'unknown')} | "
        "Model state reused: "
        f"{summary.get('model_state_reused_across_folds', 'unknown')}"
    )
    print(
        f"Window: {summary['start']} -> {summary['end']} "
        f"({summary['fold_count']} folds)"
    )
    print(
        f"Train/test bars: {summary['train_bars']}/{summary['test_bars']} | "
        f"Pairs: {', '.join(summary['pairs'])}"
    )
    print(f"Predictions scored: {metrics['prediction_count']}")

    def _pct(value: Any) -> str:
        return "n/a" if value is None else f"{float(value) * 100.0:.2f}%"

    print(f"Directional accuracy: {_pct(metrics.get('directional_accuracy'))}")
    print(
        "Edge prediction accuracy: " f"{_pct(metrics.get('edge_prediction_accuracy'))}"
    )
    print(f"Long precision: {_pct(metrics.get('precision_long'))}")
    print(f"Cost hurdle: {summary['round_trip_cost_bps']:.2f} bps estimated round trip")
    current_tier = summary.get("promotion_tier", "unknown")
    print(
        f"Promotion tier: {current_tier} "
        + ("(operational)" if summary.get("promotable") else "(blocked)")
    )
    for reason in summary["promotable_reasons"]:
        print(f"- {reason}")

    # For operational tiers, surface the next-tier blockers explicitly so the
    # plain-bullet failure reasons aren't misread as current-tier problems.
    next_tier_map = {
        "research_promising": "risk_overlay_candidate",
        "risk_overlay_candidate": "self_standing",
    }
    next_tier = next_tier_map.get(current_tier)
    promotion_tiers = summary.get("promotion_tiers") or {}
    if (
        summary.get("promotable")
        and next_tier is not None
        and isinstance(promotion_tiers, dict)
    ):
        next_tier_payload = promotion_tiers.get(next_tier) or {}
        next_reasons = (
            next_tier_payload.get("reasons")
            if isinstance(next_tier_payload, dict)
            else None
        )
        if next_reasons:
            print(f"Next tier blockers ({next_tier}):")
            for reason in next_reasons:
                print(f"- {reason}")
    if report_path:
        print(f"Saved report: {report_path}")


def _write_backtest_report(payload: dict[str, Any], report_path: str) -> str:
    return str(write_backtest_report(payload, report_path))


def _write_ml_walk_forward_report(payload: dict[str, Any], report_path: str) -> str:
    return str(write_ml_walk_forward_report(payload, report_path))


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
    print(
        f"Warmup status: {preflight.warmup_status} "
        f"({preflight.warmup_days:g} days before replay start)"
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
    if preflight.warmup_coverage:
        print("Warmup coverage:")
        for item in preflight.warmup_coverage:
            first_bar = item.first_bar_at.isoformat() if item.first_bar_at else "none"
            last_bar = item.last_bar_at.isoformat() if item.last_bar_at else "none"
            print(
                f"- {item.series_key}: {item.status}, {item.bar_count} bars, "
                f"first {first_bar}, last {last_bar}"
            )


def _format_delta(
    label: str, baseline: float, candidate: float, suffix: str = ""
) -> str:
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
            "Clamped actions",
            float(baseline_summary.get("clamped_actions", 0.0)),
            float(candidate_summary.get("clamped_actions", 0.0)),
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


def _ml_report_compare_command(args: argparse.Namespace) -> int:
    """Compare saved ML walk-forward report artifacts."""

    comparison = compare_ml_reports(
        args.reports,
        glob_pattern=args.glob_pattern,
        sort_by=args.sort,
    )
    rendered = render_ml_report_comparison(comparison, output_format=args.format)

    if args.output:
        output_path = Path(args.output).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered + "\n", encoding="utf-8")
        print(f"ML report comparison written: {output_path}")
    else:
        print(rendered)

    for warning in comparison.warnings:
        print(f"Warning: {warning}", file=sys.stderr)
    return 0


def _ml_feature_ablation_summary_command(args: argparse.Namespace) -> int:
    """Summarize feature-level ablation candidates from ML reports."""

    summary = summarize_ml_feature_ablation(
        args.reports,
        glob_pattern=args.glob_pattern,
        sort_by=args.sort,
    )
    rendered = render_ml_feature_ablation_summary(
        summary,
        output_format=args.format,
    )

    if args.output:
        output_path = Path(args.output).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered + "\n", encoding="utf-8")
        print(f"ML feature ablation summary written: {output_path}")
    else:
        print(rendered)

    for warning in summary.warnings:
        print(f"Warning: {warning}", file=sys.stderr)
    return 0


def _backtest_preflight_command(args: argparse.Namespace) -> int:
    try:
        start = _parse_datetime_arg(args.start)
        end = _parse_datetime_arg(args.end)
    except ValueError as exc:
        return _print_error(f"Invalid backtest datetime: {exc}")

    try:
        config = _load_backtest_config(args)
        config_provenance = _backtest_config_provenance(args)
        result = build_backtest_preflight(
            config,
            start=start,
            end=end,
            timeframes=args.timeframe,
            warmup_days=args.warmup_days,
            **config_provenance,
        )
    except Exception as exc:  # noqa: BLE001
        return _print_error(f"Backtest preflight failed: {exc}")

    strict_details = backtest_strict_data_details(result.preflight)
    if args.strict_data and strict_details:
        return _print_error(
            "Backtest preflight failed in strict mode: "
            + "; ".join(strict_details)
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
        config_provenance = _backtest_config_provenance(args)
        result = run_backtest(
            config,
            start=start,
            end=end,
            timeframes=args.timeframe,
            starting_cash_usd=float(args.starting_cash_usd),
            fee_bps=float(args.fee_bps),
            db_path=args.db_path,
            strict_data=bool(args.strict_data),
            warmup_days=args.warmup_days,
            **config_provenance,
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
        preflight_payload = payload.get("preflight") or {}
        preflight_status = (
            preflight_payload.get("status")
            if isinstance(preflight_payload, dict)
            else None
        )
        if preflight_status != "ready" and not args.allow_non_ready_publish:
            status_text = str(preflight_status or "unknown")
            return _print_error(
                "Backtest latest-report publish refused: preflight status is "
                f"{status_text!r}. Repair replay coverage or pass "
                "--allow-non-ready-publish to publish intentionally."
            )
        warmup_status = (
            preflight_payload.get("warmup_status")
            if isinstance(preflight_payload, dict)
            else None
        )
        if warmup_status not in {"ready", "disabled"} and not args.allow_non_ready_publish:
            status_text = str(warmup_status or "unknown")
            return _print_error(
                "Backtest latest-report publish refused: warmup status is "
                f"{status_text!r}. Repair replay warmup coverage or pass "
                "--allow-non-ready-publish to publish intentionally."
            )
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


def _rs_rotation_v2_research_command(args: argparse.Namespace) -> int:
    """Run the replay-only relative-strength v2 research probe."""

    try:
        start = _parse_datetime_arg(args.start)
        end = _parse_datetime_arg(args.end)
    except ValueError as exc:
        return _print_error(f"Invalid research datetime: {exc}")

    try:
        config = _load_backtest_config(args)
        timeframe = args.timeframe or default_rs_rotation_v2_timeframe(config)
        lookback_bars = (
            int(args.lookback_bars)
            if args.lookback_bars is not None
            else default_rs_rotation_v2_lookback_bars(config)
        )
        top_n = (
            int(args.top_n)
            if args.top_n is not None
            else default_rs_rotation_v2_top_n(config)
        )
        total_allocation_pct = (
            float(args.total_allocation_pct)
            if args.total_allocation_pct is not None
            else default_rs_rotation_v2_allocation_pct(config)
        )
        params = RSRotationV2ResearchParams(
            timeframe=timeframe,
            lookback_bars=lookback_bars,
            volatility_lookback_bars=int(args.volatility_lookback_bars),
            rebalance_interval_bars=int(args.rebalance_interval_bars),
            forward_horizon_bars=int(args.forward_horizon_bars),
            top_n=top_n,
            total_allocation_pct=total_allocation_pct,
            starting_cash_usd=float(args.starting_cash_usd),
            fee_bps=float(args.fee_bps),
            slippage_bps=(
                float(args.slippage_bps)
                if args.slippage_bps is not None
                else float(config.execution.max_slippage_bps)
            ),
            edge_buffer_bps=float(args.edge_buffer_bps),
            min_abs_momentum_bps=float(args.min_abs_momentum_bps),
            min_score_gap=float(args.min_score_gap),
            require_btc_regime=bool(args.require_btc_regime),
            require_basket_regime=bool(args.require_basket_regime),
            benchmark_pair=args.benchmark_pair,
            min_trade_usd=float(args.min_trade_usd),
            min_active_cycles=int(args.min_active_cycles),
            max_drawdown_pct=float(args.max_drawdown_pct),
        )
        result = run_rs_rotation_v2_research(
            config,
            start=start,
            end=end,
            pairs=args.pair,
            params=params,
            strict_data=bool(args.strict_data),
        )
    except Exception as exc:  # noqa: BLE001
        return _print_error(f"RS rotation v2 research failed: {exc}")

    payload = result.to_report_dict()
    payload["summary"]["replay_inputs"]["config_path"] = (
        str(Path(args.config).expanduser().resolve()) if args.config else None
    )
    payload["provenance"] = {
        "app_version": APP_VERSION,
        "config_path": (
            str(Path(args.config).expanduser().resolve()) if args.config else None
        ),
        "generated_by": "krakked rs-rotation-v2-research",
    }

    saved_report_path: str | None = None
    if args.save_report:
        try:
            saved_report_path = _write_backtest_report(payload, args.save_report)
        except Exception as exc:  # noqa: BLE001
            return _print_error(f"RS rotation v2 report write failed: {exc}")

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        _print_rs_rotation_v2_research_summary(payload, saved_report_path)
    return 0


def _market_regime_research_command(args: argparse.Namespace) -> int:
    """Label cached replay cycles with market regime overlay states."""

    try:
        start = _parse_datetime_arg(args.start)
        end = _parse_datetime_arg(args.end)
    except ValueError as exc:
        return _print_error(f"Invalid market regime datetime: {exc}")

    try:
        config = _load_backtest_config(args)
        params = _market_regime_params_from_args(args)
        result = run_market_regime_research(
            config,
            start=start,
            end=end,
            pairs=args.pair,
            params=params,
            strict_data=bool(args.strict_data),
        )
    except Exception as exc:  # noqa: BLE001
        return _print_error(f"Market regime research failed: {exc}")

    payload = result.to_report_dict()
    payload["summary"]["config_path"] = (
        str(Path(args.config).expanduser().resolve()) if args.config else None
    )
    payload["provenance"] = {
        "app_version": APP_VERSION,
        "config_path": (
            str(Path(args.config).expanduser().resolve()) if args.config else None
        ),
        "generated_by": "krakked market-regime-research",
    }

    saved_report_path: str | None = None
    if args.save_report:
        try:
            saved_report_path = _write_backtest_report(payload, args.save_report)
        except Exception as exc:  # noqa: BLE001
            return _print_error(f"Market regime report write failed: {exc}")

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        _print_market_regime_research_summary(payload, saved_report_path)
    return 0


def _market_regime_overlay_backtest_command(args: argparse.Namespace) -> int:
    """Compare normal replay against market-regime overlay replay."""

    try:
        start = _parse_datetime_arg(args.start)
        end = _parse_datetime_arg(args.end)
    except ValueError as exc:
        return _print_error(f"Invalid overlay backtest datetime: {exc}")

    try:
        config = _load_backtest_config(args)
        params = _market_regime_params_from_args(args)
        result = run_market_regime_overlay_backtest(
            config,
            start=start,
            end=end,
            pairs=args.pair,
            params=params,
            timeframes=args.replay_timeframe,
            starting_cash_usd=float(args.starting_cash_usd),
            fee_bps=float(args.fee_bps),
            strict_data=bool(args.strict_data),
        )
    except Exception as exc:  # noqa: BLE001
        return _print_error(f"Market regime overlay backtest failed: {exc}")

    payload = result.to_report_dict()
    payload["summary"]["config_path"] = (
        str(Path(args.config).expanduser().resolve()) if args.config else None
    )
    payload["provenance"] = {
        "app_version": APP_VERSION,
        "config_path": (
            str(Path(args.config).expanduser().resolve()) if args.config else None
        ),
        "generated_by": "krakked market-regime-overlay-backtest",
    }

    saved_report_path: str | None = None
    if args.save_report:
        try:
            saved_report_path = _write_backtest_report(payload, args.save_report)
        except Exception as exc:  # noqa: BLE001
            return _print_error(f"Market regime overlay report write failed: {exc}")

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        _print_market_regime_overlay_backtest_summary(payload, saved_report_path)
    return 0


def _market_regime_throttle_backtest_command(args: argparse.Namespace) -> int:
    """Compare normal replay against the real runtime market-regime throttle."""

    try:
        start = _parse_datetime_arg(args.start)
        end = _parse_datetime_arg(args.end)
    except ValueError as exc:
        return _print_error(f"Invalid runtime throttle backtest datetime: {exc}")

    try:
        config = _load_backtest_config(args)
        if args.strategy:
            config = apply_strategy_activity_override(config, args.strategy)
        params = _market_regime_params_from_args(args)
        result = run_market_regime_throttle_backtest(
            config,
            start=start,
            end=end,
            pairs=args.pair,
            params=params,
            timeframes=args.replay_timeframe,
            starting_cash_usd=float(args.starting_cash_usd),
            fee_bps=float(args.fee_bps),
            strict_data=bool(args.strict_data),
            warmup_days=args.warmup_days,
            unavailable_policy=args.unavailable_policy,
        )
    except Exception as exc:  # noqa: BLE001
        return _print_error(f"Market regime throttle backtest failed: {exc}")

    payload = result.to_report_dict()
    payload["summary"]["config_path"] = (
        str(Path(args.config).expanduser().resolve()) if args.config else None
    )
    payload["summary"]["strategy_override"] = list(args.strategy or [])
    payload["provenance"] = {
        "app_version": APP_VERSION,
        "config_path": (
            str(Path(args.config).expanduser().resolve()) if args.config else None
        ),
        "generated_by": "krakked market-regime-throttle-backtest",
    }

    saved_report_path: str | None = None
    if args.save_report:
        try:
            saved_report_path = _write_backtest_report(payload, args.save_report)
        except Exception as exc:  # noqa: BLE001
            return _print_error(f"Market regime throttle report write failed: {exc}")

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        _print_market_regime_throttle_backtest_summary(
            payload,
            saved_report_path,
        )
    return 0


def _market_regime_exposure_payload(
    result: Any,
    args: argparse.Namespace,
    *,
    generated_by: str,
) -> dict[str, Any]:
    payload = result.to_report_dict()
    payload["summary"]["config_path"] = (
        str(Path(args.config).expanduser().resolve()) if args.config else None
    )
    payload["provenance"] = {
        "app_version": APP_VERSION,
        "config_path": (
            str(Path(args.config).expanduser().resolve()) if args.config else None
        ),
        "generated_by": generated_by,
    }
    return payload


def _market_regime_exposure_research_command(args: argparse.Namespace) -> int:
    """Run controlled exposure scenarios through market-regime overlay modes."""

    try:
        start = _parse_datetime_arg(args.start)
        end = _parse_datetime_arg(args.end)
    except ValueError as exc:
        return _print_error(f"Invalid exposure research datetime: {exc}")

    try:
        config = _load_backtest_config(args)
        regime_params = _market_regime_params_from_args(args)
        scenario_params = _market_regime_exposure_scenario_params_from_args(args)
        result = run_market_regime_exposure_research(
            config,
            start=start,
            end=end,
            pairs=args.pair,
            regime_params=regime_params,
            scenario_params=scenario_params,
            scenarios=args.scenario,
            overlay_modes=args.overlay_mode,
            strict_data=bool(args.strict_data),
        )
    except Exception as exc:  # noqa: BLE001
        return _print_error(f"Market regime exposure research failed: {exc}")

    payload = _market_regime_exposure_payload(
        result,
        args,
        generated_by="krakked market-regime-exposure-research",
    )

    saved_report_path: str | None = None
    if args.save_report:
        try:
            saved_report_path = _write_backtest_report(payload, args.save_report)
        except Exception as exc:  # noqa: BLE001
            return _print_error(f"Market regime exposure report write failed: {exc}")

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        _print_market_regime_exposure_research_summary(payload, saved_report_path)
    return 0


def _market_regime_exposure_sweep_command(args: argparse.Namespace) -> int:
    """Run market-regime exposure research across configured window sets."""

    try:
        config = _load_backtest_config(args)
        regime_params = _market_regime_params_from_args(args)
        allocations = [float(value) for value in (args.allocation_pct or [20.0])]
        save_dir = Path(args.save_dir).expanduser().resolve()
        save_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:  # noqa: BLE001
        return _print_error(f"Market regime exposure sweep setup failed: {exc}")

    reports: list[dict[str, Any]] = []
    report_paths: list[str] = []
    try:
        for window_set in _unique_strings(args.window_set):
            for allocation_pct in allocations:
                scenario_params = MarketRegimeExposureScenarioParams(
                    allocation_pct=allocation_pct,
                    rebalance_interval_bars=int(args.rebalance_interval_bars),
                    starting_cash_usd=float(args.starting_cash_usd),
                    fee_bps=float(args.fee_bps),
                    target_lookback_bars=int(args.target_lookback_bars),
                    min_momentum_bps=float(args.min_momentum_bps),
                    max_target_pairs=int(args.max_target_pairs),
                )
                for (
                    window_id,
                    start_text,
                    end_text,
                ) in MARKET_REGIME_EXPOSURE_WINDOW_SETS[window_set]:
                    result = run_market_regime_exposure_research(
                        config,
                        start=_parse_datetime_arg(start_text),
                        end=_parse_datetime_arg(end_text),
                        pairs=args.pair,
                        regime_params=regime_params,
                        scenario_params=scenario_params,
                        scenarios=args.scenario or ["trend_proxy"],
                        overlay_modes=args.overlay_mode or ["target_scale"],
                        strict_data=bool(args.strict_data),
                    )
                    payload = _market_regime_exposure_payload(
                        result,
                        args,
                        generated_by="krakked market-regime-exposure-sweep",
                    )
                    payload["summary"]["window_set"] = window_set
                    payload["summary"]["window_id"] = window_id
                    payload["summary"]["allocation_pct"] = allocation_pct
                    report_path = (
                        save_dir
                        / window_set
                        / f"allocation-{allocation_pct:g}"
                        / f"{window_id}.json"
                    )
                    saved = _write_backtest_report(payload, str(report_path))
                    reports.append(payload)
                    report_paths.append(saved)
    except Exception as exc:  # noqa: BLE001
        return _print_error(f"Market regime exposure sweep failed: {exc}")

    aggregate = _market_regime_exposure_sweep_aggregate(
        reports,
        report_paths=report_paths,
        save_dir=save_dir,
    )
    aggregate_path = save_dir / "aggregate.json"
    try:
        saved_aggregate_path = _write_backtest_report(aggregate, str(aggregate_path))
    except Exception as exc:  # noqa: BLE001
        return _print_error(f"Market regime exposure aggregate write failed: {exc}")
    aggregate["summary"]["aggregate_path"] = saved_aggregate_path
    _write_backtest_report(aggregate, str(aggregate_path))

    if args.json:
        print(json.dumps(aggregate, indent=2))
    else:
        _print_market_regime_exposure_sweep_summary(aggregate)
    return 0


def _strategy_activity_sweep_command(args: argparse.Namespace) -> int:
    """Run cache-only strategy activity diagnostics across replay windows."""

    try:
        config = _load_backtest_config(args)
        groups = build_strategy_activity_groups(
            config,
            group_ids=args.group,
            custom_strategies=args.strategy,
        )
        if not groups:
            return _print_error("Strategy activity sweep has no groups to run.")
        selected_window_sets = _unique_strings(args.window_set or ["recent_20d"])
        window_sets = {
            window_set: STRATEGY_ACTIVITY_WINDOW_SETS[window_set]
            for window_set in selected_window_sets
        }
        save_dir = Path(args.save_dir).expanduser().resolve()
        save_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:  # noqa: BLE001
        return _print_error(f"Strategy activity sweep setup failed: {exc}")

    try:
        result = run_strategy_activity_sweep(
            config,
            window_sets=window_sets,
            groups=groups,
            starting_cash_usd=float(args.starting_cash_usd),
            fee_bps=float(args.fee_bps),
            strict_data=bool(args.strict_data),
            warmup_days=args.warmup_days,
        )
    except Exception as exc:  # noqa: BLE001
        return _print_error(f"Strategy activity sweep failed: {exc}")

    payload = result.to_report_dict()
    payload["summary"]["config_path"] = (
        str(Path(args.config).expanduser().resolve()) if args.config else None
    )
    payload["summary"]["save_dir"] = str(save_dir)
    payload["provenance"] = {
        "app_version": APP_VERSION,
        "config_path": (
            str(Path(args.config).expanduser().resolve()) if args.config else None
        ),
        "generated_by": "krakked strategy-activity-sweep",
    }

    report_paths: list[str] = []
    try:
        for run in payload["runs"]:
            report_path = (
                save_dir
                / str(run["window_set"])
                / str(run["group_id"])
                / f"{run['window_id']}.json"
            )
            saved_path = _write_backtest_report(
                {
                    "report_version": payload["report_version"],
                    "report_type": "strategy_activity_run",
                    "generated_at": payload["generated_at"],
                    "summary": run,
                    "provenance": payload["provenance"],
                },
                str(report_path),
            )
            run["report_path"] = saved_path
            report_paths.append(saved_path)

        aggregate_path = save_dir / "aggregate.json"
        payload["summary"]["aggregate_path"] = str(aggregate_path)
        payload["summary"]["report_paths"] = report_paths
        saved_aggregate_path = _write_backtest_report(payload, str(aggregate_path))
        payload["summary"]["aggregate_path"] = saved_aggregate_path
        _write_backtest_report(payload, str(aggregate_path))
    except Exception as exc:  # noqa: BLE001
        return _print_error(f"Strategy activity sweep report write failed: {exc}")

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        _print_strategy_activity_sweep_summary(payload)
    return 0


def _unique_strings(values: list[str]) -> list[str]:
    unique: list[str] = []
    for value in values:
        if value not in unique:
            unique.append(value)
    return unique


def _market_regime_exposure_sweep_aggregate(
    reports: list[dict[str, Any]],
    *,
    report_paths: list[str],
    save_dir: Path,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for report, report_path in zip(reports, report_paths):
        summary = report["summary"]
        preflight = report.get("preflight") or {}
        strict_data_ready = not (
            preflight.get("missing_series") or preflight.get("partial_series")
        )
        for comparison in report.get("comparisons", []):
            baseline = comparison["baseline"]
            overlay = comparison["overlay"]
            interventions = comparison["overlay_interventions"]
            baseline_exposure = float(baseline.get("avg_exposure_pct", 0.0) or 0.0)
            overlay_exposure = float(overlay.get("avg_exposure_pct", 0.0) or 0.0)
            rows.append(
                {
                    "window_set": summary["window_set"],
                    "window_id": summary["window_id"],
                    "allocation_pct": float(summary["allocation_pct"]),
                    "scenario_id": comparison["scenario_id"],
                    "overlay_mode": comparison["overlay_mode"],
                    "report_path": report_path,
                    "delta_return_pct": float(comparison["delta"]["return_pct"]),
                    "delta_max_drawdown_pct": float(
                        comparison["delta"]["max_drawdown_pct"]
                    ),
                    "baseline_avg_exposure_pct": baseline_exposure,
                    "overlay_avg_exposure_pct": overlay_exposure,
                    "overlay_exposure_ratio": (
                        overlay_exposure / baseline_exposure
                        if baseline_exposure > 0.0
                        else 0.0
                    ),
                    "overlay_active_cycle_pct": float(
                        overlay.get("active_cycle_pct", 0.0) or 0.0
                    ),
                    "overlay_interventions": int(
                        interventions.get("overlay_interventions", 0) or 0
                    ),
                    "overlay_target_reductions": int(
                        interventions.get("overlay_target_reductions", 0) or 0
                    ),
                    "reasons_present": bool(summary.get("reason_counts"))
                    or int(interventions.get("overlay_target_reductions", 0) or 0) == 0,
                    "strict_data_ready": strict_data_ready,
                }
            )

    groups: list[dict[str, Any]] = []
    group_keys = sorted(
        {
            (
                row["window_set"],
                row["allocation_pct"],
                row["scenario_id"],
                row["overlay_mode"],
            )
            for row in rows
        }
    )
    for window_set, allocation_pct, scenario_id, overlay_mode in group_keys:
        items = [
            row
            for row in rows
            if row["window_set"] == window_set
            and row["allocation_pct"] == allocation_pct
            and row["scenario_id"] == scenario_id
            and row["overlay_mode"] == overlay_mode
        ]
        window_count = len(items)
        required_windows = 3 if window_count <= 5 else 4
        positive_windows = sum(1 for row in items if row["delta_return_pct"] > 0.0)
        drawdown_windows = sum(
            1 for row in items if row["delta_max_drawdown_pct"] < 0.0
        )
        avg_return_delta = (
            sum(row["delta_return_pct"] for row in items) / window_count
            if window_count
            else 0.0
        )
        min_active = (
            min(row["overlay_active_cycle_pct"] for row in items) if items else 0.0
        )
        min_exposure_ratio = (
            min(row["overlay_exposure_ratio"] for row in items) if items else 0.0
        )
        gate = {
            "average_return_delta_positive": avg_return_delta > 0.0,
            "positive_return_windows": positive_windows >= required_windows,
            "drawdown_improved_windows": drawdown_windows >= required_windows,
            "overlay_active_cycles": min_active >= 50.0,
            "overlay_exposure_ratio": min_exposure_ratio >= 0.35,
            "strict_data_ready": all(row["strict_data_ready"] for row in items),
            "reasons_present": all(row["reasons_present"] for row in items),
        }
        gate["passed"] = all(gate.values())
        groups.append(
            {
                "window_set": window_set,
                "allocation_pct": allocation_pct,
                "scenario_id": scenario_id,
                "overlay_mode": overlay_mode,
                "window_count": window_count,
                "required_positive_windows": required_windows,
                "avg_delta_return_pct": avg_return_delta,
                "positive_return_windows": positive_windows,
                "avg_delta_max_drawdown_pct": (
                    sum(row["delta_max_drawdown_pct"] for row in items) / window_count
                    if window_count
                    else 0.0
                ),
                "drawdown_improved_windows": drawdown_windows,
                "min_overlay_active_cycle_pct": min_active,
                "min_overlay_exposure_ratio": min_exposure_ratio,
                "total_overlay_interventions": sum(
                    row["overlay_interventions"] for row in items
                ),
                "promotion_gate": gate,
            }
        )

    return {
        "report_version": 1,
        "report_type": "market_regime_exposure_sweep",
        "generated_at": datetime.now(UTC).isoformat(),
        "summary": {
            "research_only": True,
            "runtime_wiring_approved": False,
            "save_dir": str(save_dir),
            "aggregate_path": str(save_dir / "aggregate.json"),
            "window_sets": sorted({row["window_set"] for row in rows}),
            "report_count": len(reports),
            "rows": rows,
            "groups": groups,
        },
    }


def _ml_walk_forward_command(args: argparse.Namespace) -> int:
    """Run a rolling train/test evaluation for one ML strategy."""

    try:
        start = _parse_datetime_arg(args.start)
        end = _parse_datetime_arg(args.end)
    except ValueError as exc:
        return _print_error(f"Invalid walk-forward datetime: {exc}")

    try:
        config = _load_backtest_config(args)
        if args.feature_profile:
            strat_cfg = config.strategies.configs.get(args.strategy)
            if strat_cfg is None:
                return _print_error(f"Unknown strategy: {args.strategy}")
            params = dict(strat_cfg.params or {})
            params["feature_profile"] = args.feature_profile
            strat_cfg.params = params
        result = run_ml_walk_forward(
            config,
            start=start,
            end=end,
            strategy_id=args.strategy,
            timeframe=args.timeframe,
            train_bars=int(args.train_bars),
            test_bars=int(args.test_bars),
            fee_bps=float(args.fee_bps),
            slippage_bps=(
                float(args.slippage_bps) if args.slippage_bps is not None else None
            ),
            db_path=args.db_path,
            strict_data=bool(args.strict_data),
        )
    except Exception as exc:  # noqa: BLE001
        return _print_error(f"ML walk-forward failed: {exc}")

    payload = result.to_report_dict()
    payload["summary"]["config_path"] = (
        str(Path(args.config).expanduser().resolve()) if args.config else None
    )
    if args.db_path:
        payload["sqlite_output"] = str(Path(args.db_path).expanduser().resolve())

    saved_report_path: str | None = None
    if args.save_report:
        try:
            saved_report_path = _write_ml_walk_forward_report(payload, args.save_report)
        except Exception as exc:  # noqa: BLE001
            return _print_error(f"ML walk-forward report write failed: {exc}")

    published_report_path: str | None = None
    if args.publish_latest:
        try:
            published_report_path = str(
                publish_latest_ml_walk_forward_report(
                    payload, config_dir=get_config_dir()
                )
            )
        except Exception as exc:  # noqa: BLE001
            return _print_error(f"ML walk-forward latest-report publish failed: {exc}")

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        _print_ml_walk_forward_summary(
            payload, saved_report_path or published_report_path
        )
        if saved_report_path and published_report_path:
            print(f"Published latest ML walk-forward report: {published_report_path}")

    return 0


def _ml_prune_stale_command(args: argparse.Namespace) -> int:
    """Report or delete stale ML examples, models, and checkpoints."""

    if args.older_than_days is not None and int(args.older_than_days) < 0:
        return _print_error("--older-than-days must be greater than or equal to 0")

    db_path = Path(args.db_path).expanduser().resolve()
    if not db_path.exists():
        return _print_error(f"DB file not found: {db_path}")

    config_path = Path(args.config).expanduser().resolve() if args.config else None
    try:
        config = load_config(config_path=config_path, env="paper")
    except Exception as exc:  # noqa: BLE001
        return _print_error(f"Failed to load config: {exc}")

    store = SQLitePortfolioStore(str(db_path))
    try:
        groups = store.list_ml_artifact_groups()
        candidates = find_stale_ml_artifact_groups(
            config,
            groups,
            strategy_id=args.strategy,
            older_than_days=args.older_than_days,
        )

        deleted: list[dict[str, Any]] = []
        if args.apply:
            for candidate in candidates:
                counts = store.delete_ml_artifact_group(
                    candidate.group.strategy_id,
                    candidate.group.model_key,
                )
                deleted.append(
                    {
                        **candidate.to_dict(),
                        "deleted": counts,
                    }
                )

        payload = {
            "db_path": str(db_path),
            "config_path": str(config_path) if config_path else None,
            "apply": bool(args.apply),
            "strategy": args.strategy,
            "older_than_days": args.older_than_days,
            "stale_count": len(candidates),
            "deleted_count": len(deleted),
            "candidates": [candidate.to_dict() for candidate in candidates],
            "deleted": deleted,
        }
    except Exception as exc:  # noqa: BLE001
        return _print_error(f"ML stale prune failed: {exc}")
    finally:
        store.close()

    if args.json:
        print(json.dumps(payload, indent=2))
        return 0

    action = "deleted" if args.apply else "dry run"
    print(f"ML stale artifact prune {action}.")
    print(f"DB path: {db_path}")
    if not candidates:
        print("No stale ML artifact groups found.")
        return 0

    heading = (
        "Deleted stale ML artifact groups:"
        if args.apply
        else "Stale ML artifact groups:"
    )
    print(heading)
    for item in deleted if args.apply else payload["candidates"]:
        print(
            "- "
            f"{item['strategy_id']} {item['model_key']} "
            f"({item['stale_reason']}; "
            f"examples={item['example_count']}, "
            f"models={item['live_model_count']}, "
            f"checkpoints={item['checkpoint_count']})"
        )
    if not args.apply:
        print("Re-run with --apply to delete these groups.")
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

    refresh_ohlc_parser = subparsers.add_parser(
        "refresh-ohlc",
        help="Refresh local OHLC tails using public Kraken market-data endpoints",
    )
    refresh_ohlc_parser.add_argument(
        "--config",
        help="Optional path to the base config.yaml to use for market-data refresh",
    )
    refresh_ohlc_parser.add_argument(
        "--pair",
        action="append",
        help="Limit refresh to one pair; repeat to include multiple pairs",
    )
    refresh_ohlc_parser.add_argument(
        "--timeframe",
        action="append",
        help="Limit refresh to one timeframe; repeat to include multiple timeframes",
    )
    refresh_ohlc_parser.add_argument(
        "--since",
        help="Override refresh start as an ISO-8601 datetime or epoch seconds",
    )
    refresh_ohlc_parser.add_argument(
        "--json",
        action="store_true",
        help="Print the refresh result payload as JSON",
    )
    refresh_ohlc_parser.set_defaults(func=_refresh_ohlc_command)

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
        "--warmup-days",
        type=float,
        default=None,
        help=(
            "Days of cached OHLC before --start to expose for indicator warmup; "
            "defaults to the configured strategy/risk lookback requirement, use 0 for exact-window replay"
        ),
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
        "--warmup-days",
        type=float,
        default=None,
        help=(
            "Days of cached OHLC before --start to expose for indicator warmup; "
            "defaults to the configured strategy/risk lookback requirement, use 0 for exact-window replay"
        ),
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
        "--allow-non-ready-publish",
        action="store_true",
        help="Allow --publish-latest even when replay preflight status is not ready",
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

    rs_rotation_v2_parser = subparsers.add_parser(
        "rs-rotation-v2-research",
        help="Evaluate a replay-only relative-strength v2 research signal",
    )
    rs_rotation_v2_parser.add_argument(
        "--start",
        required=True,
        help="Research window start time in ISO-8601 form",
    )
    rs_rotation_v2_parser.add_argument(
        "--end",
        required=True,
        help="Research window end time in ISO-8601 form",
    )
    rs_rotation_v2_parser.add_argument(
        "--config",
        help="Optional path to the base config.yaml to use for the research run",
    )
    rs_rotation_v2_parser.add_argument(
        "--pair",
        action="append",
        help="Limit the research run to one pair; repeat to include multiple pairs",
    )
    rs_rotation_v2_parser.add_argument(
        "--timeframe",
        help="Single timeframe to evaluate; defaults to rs_rotation config timeframe",
    )
    rs_rotation_v2_parser.add_argument(
        "--lookback-bars",
        type=int,
        default=None,
        help="Momentum lookback bars; defaults to rs_rotation config lookback",
    )
    rs_rotation_v2_parser.add_argument(
        "--volatility-lookback-bars",
        type=int,
        default=42,
        help="Bars used for volatility-normalized ranking",
    )
    rs_rotation_v2_parser.add_argument(
        "--rebalance-interval-bars",
        type=int,
        default=6,
        help="Bars between simulated rebalances",
    )
    rs_rotation_v2_parser.add_argument(
        "--forward-horizon-bars",
        type=int,
        default=6,
        help="Bars used for the forward-selection diagnostic",
    )
    rs_rotation_v2_parser.add_argument(
        "--top-n",
        type=int,
        default=None,
        help="Maximum selected pairs per rebalance; defaults to rs_rotation config",
    )
    rs_rotation_v2_parser.add_argument(
        "--total-allocation-pct",
        type=float,
        default=None,
        help="Total simulated allocation percentage; defaults to rs_rotation config",
    )
    rs_rotation_v2_parser.add_argument(
        "--starting-cash-usd",
        type=float,
        default=10_000.0,
        help="Synthetic starting USD wallet balance for the research run",
    )
    rs_rotation_v2_parser.add_argument(
        "--fee-bps",
        type=float,
        default=25.0,
        help="Flat taker fee in basis points applied to simulated fills",
    )
    rs_rotation_v2_parser.add_argument(
        "--slippage-bps",
        type=float,
        default=None,
        help="Override one-way slippage bps; defaults to execution.max_slippage_bps",
    )
    rs_rotation_v2_parser.add_argument(
        "--edge-buffer-bps",
        type=float,
        default=50.0,
        help="Extra basis-point edge required above estimated round-trip costs",
    )
    rs_rotation_v2_parser.add_argument(
        "--min-abs-momentum-bps",
        type=float,
        default=0.0,
        help="Minimum absolute trailing momentum bps before a pair can be selected",
    )
    rs_rotation_v2_parser.add_argument(
        "--min-score-gap",
        type=float,
        default=0.25,
        help="Vol-normalized score advantage required to replace an existing holding",
    )
    rs_rotation_v2_parser.add_argument(
        "--no-btc-regime",
        dest="require_btc_regime",
        action="store_false",
        default=True,
        help="Disable the benchmark positive-momentum regime gate",
    )
    rs_rotation_v2_parser.add_argument(
        "--no-basket-regime",
        dest="require_basket_regime",
        action="store_false",
        default=True,
        help="Disable the broad-universe positive-momentum regime gate",
    )
    rs_rotation_v2_parser.add_argument(
        "--benchmark-pair",
        default="BTC/USD",
        help="Benchmark pair used by the BTC regime gate",
    )
    rs_rotation_v2_parser.add_argument(
        "--min-trade-usd",
        type=float,
        default=10.0,
        help="Ignore simulated rebalance deltas below this notional",
    )
    rs_rotation_v2_parser.add_argument(
        "--min-active-cycles",
        type=int,
        default=3,
        help="Minimum active cycles required for the research gate to pass",
    )
    rs_rotation_v2_parser.add_argument(
        "--max-drawdown-pct",
        type=float,
        default=5.0,
        help="Maximum allowed simulated drawdown for the research gate to pass",
    )
    rs_rotation_v2_parser.add_argument(
        "--save-report",
        help="Optional JSON path for a durable v2 research report artifact",
    )
    rs_rotation_v2_parser.add_argument(
        "--strict-data",
        action="store_true",
        help="Fail if any requested pair/timeframe is missing or only partially covered",
    )
    rs_rotation_v2_parser.add_argument(
        "--json",
        action="store_true",
        help="Print the v2 research report as JSON",
    )
    rs_rotation_v2_parser.set_defaults(func=_rs_rotation_v2_research_command)

    market_regime_parser = subparsers.add_parser(
        "market-regime-research",
        help="Label cached OHLC windows with research-only market regime states",
    )
    _add_market_regime_research_arguments(market_regime_parser)
    market_regime_parser.set_defaults(func=_market_regime_research_command)

    market_regime_overlay_parser = subparsers.add_parser(
        "market-regime-overlay-backtest",
        help="Compare normal replay against a research-only market regime overlay",
    )
    _add_market_regime_research_arguments(
        market_regime_overlay_parser,
        include_overlay_backtest_args=True,
    )
    market_regime_overlay_parser.set_defaults(
        func=_market_regime_overlay_backtest_command
    )

    market_regime_throttle_parser = subparsers.add_parser(
        "market-regime-throttle-backtest",
        help="Compare normal replay against the default-disabled runtime throttle",
    )
    _add_market_regime_throttle_backtest_arguments(market_regime_throttle_parser)
    market_regime_throttle_parser.set_defaults(
        func=_market_regime_throttle_backtest_command
    )

    market_regime_exposure_parser = subparsers.add_parser(
        "market-regime-exposure-research",
        help="Run controlled exposure scenarios through market-regime overlays",
    )
    _add_market_regime_research_arguments(
        market_regime_exposure_parser,
        include_exposure_research_args=True,
    )
    market_regime_exposure_parser.set_defaults(
        func=_market_regime_exposure_research_command
    )

    market_regime_exposure_sweep_parser = subparsers.add_parser(
        "market-regime-exposure-sweep",
        help="Run market-regime exposure research across configured window sets",
    )
    _add_market_regime_exposure_sweep_arguments(market_regime_exposure_sweep_parser)
    market_regime_exposure_sweep_parser.set_defaults(
        func=_market_regime_exposure_sweep_command
    )

    strategy_activity_sweep_parser = subparsers.add_parser(
        "strategy-activity-sweep",
        help="Diagnose where strategy replay activity dies across cached windows",
    )
    _add_strategy_activity_sweep_arguments(strategy_activity_sweep_parser)
    strategy_activity_sweep_parser.set_defaults(func=_strategy_activity_sweep_command)

    ml_walk_forward_parser = subparsers.add_parser(
        "ml-walk-forward",
        help="Evaluate one ML strategy with rolling train/test windows",
    )
    ml_walk_forward_parser.add_argument(
        "--start",
        required=True,
        help="Evaluation start time in ISO-8601 form",
    )
    ml_walk_forward_parser.add_argument(
        "--end",
        required=True,
        help="Evaluation end time in ISO-8601 form",
    )
    ml_walk_forward_parser.add_argument(
        "--strategy",
        required=True,
        help="ML strategy id to evaluate, for example ai_regression",
    )
    ml_walk_forward_parser.add_argument(
        "--timeframe",
        required=True,
        help="Single timeframe to evaluate, for example 1h",
    )
    ml_walk_forward_parser.add_argument(
        "--config",
        help="Optional path to the base config.yaml to use for the evaluation",
    )
    ml_walk_forward_parser.add_argument(
        "--pair",
        action="append",
        help="Limit the evaluation to one pair; repeat to include multiple pairs",
    )
    ml_walk_forward_parser.add_argument(
        "--train-bars",
        type=int,
        default=500,
        help="Number of replay bars used for each training window",
    )
    ml_walk_forward_parser.add_argument(
        "--test-bars",
        type=int,
        default=100,
        help="Number of replay bars used for each out-of-sample test window",
    )
    ml_walk_forward_parser.add_argument(
        "--fee-bps",
        type=float,
        default=25.0,
        help="Flat taker fee in basis points used for the cost hurdle",
    )
    ml_walk_forward_parser.add_argument(
        "--slippage-bps",
        type=float,
        default=None,
        help="Override estimated slippage basis points used for the cost hurdle",
    )
    ml_walk_forward_parser.add_argument(
        "--db-path",
        help="Optional SQLite path for ML examples, checkpoints, and decisions",
    )
    ml_walk_forward_parser.add_argument(
        "--save-report",
        help="Optional JSON path for a durable ML walk-forward report artifact",
    )
    ml_walk_forward_parser.add_argument(
        "--publish-latest",
        action="store_true",
        help="Write this ML walk-forward report to the canonical latest report path",
    )
    ml_walk_forward_parser.add_argument(
        "--strict-data",
        action="store_true",
        help="Fail if any requested pair/timeframe is missing or only partially covered",
    )
    ml_walk_forward_parser.add_argument(
        "--json",
        action="store_true",
        help="Print the ML walk-forward report as JSON",
    )
    ml_walk_forward_parser.add_argument(
        "--feature-profile",
        choices=tuple(sorted(ML_FEATURE_PROFILES)),
        help="Experimental ML feature subset profile for this walk-forward run",
    )
    ml_walk_forward_parser.set_defaults(func=_ml_walk_forward_command)

    ml_report_compare_parser = subparsers.add_parser(
        "ml-report-compare",
        help="Compare saved ML walk-forward JSON reports",
    )
    ml_report_compare_parser.add_argument(
        "reports",
        nargs="*",
        help="Saved ML walk-forward report JSON paths",
    )
    ml_report_compare_parser.add_argument(
        "--glob",
        dest="glob_pattern",
        help="Glob pattern for report JSON paths, for example reports/ml/*.json",
    )
    ml_report_compare_parser.add_argument(
        "--format",
        choices=("markdown", "tsv", "json"),
        default="markdown",
        help="Output format for the comparison table",
    )
    ml_report_compare_parser.add_argument(
        "--sort",
        choices=("name", "precision-long", "p95-lift", "positive-calls"),
        default="name",
        help="Sort order for report rows",
    )
    ml_report_compare_parser.add_argument(
        "--output",
        help="Optional path to write the rendered comparison",
    )
    ml_report_compare_parser.set_defaults(func=_ml_report_compare_command)

    ml_feature_ablation_parser = subparsers.add_parser(
        "ml-feature-ablation-summary",
        help="Summarize feature ablation candidates from ML walk-forward reports",
    )
    ml_feature_ablation_parser.add_argument(
        "reports",
        nargs="*",
        help="Saved ML walk-forward report JSON paths",
    )
    ml_feature_ablation_parser.add_argument(
        "--glob",
        dest="glob_pattern",
        help="Glob pattern for report JSON paths, for example reports/ml/*.json",
    )
    ml_feature_ablation_parser.add_argument(
        "--format",
        choices=("markdown", "tsv", "json"),
        default="markdown",
        help="Output format for the feature ablation table",
    )
    ml_feature_ablation_parser.add_argument(
        "--sort",
        choices=("drop-score", "contribution", "rank", "health", "name"),
        default="drop-score",
        help="Sort order for feature rows",
    )
    ml_feature_ablation_parser.add_argument(
        "--output",
        help="Optional path to write the rendered feature ablation table",
    )
    ml_feature_ablation_parser.set_defaults(func=_ml_feature_ablation_summary_command)

    ml_prune_stale_parser = subparsers.add_parser(
        "ml-prune-stale",
        help="Report or delete stale persisted ML examples, models, and checkpoints",
    )
    ml_prune_stale_parser.add_argument(
        "--config",
        help="Optional path to the base config.yaml used to identify current ML keys",
    )
    ml_prune_stale_parser.add_argument(
        "--db-path",
        default=DEFAULT_DB_PATH,
        help=f"Path to the SQLite portfolio store (defaults to {DEFAULT_DB_PATH})",
    )
    ml_prune_stale_parser.add_argument(
        "--strategy",
        help="Limit stale detection to one strategy id",
    )
    ml_prune_stale_parser.add_argument(
        "--older-than-days",
        type=int,
        help="Only include stale groups whose latest artifact is older than N days",
    )
    ml_prune_stale_parser.add_argument(
        "--apply",
        action="store_true",
        help="Delete stale groups; without this flag the command is a dry run",
    )
    ml_prune_stale_parser.add_argument(
        "--json",
        action="store_true",
        help="Print stale ML artifact candidates as JSON",
    )
    ml_prune_stale_parser.set_defaults(func=_ml_prune_stale_command)

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
