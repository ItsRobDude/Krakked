"""Backtesting utilities for running strategies against stored OHLC data."""

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
    "run_backtest",
]
