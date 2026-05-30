"""Backtesting utilities for running strategies against stored OHLC data."""

from .ml_reporting import (
    get_latest_ml_walk_forward_report_path,
    load_ml_walk_forward_report,
    publish_latest_ml_walk_forward_report,
    summarize_latest_ml_walk_forward_report,
    validate_ml_walk_forward_report_payload,
    write_ml_walk_forward_report,
)
from .ml_walk_forward import (
    MLWalkForwardFold,
    MLWalkForwardPrediction,
    MLWalkForwardResult,
    MLWalkForwardSummary,
    run_ml_walk_forward,
)
from .reporting import (
    get_latest_backtest_report_path,
    load_backtest_report,
    publish_latest_backtest_report,
    summarize_latest_backtest_report,
    validate_backtest_report_payload,
    write_backtest_report,
)
from .rs_rotation_v2_research import (
    RSRotationV2ResearchParams,
    RSRotationV2ResearchResult,
    default_rs_rotation_v2_allocation_pct,
    default_rs_rotation_v2_lookback_bars,
    default_rs_rotation_v2_timeframe,
    default_rs_rotation_v2_top_n,
    evaluate_rs_rotation_v2_bars,
    run_rs_rotation_v2_research,
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
    "MLWalkForwardFold",
    "MLWalkForwardPrediction",
    "MLWalkForwardResult",
    "MLWalkForwardSummary",
    "RSRotationV2ResearchParams",
    "RSRotationV2ResearchResult",
    "build_backtest_preflight",
    "default_rs_rotation_v2_allocation_pct",
    "default_rs_rotation_v2_lookback_bars",
    "default_rs_rotation_v2_timeframe",
    "default_rs_rotation_v2_top_n",
    "evaluate_rs_rotation_v2_bars",
    "get_latest_backtest_report_path",
    "get_latest_ml_walk_forward_report_path",
    "load_backtest_report",
    "load_ml_walk_forward_report",
    "publish_latest_backtest_report",
    "publish_latest_ml_walk_forward_report",
    "run_backtest",
    "run_rs_rotation_v2_research",
    "run_ml_walk_forward",
    "summarize_latest_backtest_report",
    "summarize_latest_ml_walk_forward_report",
    "validate_backtest_report_payload",
    "validate_ml_walk_forward_report_payload",
    "write_backtest_report",
    "write_ml_walk_forward_report",
]
