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
from .ml_walk_forward import (
    MLWalkForwardFold,
    MLWalkForwardPrediction,
    MLWalkForwardResult,
    MLWalkForwardSummary,
    run_ml_walk_forward,
)
from .ml_reporting import (
    get_latest_ml_walk_forward_report_path,
    load_ml_walk_forward_report,
    publish_latest_ml_walk_forward_report,
    summarize_latest_ml_walk_forward_report,
    validate_ml_walk_forward_report_payload,
    write_ml_walk_forward_report,
)

__all__ = [
    "BacktestPreflight",
    "BacktestPreflightResult",
    "BacktestResult",
    "BacktestSummary",
    "MLWalkForwardFold",
    "MLWalkForwardPrediction",
    "MLWalkForwardResult",
    "MLWalkForwardSummary",
    "build_backtest_preflight",
    "get_latest_backtest_report_path",
    "get_latest_ml_walk_forward_report_path",
    "load_backtest_report",
    "load_ml_walk_forward_report",
    "publish_latest_backtest_report",
    "publish_latest_ml_walk_forward_report",
    "run_backtest",
    "run_ml_walk_forward",
    "summarize_latest_backtest_report",
    "summarize_latest_ml_walk_forward_report",
    "validate_backtest_report_payload",
    "validate_ml_walk_forward_report_payload",
    "write_backtest_report",
    "write_ml_walk_forward_report",
]
