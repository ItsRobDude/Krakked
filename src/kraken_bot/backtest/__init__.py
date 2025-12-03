"""Backtesting utilities for running strategies against stored OHLC data."""

from .runner import BacktestResult, run_backtest

__all__ = ["BacktestResult", "run_backtest"]
