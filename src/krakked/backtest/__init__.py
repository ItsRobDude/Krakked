"""Backtesting utilities for running strategies against stored OHLC data."""

from .reporting import (
    get_latest_backtest_report_path,
    load_backtest_report,
    publish_latest_backtest_report,
    summarize_latest_backtest_report,
    validate_backtest_report_payload,
    write_backtest_report,
)
from .runner import (
    BacktestPreflight,
    BacktestPreflightResult,
    BacktestResult,
    BacktestSummary,
    build_backtest_preflight,
    run_backtest,
)

__all__ = [
    "BacktestPreflight",
    "BacktestPreflightResult",
    "BacktestResult",
    "BacktestSummary",
    "build_backtest_preflight",
    "get_latest_backtest_report_path",
    "load_backtest_report",
    "publish_latest_backtest_report",
    "run_backtest",
    "summarize_latest_backtest_report",
    "validate_backtest_report_payload",
    "write_backtest_report",
]
