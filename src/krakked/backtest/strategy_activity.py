"""Cache-only strategy activity diagnostics for offline replay windows."""

from __future__ import annotations

import copy
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Mapping, Sequence

from krakked.config import AppConfig

from .runner import BacktestMarketData, BacktestResult, run_backtest

REPORT_TYPE_STRATEGY_ACTIVITY_SWEEP = "strategy_activity_sweep"
REPORT_VERSION = 1
STARTER_STRATEGIES = ("trend_core", "vol_breakout", "majors_mean_rev")
DEFAULT_STRATEGY_ACTIVITY_GROUP_IDS = (
    "configured",
    "starter_all",
    "trend_core",
    "vol_breakout",
    "majors_mean_rev",
)
DEFAULT_STRATEGY_EVIDENCE_GROUP_IDS = ("configured", "starter_all")
STRATEGY_ACTIVITY_WINDOW_SETS = {
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


@dataclass(frozen=True)
class StrategyActivityGroup:
    group_id: str
    strategies: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "group_id": self.group_id,
            "strategies": list(self.strategies),
        }


@dataclass
class StrategyActivitySweepResult:
    generated_at: datetime
    summary: dict[str, Any]
    runs: list[dict[str, Any]]

    def to_report_dict(self) -> dict[str, Any]:
        return {
            "report_version": REPORT_VERSION,
            "report_type": REPORT_TYPE_STRATEGY_ACTIVITY_SWEEP,
            "generated_at": self.generated_at.astimezone(UTC).isoformat(),
            "summary": copy.deepcopy(self.summary),
            "runs": copy.deepcopy(self.runs),
        }


def build_strategy_activity_groups(
    config: AppConfig,
    group_ids: Sequence[str] | None = None,
    *,
    custom_strategies: Sequence[str] | None = None,
) -> list[StrategyActivityGroup]:
    requested = list(group_ids or DEFAULT_STRATEGY_ACTIVITY_GROUP_IDS)
    groups: list[StrategyActivityGroup] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()

    for group_id in requested:
        strategies = _strategies_for_group(config, group_id)
        if not strategies:
            continue
        key = (group_id, tuple(strategies))
        if key in seen:
            continue
        groups.append(StrategyActivityGroup(group_id=group_id, strategies=key[1]))
        seen.add(key)

    custom = _clean_strategy_ids(custom_strategies or [])
    if custom:
        key = ("custom", tuple(custom))
        if key not in seen:
            groups.append(StrategyActivityGroup(group_id="custom", strategies=key[1]))

    return groups


def build_strategy_evidence_groups(
    config: AppConfig,
    group_ids: Sequence[str] | None = None,
    *,
    strategy_ids: Sequence[str] | None = None,
) -> list[StrategyActivityGroup]:
    """Build one scoreboard group per requested strategy plus optional packs."""

    requested_groups = list(group_ids or DEFAULT_STRATEGY_EVIDENCE_GROUP_IDS)
    requested_strategies = list(strategy_ids or config.strategies.configs.keys())
    groups: list[StrategyActivityGroup] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()

    for group_id in requested_groups:
        strategies = _strategies_for_group(config, group_id)
        if not strategies:
            continue
        key = (group_id, tuple(strategies))
        if key in seen:
            continue
        groups.append(StrategyActivityGroup(group_id=group_id, strategies=key[1]))
        seen.add(key)

    for strategy_id in _clean_strategy_ids(requested_strategies):
        if strategy_id not in config.strategies.configs:
            raise ValueError(f"Unknown strategy id: {strategy_id}")
        key = (strategy_id, (strategy_id,))
        if key in seen:
            continue
        groups.append(StrategyActivityGroup(group_id=strategy_id, strategies=key[1]))
        seen.add(key)

    return groups


def run_strategy_activity_sweep(
    config: AppConfig,
    *,
    window_sets: Mapping[str, Sequence[tuple[str, str, str]]],
    groups: Sequence[StrategyActivityGroup],
    starting_cash_usd: float = 10_000.0,
    fee_bps: float = 25.0,
    strict_data: bool = False,
    warmup_days: float | None = None,
) -> StrategyActivitySweepResult:
    runs: list[dict[str, Any]] = []
    for window_set, windows in window_sets.items():
        for window_id, start_text, end_text in windows:
            start = _parse_utc(start_text)
            end = _parse_utc(end_text)
            for group in groups:
                try:
                    result = _run_group_backtest(
                        config,
                        group,
                        start=start,
                        end=end,
                        starting_cash_usd=starting_cash_usd,
                        fee_bps=fee_bps,
                        strict_data=strict_data,
                        warmup_days=warmup_days,
                    )
                    runs.append(
                        _activity_run_payload(
                            window_set=window_set,
                            window_id=window_id,
                            group=group,
                            result=result,
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    runs.append(
                        _activity_error_payload(
                            window_set=window_set,
                            window_id=window_id,
                            group=group,
                            error=exc,
                        )
                    )

    return StrategyActivitySweepResult(
        generated_at=datetime.now(UTC),
        summary=_activity_sweep_summary(runs, groups),
        runs=runs,
    )


def apply_strategy_activity_override(
    config: AppConfig,
    strategies: Sequence[str],
) -> AppConfig:
    config_copy = copy.deepcopy(config)
    strategy_ids = _clean_strategy_ids(strategies)
    unknown = [
        strategy_id
        for strategy_id in strategy_ids
        if strategy_id not in config_copy.strategies.configs
    ]
    if unknown:
        raise ValueError(f"Unknown strategy id(s): {', '.join(unknown)}")
    config_copy.strategies.enabled = list(strategy_ids)
    for strategy_id, strat_cfg in config_copy.strategies.configs.items():
        strat_cfg.enabled = strategy_id in strategy_ids
    if getattr(config_copy.risk, "market_regime_throttle", None) is not None:
        config_copy.risk.market_regime_throttle.enabled = False
    return config_copy


def _run_group_backtest(
    config: AppConfig,
    group: StrategyActivityGroup,
    *,
    start: datetime,
    end: datetime,
    starting_cash_usd: float,
    fee_bps: float,
    strict_data: bool,
    warmup_days: float | None,
) -> BacktestResult:
    config_copy = apply_strategy_activity_override(config, group.strategies)
    return run_backtest(
        config_copy,
        start=start,
        end=end,
        starting_cash_usd=starting_cash_usd,
        fee_bps=fee_bps,
        strict_data=strict_data,
        warmup_days=warmup_days,
    )


def _activity_run_payload(
    *,
    window_set: str,
    window_id: str,
    group: StrategyActivityGroup,
    result: BacktestResult,
) -> dict[str, Any]:
    if result.summary is None:
        raise ValueError("Strategy activity replay did not produce a summary")
    summary = result.summary.to_dict()
    per_strategy = copy.deepcopy(summary.get("per_strategy") or {})
    return {
        "window_set": window_set,
        "window_id": window_id,
        "group_id": group.group_id,
        "strategies": list(group.strategies),
        "stage": _activity_stage(summary, per_strategy),
        "trust_level": summary.get("trust_level"),
        "trust_note": summary.get("trust_note"),
        "total_cycles": int(summary.get("total_cycles", 0) or 0),
        "total_actions": int(summary.get("total_actions", 0) or 0),
        "blocked_actions": int(summary.get("blocked_actions", 0) or 0),
        "clamped_actions": int(summary.get("clamped_actions", 0) or 0),
        "total_orders": int(summary.get("total_orders", 0) or 0),
        "filled_orders": int(summary.get("filled_orders", 0) or 0),
        "execution_errors": int(summary.get("execution_errors", 0) or 0),
        "return_pct": float(summary.get("return_pct", 0.0) or 0.0),
        "max_drawdown_pct": float(summary.get("max_drawdown_pct", 0.0) or 0.0),
        "missing_series": list(summary.get("missing_series") or []),
        "partial_series": list(summary.get("partial_series") or []),
        "blocked_reason_counts": copy.deepcopy(
            summary.get("blocked_reason_counts") or {}
        ),
        "clamped_reason_counts": copy.deepcopy(
            summary.get("clamped_reason_counts") or {}
        ),
        "per_strategy": per_strategy,
    }


def _activity_error_payload(
    *,
    window_set: str,
    window_id: str,
    group: StrategyActivityGroup,
    error: Exception,
) -> dict[str, Any]:
    error_text = str(error)
    stage = "data_not_ready" if "strict mode" in error_text else "run_failed"
    return {
        "window_set": window_set,
        "window_id": window_id,
        "group_id": group.group_id,
        "strategies": list(group.strategies),
        "stage": stage,
        "trust_level": "weak_signal",
        "trust_note": f"Activity diagnostic failed: {error_text}",
        "total_cycles": 0,
        "total_actions": 0,
        "blocked_actions": 0,
        "clamped_actions": 0,
        "total_orders": 0,
        "filled_orders": 0,
        "execution_errors": 1 if stage == "run_failed" else 0,
        "return_pct": 0.0,
        "max_drawdown_pct": 0.0,
        "missing_series": [],
        "partial_series": [],
        "blocked_reason_counts": {},
        "clamped_reason_counts": {},
        "per_strategy": {
            strategy_id: {
                "cycles_evaluated": 0,
                "contexts_evaluated": 0,
                "timeframes_evaluated": [],
                "intents_emitted": 0,
                "actions_after_scoring": 0,
                "filtered_by_score": 0,
                "filtered_no_position_exits": 0,
                "filtered_position_exits": 0,
                "filtered_low_score_entries": 0,
                "blocked_actions": 0,
                "data_stale_contexts": 0,
                "skipped_no_pairs": 0,
                "skipped_stale_timeframe_contexts": 0,
                "min_score": None,
                "max_score": None,
            }
            for strategy_id in group.strategies
        },
        "error": error_text,
    }


def _activity_sweep_summary(
    runs: Sequence[Mapping[str, Any]],
    groups: Sequence[StrategyActivityGroup],
) -> dict[str, Any]:
    group_summaries: list[dict[str, Any]] = []
    for group in groups:
        group_runs = [run for run in runs if run.get("group_id") == group.group_id]
        if not group_runs:
            continue
        window_count = len(group_runs)
        action_windows = sum(1 for run in group_runs if int(run["total_actions"]) > 0)
        fill_windows = sum(1 for run in group_runs if int(run["filled_orders"]) > 0)
        ready_windows = sum(
            1
            for run in group_runs
            if not run.get("error")
            and not run.get("missing_series")
            and not run.get("partial_series")
        )
        execution_error_windows = sum(
            1
            for run in group_runs
            if int(run["execution_errors"]) > 0 or bool(run.get("error"))
        )
        stage_counts = Counter(str(run.get("stage") or "unknown") for run in group_runs)
        group_summaries.append(
            {
                "group_id": group.group_id,
                "strategies": list(group.strategies),
                "window_count": window_count,
                "ready_windows": ready_windows,
                "action_windows": action_windows,
                "fill_windows": fill_windows,
                "execution_error_windows": execution_error_windows,
                "stage_counts": dict(stage_counts.most_common()),
                "total_actions": sum(int(run["total_actions"]) for run in group_runs),
                "total_filled_orders": sum(
                    int(run["filled_orders"]) for run in group_runs
                ),
                "avg_actions_per_window": (
                    sum(int(run["total_actions"]) for run in group_runs) / window_count
                ),
                "avg_fills_per_window": (
                    sum(int(run["filled_orders"]) for run in group_runs) / window_count
                ),
                "gate2_candidate": (
                    ready_windows == window_count
                    and execution_error_windows == 0
                    and action_windows > 0
                    and fill_windows > 0
                ),
            }
        )

    candidate_groups = [
        group["group_id"] for group in group_summaries if group["gate2_candidate"]
    ]
    best_group = _best_activity_group(group_summaries)
    return {
        "research_only": True,
        "runtime_config_changed": False,
        "group_count": len(group_summaries),
        "run_count": len(runs),
        "window_sets": sorted({str(run["window_set"]) for run in runs}),
        "groups": group_summaries,
        "gate2_candidate_groups": candidate_groups,
        "best_gate2_candidate_group": best_group,
        "ready_for_gate2": bool(candidate_groups),
    }


def build_strategy_evidence_scoreboard(
    runs: Sequence[Mapping[str, Any]],
    groups: Sequence[StrategyActivityGroup],
    *,
    baselines: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Summarize strategy replay runs as one comparable evidence table."""

    rows: list[dict[str, Any]] = []
    for group in groups:
        group_runs = [run for run in runs if run.get("group_id") == group.group_id]
        if not group_runs:
            continue
        ready_runs = [
            run
            for run in group_runs
            if not run.get("error") and run.get("stage") != "data_not_ready"
        ]
        filled_runs = [run for run in ready_runs if run.get("stage") == "filled"]
        ready_returns = [
            float(run.get("return_pct", 0.0) or 0.0) for run in ready_runs
        ]
        filled_returns = [
            float(run.get("return_pct", 0.0) or 0.0) for run in filled_runs
        ]
        ready_drawdowns = [
            float(run.get("max_drawdown_pct", 0.0) or 0.0) for run in ready_runs
        ]
        positive_ready = [value for value in ready_returns if value > 0.0]
        row = {
            "group_id": group.group_id,
            "strategies": list(group.strategies),
            "window_count": len(group_runs),
            "ready_windows": len(ready_runs),
            "filled_windows": len(filled_runs),
            "positive_ready_windows": len(positive_ready),
            "avg_return_ready_pct": _mean_or_none(ready_returns),
            "avg_return_filled_pct": _mean_or_none(filled_returns),
            "avg_max_drawdown_ready_pct": _mean_or_none(ready_drawdowns),
            "total_actions": sum(
                int(run.get("total_actions", 0) or 0) for run in group_runs
            ),
            "total_filled_orders": sum(
                int(run.get("filled_orders", 0) or 0) for run in group_runs
            ),
            "stage_counts": dict(
                Counter(str(run.get("stage") or "unknown") for run in group_runs)
            ),
            "beats_cash_ready_windows": len(positive_ready),
            "current_recent_20d": _scoreboard_window_snapshot(
                group_runs,
                window_set="recent_20d",
                window_id="20260510-20260530",
            ),
            "current_long_4h": _scoreboard_window_snapshot(
                group_runs,
                window_set="long_4h",
                window_id="20260430-20260530",
            ),
        }
        row["evidence_status"] = _strategy_evidence_status(row)
        rows.append(row)

    return {
        "research_only": True,
        "runtime_config_changed": False,
        "same_replay_engine": True,
        "cash_baseline_return_pct": 0.0,
        "rows": rows,
        "baselines": copy.deepcopy(dict(baselines or {})),
    }


def build_strategy_evidence_baselines(
    config: AppConfig,
    *,
    window_sets: Mapping[str, Sequence[tuple[str, str, str]]],
    pairs: Sequence[str] | None = None,
    timeframe: str = "4h",
    starting_cash_usd: float = 10_000.0,
    fee_bps: float = 25.0,
) -> dict[str, Any]:
    """Build cash and equal-weight buy-hold context over the scoreboard windows."""

    baseline_pairs = _clean_pairs(
        pairs if pairs is not None else config.universe.include_pairs
    )
    windows: list[dict[str, Any]] = []
    for window_set, rows in window_sets.items():
        for window_id, start_text, end_text in rows:
            start = _parse_utc(start_text)
            end = _parse_utc(end_text)
            windows.append(
                _buy_hold_equal_weight_window(
                    config,
                    window_set=window_set,
                    window_id=window_id,
                    start=start,
                    end=end,
                    pairs=baseline_pairs,
                    timeframe=timeframe,
                    starting_cash_usd=starting_cash_usd,
                    fee_bps=fee_bps,
                )
            )

    usable = [window for window in windows if window.get("return_pct") is not None]
    returns = [float(window["return_pct"]) for window in usable]
    drawdowns = [float(window["max_drawdown_pct"]) for window in usable]
    return {
        "cash": {
            "return_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "window_count": len(windows),
        },
        "buy_hold_equal_weight": {
            "pairs": baseline_pairs,
            "timeframe": timeframe,
            "one_way_fee_bps": float(fee_bps),
            "window_count": len(windows),
            "usable_windows": len(usable),
            "positive_windows": sum(1 for value in returns if value > 0.0),
            "avg_return_pct": _mean_or_none(returns),
            "avg_max_drawdown_pct": _mean_or_none(drawdowns),
            "windows": windows,
        },
    }


def _best_activity_group(groups: Sequence[Mapping[str, Any]]) -> str | None:
    candidates = [group for group in groups if bool(group.get("gate2_candidate"))]
    if not candidates:
        return None
    ranked = sorted(
        candidates,
        key=lambda group: (
            int(group.get("fill_windows", 0) or 0),
            int(group.get("action_windows", 0) or 0),
            int(group.get("total_filled_orders", 0) or 0),
            int(group.get("total_actions", 0) or 0),
        ),
        reverse=True,
    )
    return str(ranked[0]["group_id"])


def _mean_or_none(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _scoreboard_window_snapshot(
    runs: Sequence[Mapping[str, Any]],
    *,
    window_set: str,
    window_id: str,
) -> dict[str, Any] | None:
    for run in runs:
        if run.get("window_set") == window_set and run.get("window_id") == window_id:
            return {
                "window_set": window_set,
                "window_id": window_id,
                "stage": run.get("stage"),
                "return_pct": run.get("return_pct") if not run.get("error") else None,
                "max_drawdown_pct": (
                    run.get("max_drawdown_pct") if not run.get("error") else None
                ),
                "filled_orders": run.get("filled_orders"),
                "error": run.get("error"),
            }
    return None


def _strategy_evidence_status(row: Mapping[str, Any]) -> str:
    if int(row.get("ready_windows", 0) or 0) <= 0:
        return "data_not_ready"
    if int(row.get("filled_windows", 0) or 0) <= 0:
        return "inactive_or_cash"
    avg_return = row.get("avg_return_ready_pct")
    if avg_return is None:
        return "insufficient_data"
    return "positive" if float(avg_return) > 0.0 else "unproven"


def _clean_pairs(values: Sequence[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in values:
        pair = str(value).strip()
        if not pair or pair in seen:
            continue
        cleaned.append(pair)
        seen.add(pair)
    return cleaned


def _buy_hold_equal_weight_window(
    config: AppConfig,
    *,
    window_set: str,
    window_id: str,
    start: datetime,
    end: datetime,
    pairs: Sequence[str],
    timeframe: str,
    starting_cash_usd: float,
    fee_bps: float,
) -> dict[str, Any]:
    if not pairs:
        return _baseline_window_payload(
            window_set=window_set,
            window_id=window_id,
            return_pct=None,
            max_drawdown_pct=None,
            warnings=["No buy-hold baseline pairs requested."],
        )

    market_data = BacktestMarketData(config, pairs, [timeframe], start, end)
    coverage = [item.to_dict() for item in market_data.get_preflight().coverage]
    timestamps = list(market_data.iter_timestamps())
    if not timestamps:
        return _baseline_window_payload(
            window_set=window_set,
            window_id=window_id,
            return_pct=None,
            max_drawdown_pct=None,
            warnings=["No cached bars available for buy-hold baseline window."],
            coverage=coverage,
        )

    pair_cash = float(starting_cash_usd) / len(pairs)
    one_way_cost = max(float(fee_bps), 0.0) / 10_000.0
    curves: list[list[float]] = []
    warnings: list[str] = []
    for pair in pairs:
        start_bar = market_data.get_bar_at_or_after(pair, timeframe, timestamps[0])
        if start_bar is None or float(start_bar.close) <= 0.0:
            warnings.append(f"Missing start bar for {pair}@{timeframe}.")
            continue
        base_size = (pair_cash * (1.0 - one_way_cost)) / float(start_bar.close)
        curve: list[float] = []
        for timestamp in timestamps:
            bar = market_data.get_bar_at_or_before(pair, timeframe, timestamp)
            if bar is None or float(bar.close) <= 0.0:
                warnings.append(f"Missing mark bar for {pair}@{timeframe}.")
                curve = []
                break
            curve.append(base_size * float(bar.close) * (1.0 - one_way_cost))
        if curve:
            curves.append(curve)

    if len(curves) != len(pairs):
        return _baseline_window_payload(
            window_set=window_set,
            window_id=window_id,
            return_pct=None,
            max_drawdown_pct=None,
            warnings=warnings,
            coverage=coverage,
        )

    min_length = min(len(curve) for curve in curves)
    equity_curve = [
        sum(curve[index] for curve in curves) for index in range(min_length)
    ]
    ending_equity = equity_curve[-1] if equity_curve else float(starting_cash_usd)
    return _baseline_window_payload(
        window_set=window_set,
        window_id=window_id,
        return_pct=((ending_equity - starting_cash_usd) / starting_cash_usd) * 100.0,
        max_drawdown_pct=_max_drawdown_pct(equity_curve),
        warnings=warnings,
        coverage=coverage,
    )


def _baseline_window_payload(
    *,
    window_set: str,
    window_id: str,
    return_pct: float | None,
    max_drawdown_pct: float | None,
    warnings: Sequence[str],
    coverage: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "window_set": window_set,
        "window_id": window_id,
        "return_pct": return_pct,
        "max_drawdown_pct": max_drawdown_pct,
        "warnings": sorted(set(str(warning) for warning in warnings if warning)),
        "coverage": copy.deepcopy(list(coverage or [])),
    }


def _max_drawdown_pct(equity_curve: Sequence[float]) -> float:
    peak = 0.0
    max_drawdown = 0.0
    for equity in equity_curve:
        value = float(equity)
        if value <= 0.0:
            continue
        peak = max(peak, value)
        if peak <= 0.0:
            continue
        drawdown = ((peak - value) / peak) * 100.0
        max_drawdown = max(max_drawdown, drawdown)
    return max_drawdown


def _activity_stage(
    summary: Mapping[str, Any],
    per_strategy: Mapping[str, Mapping[str, Any]],
) -> str:
    if summary.get("missing_series") or summary.get("partial_series"):
        return "data_not_ready"
    if int(summary.get("execution_errors", 0) or 0) > 0:
        return "execution_errors"
    contexts = sum(
        int(strategy.get("contexts_evaluated", 0) or 0)
        for strategy in per_strategy.values()
    )
    intents = sum(
        int(strategy.get("intents_emitted", 0) or 0)
        for strategy in per_strategy.values()
    )
    actions_after_scoring = sum(
        int(strategy.get("actions_after_scoring", 0) or 0)
        for strategy in per_strategy.values()
    )
    total_actions = int(summary.get("total_actions", 0) or 0)
    blocked_actions = int(summary.get("blocked_actions", 0) or 0)
    total_orders = int(summary.get("total_orders", 0) or 0)
    filled_orders = int(summary.get("filled_orders", 0) or 0)

    if contexts <= 0:
        return "not_evaluated"
    if intents <= 0:
        return "no_intents"
    if actions_after_scoring <= 0:
        return "score_filtered"
    if total_actions > 0 and blocked_actions == total_actions:
        return "risk_blocked"
    if total_orders <= 0:
        return "no_orders"
    if filled_orders <= 0:
        return "no_fills"
    return "filled"


def _strategies_for_group(config: AppConfig, group_id: str) -> list[str]:
    if group_id == "configured":
        return _clean_strategy_ids(config.strategies.enabled)
    if group_id == "starter_all":
        return [
            strategy_id
            for strategy_id in STARTER_STRATEGIES
            if strategy_id in config.strategies.configs
        ]
    if group_id in STARTER_STRATEGIES:
        return [group_id] if group_id in config.strategies.configs else []
    raise ValueError(f"Unsupported strategy activity group: {group_id}")


def _clean_strategy_ids(values: Sequence[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in values:
        strategy_id = str(value).strip()
        if not strategy_id or strategy_id in seen:
            continue
        cleaned.append(strategy_id)
        seen.add(strategy_id)
    return cleaned


def _parse_utc(value: str) -> datetime:
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
