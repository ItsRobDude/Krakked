"""Controlled exposure scenarios for market-regime overlay research."""

from __future__ import annotations

import copy
import math
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from statistics import mean, pstdev
from typing import Any, Mapping, Sequence

from krakked.config import AppConfig
from krakked.market_data.models import OHLCBar
from krakked.strategy.models import ExecutionPlan, RiskAdjustedAction

from .market_regime_overlay import (
    MarketRegimeOverlayParams,
    MarketRegimeSnapshot,
    _as_utc,
    _clean_pairs,
    _default_pairs,
    _preflight_to_dict,
    _sort_bars,
    _strict_data_message,
    apply_market_regime_overlay_to_plan,
    classify_market_regime_snapshot,
)
from .runner import BacktestMarketData, _timeframe_seconds, backtest_strict_data_details

REPORT_TYPE_EXPOSURE_RESEARCH = "market_regime_exposure_research"
REPORT_VERSION = 1
DEFAULT_EXPOSURE_SCENARIOS = (
    "starter_equal_weight",
    "btc_only",
    "alt_equal_weight",
    "trend_proxy",
    "trend_rank_proxy",
)
DEFAULT_EXPOSURE_OVERLAY_MODES = ("entry_guard", "target_scale")
SUPPORTED_EXPOSURE_SCENARIOS = frozenset(DEFAULT_EXPOSURE_SCENARIOS)
SUPPORTED_EXPOSURE_OVERLAY_MODES = frozenset(("entry_guard", "target_scale"))
TOP2_SOFT_TARGET_SCALE_PROFILE_ID = "top2_soft_target_scale"
REPORT_TYPE_DEFENSIVE_BASELINE = "defensive_baseline_report"
DEFAULT_DEFENSIVE_BASELINE_START = "2025-12-01T00:00:00Z"
DEFAULT_DEFENSIVE_BASELINE_WINDOW_SET = "regime_diverse_4h"
DEFAULT_DEFENSIVE_BASELINE_PAIRS = ("BTC/USD", "ETH/USD", "SOL/USD", "ADA/USD")
DEFAULT_DEFENSIVE_BASELINE_REBALANCE_DELTA_PCT = 2.5
DEFAULT_STATIC_EXPOSURE_FRONTIER_PCTS = tuple(
    float(value) for value in range(0, 101, 10)
)


@dataclass(frozen=True)
class MarketRegimeExposureScenarioParams:
    allocation_pct: float = 20.0
    rebalance_interval_bars: int = 6
    starting_cash_usd: float = 10_000.0
    fee_bps: float = 25.0
    target_lookback_bars: int = 63
    min_momentum_bps: float = 150.0
    max_target_pairs: int = 4

    def __post_init__(self) -> None:
        if self.allocation_pct <= 0.0 or self.allocation_pct > 100.0:
            raise ValueError("allocation_pct must be greater than 0 and at most 100")
        if int(self.rebalance_interval_bars) < 1:
            raise ValueError("rebalance_interval_bars must be at least 1")
        if self.starting_cash_usd <= 0.0:
            raise ValueError("starting_cash_usd must be greater than 0")
        if self.fee_bps < 0.0:
            raise ValueError("fee_bps must be greater than or equal to 0")
        if int(self.target_lookback_bars) < 2:
            raise ValueError("target_lookback_bars must be at least 2")
        if int(self.max_target_pairs) < 1:
            raise ValueError("max_target_pairs must be at least 1")


@dataclass
class MarketRegimeExposureResearchResult:
    generated_at: datetime
    start: datetime
    end: datetime
    pairs: list[str]
    regime_params: MarketRegimeOverlayParams
    scenario_params: MarketRegimeExposureScenarioParams
    summary: dict[str, Any]
    preflight: dict[str, Any] | None = None
    runs: list[dict[str, Any]] = field(default_factory=list)
    comparisons: list[dict[str, Any]] = field(default_factory=list)

    def to_report_dict(self) -> dict[str, Any]:
        return {
            "report_version": REPORT_VERSION,
            "report_type": REPORT_TYPE_EXPOSURE_RESEARCH,
            "generated_at": self.generated_at.astimezone(UTC).isoformat(),
            "summary": copy.deepcopy(self.summary),
            "preflight": copy.deepcopy(self.preflight),
            "runs": copy.deepcopy(self.runs),
            "comparisons": copy.deepcopy(self.comparisons),
        }


@dataclass
class DefensiveBaselineReportResult:
    generated_at: datetime
    summary: dict[str, Any]
    primary_continuous_span: dict[str, Any]
    regime_window_results: list[dict[str, Any]]
    static_exposure_frontier: list[dict[str, Any]]
    matched_exposure_comparisons: list[dict[str, Any]]
    lever_attribution: dict[str, Any]
    preflight: dict[str, Any] | None = None

    def to_report_dict(self) -> dict[str, Any]:
        return {
            "report_version": REPORT_VERSION,
            "report_type": REPORT_TYPE_DEFENSIVE_BASELINE,
            "generated_at": self.generated_at.astimezone(UTC).isoformat(),
            "summary": copy.deepcopy(self.summary),
            "preflight": copy.deepcopy(self.preflight),
            "primary_continuous_span": copy.deepcopy(self.primary_continuous_span),
            "regime_window_results": copy.deepcopy(self.regime_window_results),
            "static_exposure_frontier": copy.deepcopy(self.static_exposure_frontier),
            "matched_exposure_comparisons": copy.deepcopy(
                self.matched_exposure_comparisons
            ),
            "lever_attribution": copy.deepcopy(self.lever_attribution),
        }


@dataclass
class _ScenarioPortfolio:
    cash_usd: float
    holdings: dict[str, float]


def run_market_regime_exposure_research(
    config: AppConfig,
    *,
    start: datetime,
    end: datetime,
    pairs: Sequence[str] | None = None,
    regime_params: MarketRegimeOverlayParams | None = None,
    scenario_params: MarketRegimeExposureScenarioParams | None = None,
    scenarios: Sequence[str] | None = None,
    overlay_modes: Sequence[str] | None = None,
    strict_data: bool = False,
) -> MarketRegimeExposureResearchResult:
    regime_params = regime_params or MarketRegimeOverlayParams()
    scenario_params = scenario_params or MarketRegimeExposureScenarioParams()
    selected_pairs = _clean_pairs(list(pairs or _default_pairs(config)))
    if regime_params.benchmark_pair not in selected_pairs:
        selected_pairs.insert(0, regime_params.benchmark_pair)

    market_data = BacktestMarketData(
        config,
        pairs=selected_pairs,
        timeframes=[regime_params.timeframe],
        start=_as_utc(start),
        end=_as_utc(end),
    )
    try:
        preflight = market_data.get_preflight()
        if strict_data and (preflight.missing_series or preflight.partial_series):
            raise ValueError(
                _strict_data_message("market regime exposure research", preflight)
            )
        market_data.set_time(_as_utc(end))
        bars_by_pair = {
            pair: market_data.get_ohlc(
                pair, regime_params.timeframe, lookback=1_000_000
            )
            for pair in selected_pairs
        }
        return evaluate_market_regime_exposure_scenarios(
            bars_by_pair,
            start=start,
            end=end,
            pairs=selected_pairs,
            regime_params=regime_params,
            scenario_params=scenario_params,
            scenarios=scenarios,
            overlay_modes=overlay_modes,
            preflight=_preflight_to_dict(preflight),
        )
    finally:
        shutdown = getattr(market_data, "shutdown", None)
        if callable(shutdown):
            shutdown()


def run_defensive_baseline_report(
    config: AppConfig,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    pairs: Sequence[str] | None = None,
    timeframe: str = "4h",
    window_sets: Mapping[str, Sequence[tuple[str, str, str]]] | None = None,
    regime_params: MarketRegimeOverlayParams | None = None,
    scenario_params: MarketRegimeExposureScenarioParams | None = None,
    strict_data: bool = False,
    rebalance_delta_pct: float = DEFAULT_DEFENSIVE_BASELINE_REBALANCE_DELTA_PCT,
) -> DefensiveBaselineReportResult:
    """Build the defensive baseline yardstick report.

    The primary span is continuous-history first; optional evidence windows are
    secondary diagnostics. All timing decisions are made from bars available
    before the rebalance bar and applied on the rebalance bar's close through
    the existing controlled-exposure execution helper.
    """

    params = regime_params or MarketRegimeOverlayParams(timeframe=timeframe)
    scenario = scenario_params or MarketRegimeExposureScenarioParams(
        allocation_pct=100.0,
        rebalance_interval_bars=6,
        fee_bps=25.0,
        target_lookback_bars=63,
        max_target_pairs=2,
    )
    selected_pairs = _clean_pairs(
        list(pairs or DEFAULT_DEFENSIVE_BASELINE_PAIRS or _default_pairs(config))
    )
    if params.benchmark_pair not in selected_pairs:
        selected_pairs.insert(0, params.benchmark_pair)

    requested_start = _as_utc(start or _parse_utc(DEFAULT_DEFENSIVE_BASELINE_START))
    load_end = _as_utc(end or datetime.now(UTC))
    market_data = BacktestMarketData(
        config,
        pairs=selected_pairs,
        timeframes=[timeframe],
        start=requested_start,
        end=load_end,
    )
    try:
        preflight = market_data.get_preflight()
        strict_details = backtest_strict_data_details(preflight)
        if strict_data and end is not None and strict_details:
            raise ValueError(
                _strict_data_message("defensive baseline report", preflight)
            )
        market_data.set_time(load_end)
        bars_by_pair = {
            pair: market_data.get_ohlc(pair, timeframe, lookback=1_000_000)
            for pair in selected_pairs
        }
        return evaluate_defensive_baseline_report(
            bars_by_pair,
            start=requested_start,
            end=load_end,
            pairs=selected_pairs,
            regime_params=params,
            scenario_params=scenario,
            window_sets=window_sets,
            preflight=_preflight_to_dict(preflight),
            strict_data_requested=bool(strict_data),
            strict_data_details=strict_details,
            end_was_inferred=end is None,
            rebalance_delta_pct=rebalance_delta_pct,
        )
    finally:
        shutdown = getattr(market_data, "shutdown", None)
        if callable(shutdown):
            shutdown()


def build_top2_soft_target_scale_baseline(
    config: AppConfig,
    *,
    window_sets: Mapping[str, Sequence[tuple[str, str, str]]],
    pairs: Sequence[str] | None = None,
    timeframe: str = "4h",
    allocations: Sequence[float] = (5.0, 20.0),
    starting_cash_usd: float = 10_000.0,
) -> dict[str, Any]:
    """Build the hand-coded top-2 soft target-scale comparison baseline."""

    rows: list[dict[str, Any]] = []
    for window_set, windows in window_sets.items():
        for allocation_pct in allocations:
            regime_params = MarketRegimeOverlayParams(
                timeframe=timeframe,
                neutral_allocation_multiplier=0.75,
                risk_off_allocation_multiplier=0.25,
                momentum_lookback_bars=63,
                basket_momentum_lookback_bars=63,
                volatility_lookback_bars=63,
                drawdown_lookback_bars=63,
            )
            scenario_params = MarketRegimeExposureScenarioParams(
                allocation_pct=float(allocation_pct),
                rebalance_interval_bars=6,
                starting_cash_usd=float(starting_cash_usd),
                fee_bps=25.0,
                target_lookback_bars=63,
                max_target_pairs=2,
            )
            for window_id, start_text, end_text in windows:
                try:
                    result = run_market_regime_exposure_research(
                        config,
                        start=_parse_utc(start_text),
                        end=_parse_utc(end_text),
                        pairs=pairs,
                        regime_params=regime_params,
                        scenario_params=scenario_params,
                        scenarios=["trend_rank_proxy"],
                        overlay_modes=["target_scale"],
                        strict_data=True,
                    )
                    comparison = result.comparisons[0]
                    baseline = comparison["baseline"]
                    overlay = comparison["overlay"]
                    rows.append(
                        {
                            "window_set": window_set,
                            "window_id": window_id,
                            "allocation_pct": float(allocation_pct),
                            "status": "ready",
                            "baseline_return_pct": baseline["return_pct"],
                            "baseline_max_drawdown_pct": baseline["max_drawdown_pct"],
                            "overlay_return_pct": overlay["return_pct"],
                            "overlay_max_drawdown_pct": overlay["max_drawdown_pct"],
                            "delta_return_pct": comparison["delta"]["return_pct"],
                            "delta_max_drawdown_pct": comparison["delta"][
                                "max_drawdown_pct"
                            ],
                            "overlay_active_cycle_pct": overlay["active_cycle_pct"],
                            "overlay_avg_exposure_pct": overlay["avg_exposure_pct"],
                            "report_type": "controlled_exposure_proxy",
                        }
                    )
                except Exception as exc:  # noqa: BLE001
                    rows.append(
                        {
                            "window_set": window_set,
                            "window_id": window_id,
                            "allocation_pct": float(allocation_pct),
                            "status": "failed",
                            "error": str(exc),
                            "report_type": "controlled_exposure_proxy",
                        }
                    )

    groups: list[dict[str, Any]] = []
    for allocation_pct in sorted({float(row["allocation_pct"]) for row in rows}):
        items = [
            row
            for row in rows
            if float(row["allocation_pct"]) == allocation_pct
            and row.get("status") == "ready"
        ]
        returns = [float(row["delta_return_pct"]) for row in items]
        drawdowns = [float(row["delta_max_drawdown_pct"]) for row in items]
        groups.append(
            {
                "allocation_pct": allocation_pct,
                "ready_windows": len(items),
                "window_count": sum(
                    1 for row in rows if float(row["allocation_pct"]) == allocation_pct
                ),
                "avg_delta_return_pct": _mean_or_none(returns),
                "positive_return_windows": sum(1 for value in returns if value > 0.0),
                "avg_delta_max_drawdown_pct": _mean_or_none(drawdowns),
                "drawdown_improved_windows": sum(
                    1 for value in drawdowns if value < 0.0
                ),
            }
        )

    return {
        "profile_id": TOP2_SOFT_TARGET_SCALE_PROFILE_ID,
        "baseline_type": "controlled_exposure_proxy",
        "research_only": True,
        "runtime_wiring_approved": False,
        "scenario_id": "trend_rank_proxy",
        "overlay_mode": "target_scale",
        "max_target_pairs": 2,
        "neutral_allocation_multiplier": 0.75,
        "risk_off_allocation_multiplier": 0.25,
        "fee_bps": 25.0,
        "timeframe": timeframe,
        "rows": rows,
        "groups": groups,
    }


def evaluate_market_regime_exposure_scenarios(
    bars_by_pair: Mapping[str, Sequence[OHLCBar]],
    *,
    start: datetime,
    end: datetime,
    pairs: Sequence[str],
    regime_params: MarketRegimeOverlayParams,
    scenario_params: MarketRegimeExposureScenarioParams,
    scenarios: Sequence[str] | None = None,
    overlay_modes: Sequence[str] | None = None,
    preflight: dict[str, Any] | None = None,
) -> MarketRegimeExposureResearchResult:
    start = _as_utc(start)
    end = _as_utc(end)
    cleaned = {pair: _sort_bars(bars) for pair, bars in bars_by_pair.items()}
    selected_scenarios = _validate_requested_values(
        scenarios or DEFAULT_EXPOSURE_SCENARIOS,
        SUPPORTED_EXPOSURE_SCENARIOS,
        label="scenario",
    )
    selected_overlay_modes = _validate_requested_values(
        overlay_modes or DEFAULT_EXPOSURE_OVERLAY_MODES,
        SUPPORTED_EXPOSURE_OVERLAY_MODES,
        label="overlay mode",
    )
    price_maps = _price_maps(cleaned)
    timeline = _common_timeline(
        price_maps,
        pairs=[pair for pair in pairs if pair in price_maps],
        start=start,
        end=end,
    )
    if not timeline:
        raise ValueError("No common exposure-scenario bars were available")

    snapshots = {
        ts: classify_market_regime_snapshot(
            cleaned,
            timestamp=ts,
            params=regime_params,
        )
        for ts in timeline
    }

    runs: list[dict[str, Any]] = []
    comparisons: list[dict[str, Any]] = []
    for scenario_id in selected_scenarios:
        target_pairs = _scenario_target_pairs(
            scenario_id,
            pairs=pairs,
            benchmark_pair=regime_params.benchmark_pair,
        )
        baseline = _simulate_exposure_run(
            scenario_id=scenario_id,
            overlay_mode="none",
            target_pairs=target_pairs,
            price_maps=price_maps,
            timeline=timeline,
            snapshots=snapshots,
            scenario_params=scenario_params,
        )
        runs.append(baseline)
        for overlay_mode in selected_overlay_modes:
            overlay = _simulate_exposure_run(
                scenario_id=scenario_id,
                overlay_mode=overlay_mode,
                target_pairs=target_pairs,
                price_maps=price_maps,
                timeline=timeline,
                snapshots=snapshots,
                scenario_params=scenario_params,
            )
            runs.append(overlay)
            comparisons.append(_compare_exposure_runs(baseline, overlay))

    summary = _summarize_exposure_research(
        start=start,
        end=end,
        pairs=list(pairs),
        regime_params=regime_params,
        scenario_params=scenario_params,
        scenarios=selected_scenarios,
        overlay_modes=selected_overlay_modes,
        timeline=timeline,
        snapshots=snapshots,
        comparisons=comparisons,
    )
    return MarketRegimeExposureResearchResult(
        generated_at=datetime.now(UTC),
        start=start,
        end=end,
        pairs=list(pairs),
        regime_params=regime_params,
        scenario_params=scenario_params,
        summary=summary,
        preflight=copy.deepcopy(preflight),
        runs=runs,
        comparisons=comparisons,
    )


def evaluate_defensive_baseline_report(
    bars_by_pair: Mapping[str, Sequence[OHLCBar]],
    *,
    start: datetime,
    end: datetime,
    pairs: Sequence[str],
    regime_params: MarketRegimeOverlayParams,
    scenario_params: MarketRegimeExposureScenarioParams,
    window_sets: Mapping[str, Sequence[tuple[str, str, str]]] | None = None,
    preflight: dict[str, Any] | None = None,
    strict_data_requested: bool = False,
    strict_data_details: Sequence[str] | None = None,
    end_was_inferred: bool = False,
    rebalance_delta_pct: float = DEFAULT_DEFENSIVE_BASELINE_REBALANCE_DELTA_PCT,
) -> DefensiveBaselineReportResult:
    cleaned = {pair: _sort_bars(bars) for pair, bars in bars_by_pair.items()}
    selected_pairs = _clean_pairs(list(pairs))
    price_maps = _price_maps(cleaned)
    primary_timeline = _common_timeline(
        price_maps,
        pairs=[pair for pair in selected_pairs if pair in price_maps],
        start=_as_utc(start),
        end=_as_utc(end),
    )
    if not primary_timeline:
        raise ValueError("No common defensive-baseline bars were available")

    from .evidence_windows import EVIDENCE_WINDOW_SET_TUPLES

    selected_window_sets = window_sets or {
        DEFAULT_DEFENSIVE_BASELINE_WINDOW_SET: EVIDENCE_WINDOW_SET_TUPLES[
            DEFAULT_DEFENSIVE_BASELINE_WINDOW_SET
        ]
    }

    primary = _evaluate_defensive_span(
        cleaned,
        price_maps=price_maps,
        timeline=primary_timeline,
        pairs=selected_pairs,
        regime_params=regime_params,
        scenario_params=scenario_params,
        span_id="primary_continuous",
        span_label="primary_continuous",
        rebalance_delta_pct=rebalance_delta_pct,
    )

    regime_results: list[dict[str, Any]] = []
    for window_set, windows in selected_window_sets.items():
        for window_id, start_text, end_text in windows:
            window_start = _parse_utc(start_text)
            window_end = _parse_utc(end_text)
            window_timeline = [
                ts
                for ts in primary_timeline
                if int(window_start.timestamp()) <= ts <= int(window_end.timestamp())
            ]
            if not window_timeline:
                regime_results.append(
                    {
                        "window_set": window_set,
                        "window_id": window_id,
                        "start": window_start.isoformat(),
                        "end": window_end.isoformat(),
                        "status": "insufficient_data",
                        "reason": "no common bars in window",
                    }
                )
                continue
            window = _evaluate_defensive_span(
                cleaned,
                price_maps=price_maps,
                timeline=window_timeline,
                pairs=selected_pairs,
                regime_params=regime_params,
                scenario_params=scenario_params,
                span_id=f"{window_set}:{window_id}",
                span_label=window_id,
                rebalance_delta_pct=rebalance_delta_pct,
            )
            window["window_set"] = window_set
            window["window_id"] = window_id
            regime_results.append(window)

    reported_strict_details = (
        [] if end_was_inferred else list(strict_data_details or [])
    )
    verdict = _defensive_baseline_verdict(primary)
    summary = {
        "start_requested": _as_utc(start).isoformat(),
        "end_requested": _as_utc(end).isoformat(),
        "end_was_inferred": bool(end_was_inferred),
        "actual_start": primary["actual_start"],
        "actual_end": primary["actual_end"],
        "pairs": selected_pairs,
        "timeframe": regime_params.timeframe,
        "strict_data_requested": bool(strict_data_requested),
        "strict_data_ready": not bool(reported_strict_details),
        "strict_data_details": reported_strict_details,
        "rebalance_delta_pct": float(rebalance_delta_pct),
        "fee_bps": float(scenario_params.fee_bps),
        "one_way_all_in_cost_bps": float(scenario_params.fee_bps),
        "cost_model_note": (
            "fee_bps is used as a one-way all-in cost proxy; no separate "
            "slippage model is applied in this module."
        ),
        "verdict": verdict,
    }
    return DefensiveBaselineReportResult(
        generated_at=datetime.now(UTC),
        summary=summary,
        preflight=copy.deepcopy(preflight),
        primary_continuous_span=primary,
        regime_window_results=regime_results,
        static_exposure_frontier=copy.deepcopy(primary["static_exposure_frontier"]),
        matched_exposure_comparisons=copy.deepcopy(
            primary["matched_exposure_comparisons"]
        ),
        lever_attribution=_build_lever_attribution(primary),
    )


def _evaluate_defensive_span(
    bars_by_pair: Mapping[str, Sequence[OHLCBar]],
    *,
    price_maps: Mapping[str, Mapping[int, float]],
    timeline: Sequence[int],
    pairs: Sequence[str],
    regime_params: MarketRegimeOverlayParams,
    scenario_params: MarketRegimeExposureScenarioParams,
    span_id: str,
    span_label: str,
    rebalance_delta_pct: float,
) -> dict[str, Any]:
    snapshots = {
        ts: classify_market_regime_snapshot(
            bars_by_pair,
            timestamp=ts,
            params=regime_params,
        )
        for ts in timeline
    }
    runs: list[dict[str, Any]] = []
    static_frontier: list[dict[str, Any]] = []

    for exposure_pct in DEFAULT_STATIC_EXPOSURE_FRONTIER_PCTS:
        run = _simulate_defensive_run(
            run_id=f"static_equal_weight_{exposure_pct:g}",
            run_group="static_frontier",
            construction_mode="equal_weight",
            timing_mode="none",
            target_pairs=list(pairs),
            price_maps=price_maps,
            timeline=timeline,
            snapshots=snapshots,
            scenario_params=scenario_params,
            allocation_pct=float(exposure_pct),
            rebalance_delta_pct=rebalance_delta_pct,
        )
        static_frontier.append(_defensive_run_summary_slice(run))
        runs.append(run)

    core_specs = [
        ("btc_only", "construction", "btc_only", "none"),
        ("equal_weight_basket", "construction", "equal_weight", "none"),
        ("inverse_vol_weight", "construction", "inverse_vol_weight", "none"),
        ("equal_weight_ewma_vol_target", "timing", "equal_weight", "ewma_vol_target"),
        (
            "inverse_vol_ewma_vol_target",
            "combined",
            "inverse_vol_weight",
            "ewma_vol_target",
        ),
        (
            "equal_weight_momentum_risk_off",
            "timing",
            "equal_weight",
            "momentum_risk_off",
        ),
        (
            "trend_rank_target_scale",
            "combined",
            "trend_rank_top2",
            "target_scale",
        ),
    ]
    for run_id, run_group, construction_mode, timing_mode in core_specs:
        runs.append(
            _simulate_defensive_run(
                run_id=run_id,
                run_group=run_group,
                construction_mode=construction_mode,
                timing_mode=timing_mode,
                target_pairs=list(pairs),
                price_maps=price_maps,
                timeline=timeline,
                snapshots=snapshots,
                scenario_params=scenario_params,
                allocation_pct=float(scenario_params.allocation_pct),
                rebalance_delta_pct=rebalance_delta_pct,
            )
        )

    matched: list[dict[str, Any]] = []
    for run in runs:
        if run["run_group"] not in {"timing", "combined"}:
            continue
        matched_allocation = max(min(float(run["avg_exposure_pct"]), 100.0), 0.0)
        static = _simulate_defensive_run(
            run_id=f"{run['run_id']}_matched_static",
            run_group="matched_static",
            construction_mode=str(run["construction_mode"]),
            timing_mode="none",
            target_pairs=list(pairs),
            price_maps=price_maps,
            timeline=timeline,
            snapshots=snapshots,
            scenario_params=scenario_params,
            allocation_pct=matched_allocation,
            rebalance_delta_pct=rebalance_delta_pct,
        )
        matched.append(_compare_matched_static(run, static))

    return {
        "span_id": span_id,
        "span_label": span_label,
        "status": "ready",
        "actual_start": datetime.fromtimestamp(int(timeline[0]), tz=UTC).isoformat(),
        "actual_end": datetime.fromtimestamp(int(timeline[-1]), tz=UTC).isoformat(),
        "bar_count": len(timeline),
        "continuity": _timeline_continuity(timeline, regime_params.timeframe),
        "state_counts": dict(
            sorted(Counter(snapshot.regime for snapshot in snapshots.values()).items())
        ),
        "runs": [_defensive_run_summary_slice(run) for run in runs],
        "static_exposure_frontier": static_frontier,
        "matched_exposure_comparisons": matched,
    }


def _simulate_defensive_run(
    *,
    run_id: str,
    run_group: str,
    construction_mode: str,
    timing_mode: str,
    target_pairs: Sequence[str],
    price_maps: Mapping[str, Mapping[int, float]],
    timeline: Sequence[int],
    snapshots: Mapping[int, MarketRegimeSnapshot],
    scenario_params: MarketRegimeExposureScenarioParams,
    allocation_pct: float,
    rebalance_delta_pct: float,
) -> dict[str, Any]:
    portfolio = _ScenarioPortfolio(
        cash_usd=float(scenario_params.starting_cash_usd),
        holdings={pair: 0.0 for pair in target_pairs},
    )
    equity_curve: list[float] = []
    exposure_curve: list[float] = []
    trades = 0
    fees_usd = 0.0
    rebalance_count = 0
    skipped_rebalances = 0
    target_selection_counts: Counter[str] = Counter()

    for index, ts in enumerate(timeline):
        prices = {pair: float(price_maps[pair][ts]) for pair in target_pairs}
        if index % int(scenario_params.rebalance_interval_bars) == 0:
            rebalance_count += 1
            decision_index = index - 1
            equity = _portfolio_equity(portfolio, prices)
            if decision_index < 0:
                target_weights: dict[str, float] = {}
            else:
                target_weights = _defensive_target_weights(
                    construction_mode,
                    target_pairs=target_pairs,
                    price_maps=price_maps,
                    timeline=timeline,
                    index=decision_index,
                    scenario_params=scenario_params,
                    allocation_pct=allocation_pct,
                )
                multiplier = _defensive_timing_multiplier(
                    timing_mode,
                    price_maps=price_maps,
                    timeline=timeline,
                    index=decision_index,
                    snapshots=snapshots,
                    scenario_params=scenario_params,
                )
                target_weights = {
                    pair: weight * multiplier for pair, weight in target_weights.items()
                }
            if not target_weights:
                skipped_rebalances += 1
            target_selection_counts.update(target_weights.keys())
            plan = _target_plan(
                scenario_id=construction_mode,
                overlay_mode=timing_mode,
                timestamp=ts,
                portfolio=portfolio,
                prices=prices,
                target_weights=target_weights,
                equity_usd=equity,
                rebalance_delta_pct=rebalance_delta_pct,
            )
            executed = _execute_plan(
                portfolio,
                plan,
                prices,
                fee_bps=float(scenario_params.fee_bps),
            )
            trades += executed["trades"]
            fees_usd += executed["fees_usd"]

        equity = _portfolio_equity(portfolio, prices)
        exposure = _portfolio_exposure(portfolio, prices)
        equity_curve.append(equity)
        exposure_curve.append((exposure / equity) * 100.0 if equity > 0.0 else 0.0)

    return _defensive_run_payload(
        run_id=run_id,
        run_group=run_group,
        construction_mode=construction_mode,
        timing_mode=timing_mode,
        allocation_pct=allocation_pct,
        scenario_params=scenario_params,
        equity_curve=equity_curve,
        exposure_curve=exposure_curve,
        trades=trades,
        fees_usd=fees_usd,
        rebalance_count=rebalance_count,
        skipped_rebalances=skipped_rebalances,
        target_selection_counts=target_selection_counts,
    )


def _defensive_target_weights(
    construction_mode: str,
    *,
    target_pairs: Sequence[str],
    price_maps: Mapping[str, Mapping[int, float]],
    timeline: Sequence[int],
    index: int,
    scenario_params: MarketRegimeExposureScenarioParams,
    allocation_pct: float,
) -> dict[str, float]:
    if allocation_pct <= 0.0:
        return {}
    if construction_mode == "btc_only":
        benchmark = "BTC/USD" if "BTC/USD" in target_pairs else target_pairs[0]
        return {benchmark: float(allocation_pct) / 100.0}
    if construction_mode == "equal_weight":
        return _equal_target_weights(target_pairs, allocation_pct=allocation_pct)
    if construction_mode == "inverse_vol_weight":
        vols = {
            pair: _realized_volatility_pct(
                price_maps.get(pair, {}),
                timeline=timeline,
                index=index,
                lookback=int(scenario_params.target_lookback_bars),
            )
            for pair in target_pairs
        }
        usable = {
            pair: vol for pair, vol in vols.items() if vol is not None and vol > 0
        }
        if not usable:
            return _equal_target_weights(target_pairs, allocation_pct=allocation_pct)
        inv = {pair: 1.0 / float(vol) for pair, vol in usable.items()}
        total = sum(inv.values())
        allocation = float(allocation_pct) / 100.0
        return {pair: allocation * (value / total) for pair, value in inv.items()}
    if construction_mode == "trend_rank_top2":
        params = MarketRegimeExposureScenarioParams(
            allocation_pct=float(allocation_pct),
            rebalance_interval_bars=int(scenario_params.rebalance_interval_bars),
            starting_cash_usd=float(scenario_params.starting_cash_usd),
            fee_bps=float(scenario_params.fee_bps),
            target_lookback_bars=int(scenario_params.target_lookback_bars),
            min_momentum_bps=float(scenario_params.min_momentum_bps),
            max_target_pairs=min(2, int(scenario_params.max_target_pairs)),
        )
        return _trend_rank_proxy_target_weights(
            target_pairs,
            price_maps=price_maps,
            timeline=timeline,
            index=index,
            scenario_params=params,
        )
    raise ValueError(f"Unsupported defensive construction mode: {construction_mode}")


def _defensive_timing_multiplier(
    timing_mode: str,
    *,
    price_maps: Mapping[str, Mapping[int, float]],
    timeline: Sequence[int],
    index: int,
    snapshots: Mapping[int, MarketRegimeSnapshot],
    scenario_params: MarketRegimeExposureScenarioParams,
) -> float:
    if timing_mode == "none":
        return 1.0
    if timing_mode == "target_scale":
        snapshot = snapshots.get(timeline[index])
        return float(snapshot.allocation_multiplier) if snapshot else 1.0
    if timing_mode == "momentum_risk_off":
        benchmark = "BTC/USD" if "BTC/USD" in price_maps else next(iter(price_maps))
        benchmark_momentum = _momentum_bps_at(
            price_maps.get(benchmark, {}),
            timeline=timeline,
            index=index,
            lookback=int(scenario_params.target_lookback_bars),
            allow_partial_lookback=False,
        )
        basket_values = [
            _momentum_bps_at(
                price_maps.get(pair, {}),
                timeline=timeline,
                index=index,
                lookback=int(scenario_params.target_lookback_bars),
                allow_partial_lookback=False,
            )
            for pair in price_maps
        ]
        basket = [value for value in basket_values if value is not None]
        basket_momentum = mean(basket) if basket else None
        if benchmark_momentum is None or basket_momentum is None:
            return 1.0
        if benchmark_momentum <= 0.0 or basket_momentum <= 0.0:
            return 0.25
        if benchmark_momentum < float(
            scenario_params.min_momentum_bps
        ) or basket_momentum < float(scenario_params.min_momentum_bps):
            return 0.75
        return 1.0
    if timing_mode == "ewma_vol_target":
        benchmark = "BTC/USD" if "BTC/USD" in price_maps else next(iter(price_maps))
        vol = _realized_volatility_pct(
            price_maps.get(benchmark, {}),
            timeline=timeline,
            index=index,
            lookback=int(scenario_params.target_lookback_bars),
        )
        target_vol_pct = 25.0
        if vol is None or vol <= 0.0:
            return 1.0
        return max(min(target_vol_pct / float(vol), 1.0), 0.0)
    raise ValueError(f"Unsupported defensive timing mode: {timing_mode}")


def _realized_volatility_pct(
    price_map: Mapping[int, float],
    *,
    timeline: Sequence[int],
    index: int,
    lookback: int,
) -> float | None:
    if index <= 0:
        return None
    start = max(1, index - int(lookback) + 1)
    returns: list[float] = []
    for pos in range(start, index + 1):
        prev_price = float(price_map.get(timeline[pos - 1], 0.0) or 0.0)
        price = float(price_map.get(timeline[pos], 0.0) or 0.0)
        if prev_price <= 0.0 or price <= 0.0:
            continue
        returns.append((price - prev_price) / prev_price)
    if len(returns) < 2:
        return None
    periods_per_year = _periods_per_year_from_timeline(timeline)
    return pstdev(returns) * math.sqrt(periods_per_year) * 100.0


def _defensive_run_payload(
    *,
    run_id: str,
    run_group: str,
    construction_mode: str,
    timing_mode: str,
    allocation_pct: float,
    scenario_params: MarketRegimeExposureScenarioParams,
    equity_curve: Sequence[float],
    exposure_curve: Sequence[float],
    trades: int,
    fees_usd: float,
    rebalance_count: int,
    skipped_rebalances: int,
    target_selection_counts: Counter[str],
) -> dict[str, Any]:
    ending_equity = float(equity_curve[-1]) if equity_curve else 0.0
    returns = _period_returns(equity_curve)
    recovery = _drawdown_recovery(equity_curve)
    return {
        "run_id": run_id,
        "run_group": run_group,
        "construction_mode": construction_mode,
        "timing_mode": timing_mode,
        "allocation_pct": float(allocation_pct),
        "starting_cash_usd": float(scenario_params.starting_cash_usd),
        "ending_equity_usd": ending_equity,
        "return_pct": (
            (
                (ending_equity - scenario_params.starting_cash_usd)
                / scenario_params.starting_cash_usd
            )
            * 100.0
            if scenario_params.starting_cash_usd > 0
            else 0.0
        ),
        "max_drawdown_pct": _max_drawdown_pct(equity_curve),
        "downside_volatility_pct": _downside_volatility_pct(returns),
        "expected_shortfall_pct": _expected_shortfall_pct(returns),
        "max_recovery_bars": recovery["max_recovery_bars"],
        "unrecovered_drawdown": recovery["unrecovered_drawdown"],
        "trades": int(trades),
        "fees_usd": float(fees_usd),
        "cost_drag_pct": (
            (float(fees_usd) / scenario_params.starting_cash_usd) * 100.0
            if scenario_params.starting_cash_usd > 0
            else 0.0
        ),
        "rebalance_count": int(rebalance_count),
        "skipped_rebalances": int(skipped_rebalances),
        "avg_exposure_pct": mean(exposure_curve) if exposure_curve else 0.0,
        "max_exposure_pct": max(exposure_curve) if exposure_curve else 0.0,
        "min_exposure_pct": min(exposure_curve) if exposure_curve else 0.0,
        "target_selection_counts": dict(sorted(target_selection_counts.items())),
    }


def _defensive_run_summary_slice(run: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "run_id",
        "run_group",
        "construction_mode",
        "timing_mode",
        "allocation_pct",
        "return_pct",
        "max_drawdown_pct",
        "downside_volatility_pct",
        "expected_shortfall_pct",
        "max_recovery_bars",
        "unrecovered_drawdown",
        "trades",
        "fees_usd",
        "cost_drag_pct",
        "rebalance_count",
        "skipped_rebalances",
        "avg_exposure_pct",
        "max_exposure_pct",
        "min_exposure_pct",
        "target_selection_counts",
    )
    return {key: copy.deepcopy(run.get(key)) for key in keys}


def _compare_matched_static(
    dynamic: Mapping[str, Any],
    static: Mapping[str, Any],
) -> dict[str, Any]:
    drawdown_delta = float(dynamic["max_drawdown_pct"]) - float(
        static["max_drawdown_pct"]
    )
    return_delta = float(dynamic["return_pct"]) - float(static["return_pct"])
    downside_delta = float(dynamic["downside_volatility_pct"]) - float(
        static["downside_volatility_pct"]
    )
    static_drawdown = float(static["max_drawdown_pct"])
    drawdown_improvement_pct = (
        ((static_drawdown - float(dynamic["max_drawdown_pct"])) / static_drawdown)
        * 100.0
        if static_drawdown > 0.0
        else 0.0
    )
    useful = (drawdown_improvement_pct >= 10.0 and return_delta >= -0.25) or (
        return_delta > 0.0 and drawdown_delta <= 0.0 and downside_delta <= 0.0
    )
    return {
        "run_id": dynamic["run_id"],
        "matched_static_run_id": static["run_id"],
        "construction_mode": dynamic["construction_mode"],
        "timing_mode": dynamic["timing_mode"],
        "dynamic": _defensive_run_summary_slice(dynamic),
        "matched_static": _defensive_run_summary_slice(static),
        "delta": {
            "return_pct": return_delta,
            "max_drawdown_pct": drawdown_delta,
            "downside_volatility_pct": downside_delta,
            "fees_usd": float(dynamic["fees_usd"]) - float(static["fees_usd"]),
            "cost_drag_pct": float(dynamic["cost_drag_pct"])
            - float(static["cost_drag_pct"]),
        },
        "drawdown_improvement_pct": drawdown_improvement_pct,
        "matched_exposure_gate": {
            "passed": useful,
            "required_drawdown_improvement_pct": 10.0,
            "max_allowed_return_degradation_pct": 0.25,
        },
    }


def _defensive_baseline_verdict(span: Mapping[str, Any]) -> dict[str, Any]:
    matched = list(span.get("matched_exposure_comparisons") or [])
    if not matched:
        return {
            "status": "insufficient_data",
            "reasons": ["no matched exposure comparisons were produced"],
        }
    passing = [
        item
        for item in matched
        if bool(item.get("matched_exposure_gate", {}).get("passed"))
    ]
    if passing:
        return {
            "status": "baseline_useful",
            "passing_runs": [item["run_id"] for item in passing],
            "reasons": [
                "at least one dynamic defensive baseline beat matched static exposure"
            ],
        }
    drawdown_only = [
        item
        for item in matched
        if float(item.get("drawdown_improvement_pct", 0.0) or 0.0) > 0.0
    ]
    if drawdown_only:
        return {
            "status": "risk_control_tradeoff",
            "reasons": [
                "some overlays reduced drawdown but did not beat matched static exposure"
            ],
        }
    return {
        "status": "not_useful",
        "reasons": ["dynamic overlays did not improve matched static exposure"],
    }


def _build_lever_attribution(span: Mapping[str, Any]) -> dict[str, Any]:
    runs = list(span.get("runs") or [])

    def _best(group: str, key: str, reverse: bool) -> dict[str, Any] | None:
        items = [run for run in runs if run.get("run_group") == group]
        if not items:
            return None
        selected = sorted(
            items,
            key=lambda item: float(item.get(key, 0.0) or 0.0),
            reverse=reverse,
        )[0]
        return _defensive_run_summary_slice(selected)

    return {
        "best_construction_by_return": _best("construction", "return_pct", True),
        "best_construction_by_drawdown": _best(
            "construction", "max_drawdown_pct", False
        ),
        "best_timing_by_return": _best("timing", "return_pct", True),
        "best_timing_by_drawdown": _best("timing", "max_drawdown_pct", False),
        "best_combined_by_return": _best("combined", "return_pct", True),
        "best_combined_by_drawdown": _best("combined", "max_drawdown_pct", False),
    }


def _period_returns(equity_curve: Sequence[float]) -> list[float]:
    returns: list[float] = []
    for previous, current in zip(equity_curve, equity_curve[1:]):
        if float(previous) <= 0.0:
            continue
        returns.append((float(current) - float(previous)) / float(previous))
    return returns


def _downside_volatility_pct(returns: Sequence[float]) -> float:
    downside = [min(float(value), 0.0) for value in returns]
    if len(downside) < 2:
        return 0.0
    return pstdev(downside) * math.sqrt(365.0 * 6.0) * 100.0


def _expected_shortfall_pct(returns: Sequence[float]) -> float:
    if not returns:
        return 0.0
    losses = sorted(float(value) * 100.0 for value in returns)
    count = max(1, math.ceil(len(losses) * 0.05))
    return abs(mean(losses[:count]))


def _drawdown_recovery(equity_curve: Sequence[float]) -> dict[str, Any]:
    peak = 0.0
    peak_index = 0
    max_recovery = 0
    unrecovered = False
    for index, equity in enumerate(equity_curve):
        value = float(equity)
        if value >= peak:
            if peak > 0.0:
                max_recovery = max(max_recovery, index - peak_index)
            peak = value
            peak_index = index
            unrecovered = False
        elif peak > 0.0:
            unrecovered = True
    if unrecovered:
        max_recovery = max(max_recovery, len(equity_curve) - 1 - peak_index)
    return {"max_recovery_bars": max_recovery, "unrecovered_drawdown": unrecovered}


def _timeline_continuity(timeline: Sequence[int], timeframe: str) -> dict[str, Any]:
    expected = _timeframe_seconds(timeframe)
    gaps = 0
    max_gap = 0
    for previous, current in zip(timeline, timeline[1:]):
        gap = int(current) - int(previous)
        max_gap = max(max_gap, gap)
        if expected > 0 and gap != expected:
            gaps += 1
    return {
        "status": "continuous" if gaps == 0 else "has_gaps",
        "gap_count": gaps,
        "expected_interval_seconds": expected,
        "max_observed_gap_seconds": max_gap,
    }


def _periods_per_year_from_timeline(timeline: Sequence[int]) -> float:
    if len(timeline) >= 2:
        interval = max(int(timeline[1]) - int(timeline[0]), 1)
    else:
        interval = _timeframe_seconds("4h")
    return (365.0 * 24.0 * 60.0 * 60.0) / float(interval)


def _validate_requested_values(
    values: Sequence[str],
    supported: frozenset[str],
    *,
    label: str,
) -> list[str]:
    cleaned: list[str] = []
    for value in values:
        item = str(value).strip()
        if item not in supported:
            raise ValueError(
                f"Unsupported {label}: {item}. "
                f"Supported values: {', '.join(sorted(supported))}"
            )
        if item not in cleaned:
            cleaned.append(item)
    return cleaned


def _price_maps(
    bars_by_pair: Mapping[str, Sequence[OHLCBar]],
) -> dict[str, dict[int, float]]:
    return {
        pair: {
            int(bar.timestamp): float(bar.close)
            for bar in bars
            if float(bar.close) > 0.0
        }
        for pair, bars in bars_by_pair.items()
    }


def _common_timeline(
    price_maps: Mapping[str, Mapping[int, float]],
    *,
    pairs: Sequence[str],
    start: datetime,
    end: datetime,
) -> list[int]:
    if not pairs:
        return []
    common: set[int] | None = None
    for pair in pairs:
        timestamps = set(price_maps.get(pair, {}))
        common = timestamps if common is None else common.intersection(timestamps)
    if common is None:
        return []
    start_ts = int(start.timestamp())
    end_ts = int(end.timestamp())
    return sorted(ts for ts in common if start_ts <= ts <= end_ts)


def _scenario_target_pairs(
    scenario_id: str,
    *,
    pairs: Sequence[str],
    benchmark_pair: str,
) -> list[str]:
    cleaned_pairs = _clean_pairs(pairs)
    if scenario_id == "starter_equal_weight":
        target_pairs = cleaned_pairs
    elif scenario_id == "btc_only":
        target_pairs = [benchmark_pair]
    elif scenario_id == "alt_equal_weight":
        target_pairs = [pair for pair in cleaned_pairs if pair != benchmark_pair]
    elif scenario_id in {"trend_proxy", "trend_rank_proxy"}:
        target_pairs = cleaned_pairs
    else:
        raise ValueError(f"Unsupported scenario: {scenario_id}")
    if not target_pairs:
        raise ValueError(f"Scenario {scenario_id} has no target pairs")
    return target_pairs


def _simulate_exposure_run(
    *,
    scenario_id: str,
    overlay_mode: str,
    target_pairs: Sequence[str],
    price_maps: Mapping[str, Mapping[int, float]],
    timeline: Sequence[int],
    snapshots: Mapping[int, MarketRegimeSnapshot],
    scenario_params: MarketRegimeExposureScenarioParams,
) -> dict[str, Any]:
    portfolio = _ScenarioPortfolio(
        cash_usd=float(scenario_params.starting_cash_usd),
        holdings={pair: 0.0 for pair in target_pairs},
    )
    equity_curve: list[float] = []
    exposure_curve: list[float] = []
    state_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    trades = 0
    fees_usd = 0.0
    overlay_blocks = 0
    overlay_clamps = 0
    overlay_target_reductions = 0
    rebalance_count = 0
    cash_target_rebalances = 0
    target_selection_counts: Counter[str] = Counter()

    for index, ts in enumerate(timeline):
        prices = {pair: float(price_maps[pair][ts]) for pair in target_pairs}
        snapshot = snapshots[ts]
        state_counts[snapshot.regime] += 1
        reason_counts.update(snapshot.reason_codes)

        if index % int(scenario_params.rebalance_interval_bars) == 0:
            rebalance_count += 1
            equity = _portfolio_equity(portfolio, prices)
            base_weights = _scenario_target_weights(
                scenario_id,
                target_pairs=target_pairs,
                price_maps=price_maps,
                timeline=timeline,
                index=index,
                scenario_params=scenario_params,
            )
            if not base_weights:
                cash_target_rebalances += 1
            target_selection_counts.update(base_weights.keys())
            target_weights = dict(base_weights)
            if overlay_mode == "target_scale":
                multiplier = float(snapshot.allocation_multiplier)
                target_weights = {
                    pair: weight * multiplier for pair, weight in base_weights.items()
                }
                overlay_target_reductions += sum(
                    1
                    for pair, weight in base_weights.items()
                    if float(weight) > float(target_weights.get(pair, 0.0))
                )
            plan = _target_plan(
                scenario_id=scenario_id,
                overlay_mode=overlay_mode,
                timestamp=ts,
                portfolio=portfolio,
                prices=prices,
                target_weights=target_weights,
                equity_usd=equity,
            )
            if overlay_mode == "entry_guard":
                plan = apply_market_regime_overlay_to_plan(plan, snapshot)
                overlay_payload = plan.metadata.get("market_regime_overlay", {})
                overlay_blocks += int(
                    overlay_payload.get("overlay_blocked_actions", 0) or 0
                )
                overlay_clamps += int(
                    overlay_payload.get("overlay_clamped_actions", 0) or 0
                )
            executed = _execute_plan(
                portfolio,
                plan,
                prices,
                fee_bps=float(scenario_params.fee_bps),
            )
            trades += executed["trades"]
            fees_usd += executed["fees_usd"]

        equity = _portfolio_equity(portfolio, prices)
        exposure = _portfolio_exposure(portfolio, prices)
        equity_curve.append(equity)
        exposure_curve.append((exposure / equity) * 100.0 if equity > 0.0 else 0.0)

    ending_equity = equity_curve[-1]
    active_cycles = sum(1 for exposure in exposure_curve if exposure > 0.01)
    cash_cycles = len(exposure_curve) - active_cycles
    return {
        "scenario_id": scenario_id,
        "overlay_mode": overlay_mode,
        "target_pairs": list(target_pairs),
        "allocation_pct": scenario_params.allocation_pct,
        "rebalance_interval_bars": scenario_params.rebalance_interval_bars,
        "starting_cash_usd": scenario_params.starting_cash_usd,
        "ending_equity_usd": ending_equity,
        "return_pct": (
            (ending_equity - scenario_params.starting_cash_usd)
            / scenario_params.starting_cash_usd
        )
        * 100.0,
        "max_drawdown_pct": _max_drawdown_pct(equity_curve),
        "trades": trades,
        "fees_usd": fees_usd,
        "rebalance_count": rebalance_count,
        "cash_target_rebalances": cash_target_rebalances,
        "total_cycles": len(equity_curve),
        "active_cycles": active_cycles,
        "cash_cycles": cash_cycles,
        "active_cycle_pct": (
            (active_cycles / len(equity_curve)) * 100.0 if equity_curve else 0.0
        ),
        "avg_exposure_pct": mean(exposure_curve) if exposure_curve else 0.0,
        "max_exposure_pct": max(exposure_curve) if exposure_curve else 0.0,
        "state_counts": dict(sorted(state_counts.items())),
        "reason_counts": dict(reason_counts.most_common()),
        "target_selection_counts": dict(sorted(target_selection_counts.items())),
        "overlay_blocks": overlay_blocks,
        "overlay_clamps": overlay_clamps,
        "overlay_target_reductions": overlay_target_reductions,
        "overlay_interventions": overlay_blocks
        + overlay_clamps
        + overlay_target_reductions,
    }


def _equal_target_weights(
    target_pairs: Sequence[str],
    *,
    allocation_pct: float,
) -> dict[str, float]:
    allocation = float(allocation_pct) / 100.0
    weight = allocation / len(target_pairs)
    return {pair: weight for pair in target_pairs}


def _scenario_target_weights(
    scenario_id: str,
    *,
    target_pairs: Sequence[str],
    price_maps: Mapping[str, Mapping[int, float]],
    timeline: Sequence[int],
    index: int,
    scenario_params: MarketRegimeExposureScenarioParams,
) -> dict[str, float]:
    if scenario_id == "trend_proxy":
        return _trend_proxy_target_weights(
            target_pairs,
            price_maps=price_maps,
            timeline=timeline,
            index=index,
            scenario_params=scenario_params,
        )
    if scenario_id == "trend_rank_proxy":
        return _trend_rank_proxy_target_weights(
            target_pairs,
            price_maps=price_maps,
            timeline=timeline,
            index=index,
            scenario_params=scenario_params,
        )
    return _equal_target_weights(
        target_pairs,
        allocation_pct=scenario_params.allocation_pct,
    )


def _trend_proxy_target_weights(
    target_pairs: Sequence[str],
    *,
    price_maps: Mapping[str, Mapping[int, float]],
    timeline: Sequence[int],
    index: int,
    scenario_params: MarketRegimeExposureScenarioParams,
) -> dict[str, float]:
    scored: list[tuple[str, float]] = []
    for pair in target_pairs:
        momentum = _momentum_bps_at(
            price_maps.get(pair, {}),
            timeline=timeline,
            index=index,
            lookback=int(scenario_params.target_lookback_bars),
            allow_partial_lookback=False,
        )
        if momentum is None or momentum < float(scenario_params.min_momentum_bps):
            continue
        scored.append((pair, momentum))

    scored.sort(key=lambda item: (-item[1], item[0]))
    selected = [pair for pair, _ in scored[: int(scenario_params.max_target_pairs)]]
    if not selected:
        return {}
    return _equal_target_weights(
        selected,
        allocation_pct=scenario_params.allocation_pct,
    )


def _trend_rank_proxy_target_weights(
    target_pairs: Sequence[str],
    *,
    price_maps: Mapping[str, Mapping[int, float]],
    timeline: Sequence[int],
    index: int,
    scenario_params: MarketRegimeExposureScenarioParams,
) -> dict[str, float]:
    scored: list[tuple[str, float]] = []
    for pair in target_pairs:
        momentum = _momentum_bps_at(
            price_maps.get(pair, {}),
            timeline=timeline,
            index=index,
            lookback=int(scenario_params.target_lookback_bars),
            allow_partial_lookback=True,
        )
        if momentum is None:
            continue
        scored.append((pair, momentum))

    scored.sort(key=lambda item: (-item[1], item[0]))
    selected = [pair for pair, _ in scored[: int(scenario_params.max_target_pairs)]]
    if not selected:
        return {}
    return _equal_target_weights(
        selected,
        allocation_pct=scenario_params.allocation_pct,
    )


def _momentum_bps_at(
    price_map: Mapping[int, float],
    *,
    timeline: Sequence[int],
    index: int,
    lookback: int,
    allow_partial_lookback: bool,
) -> float | None:
    actual_lookback = (
        min(int(lookback), index + 1) if allow_partial_lookback else int(lookback)
    )
    if actual_lookback < 2 or index < actual_lookback - 1:
        return None
    start_ts = timeline[index - actual_lookback + 1]
    end_ts = timeline[index]
    start_price = float(price_map.get(start_ts, 0.0) or 0.0)
    end_price = float(price_map.get(end_ts, 0.0) or 0.0)
    if start_price <= 0.0 or end_price <= 0.0:
        return None
    return ((end_price - start_price) / start_price) * 10_000.0


def _target_plan(
    *,
    scenario_id: str,
    overlay_mode: str,
    timestamp: int,
    portfolio: _ScenarioPortfolio,
    prices: Mapping[str, float],
    target_weights: Mapping[str, float],
    equity_usd: float,
    rebalance_delta_pct: float = 0.0,
) -> ExecutionPlan:
    actions: list[RiskAdjustedAction] = []
    for pair in prices:
        target_weight = target_weights.get(pair, 0.0)
        price = float(prices[pair])
        current_base = float(portfolio.holdings.get(pair, 0.0))
        target_notional = max(equity_usd * float(target_weight), 0.0)
        target_base = target_notional / price if price > 0.0 else 0.0
        current_notional = max(current_base * price, 0.0)
        delta_notional = abs(target_notional - current_notional)
        if (
            rebalance_delta_pct > 0.0
            and equity_usd > 0.0
            and (delta_notional / equity_usd) * 100.0 < float(rebalance_delta_pct)
        ):
            target_notional = current_notional
            target_base = current_base
        actions.append(
            RiskAdjustedAction(
                pair=pair,
                strategy_id=f"market_regime_exposure:{scenario_id}:{overlay_mode}",
                action_type=_action_type(current_base, target_base),
                target_base_size=target_base,
                target_notional_usd=target_notional,
                current_base_size=current_base,
                reason="controlled market-regime exposure research target",
                blocked=False,
                blocked_reasons=[],
            )
        )
    return ExecutionPlan(
        plan_id=f"market-regime-exposure-{scenario_id}-{overlay_mode}-{timestamp}",
        generated_at=datetime.fromtimestamp(int(timestamp), tz=UTC),
        actions=actions,
        metadata={
            "research_only": True,
            "scenario_id": scenario_id,
            "overlay_mode": overlay_mode,
        },
    )


def _action_type(current_base: float, target_base: float) -> str:
    threshold = 1e-12
    if abs(target_base - current_base) <= threshold:
        return "none"
    if current_base <= threshold and target_base > threshold:
        return "open"
    if target_base <= threshold and current_base > threshold:
        return "close"
    if target_base > current_base:
        return "increase"
    return "reduce"


def _execute_plan(
    portfolio: _ScenarioPortfolio,
    plan: ExecutionPlan,
    prices: Mapping[str, float],
    *,
    fee_bps: float,
) -> dict[str, Any]:
    trades = 0
    fees_usd = 0.0
    for action in plan.actions:
        if action.blocked or action.action_type == "none":
            continue
        price = float(prices[action.pair])
        current_base = float(portfolio.holdings.get(action.pair, 0.0))
        target_base = max(float(action.target_base_size), 0.0)
        delta_base = target_base - current_base
        trade_notional = abs(delta_base) * price
        if trade_notional <= 1e-8:
            continue
        fee = trade_notional * (float(fee_bps) / 10_000.0)
        if delta_base > 0.0:
            portfolio.cash_usd -= trade_notional + fee
        else:
            portfolio.cash_usd += trade_notional - fee
        portfolio.holdings[action.pair] = target_base
        trades += 1
        fees_usd += fee
    return {"trades": trades, "fees_usd": fees_usd}


def _portfolio_equity(
    portfolio: _ScenarioPortfolio,
    prices: Mapping[str, float],
) -> float:
    return portfolio.cash_usd + _portfolio_exposure(portfolio, prices)


def _portfolio_exposure(
    portfolio: _ScenarioPortfolio,
    prices: Mapping[str, float],
) -> float:
    return sum(
        max(float(base), 0.0) * float(prices[pair])
        for pair, base in portfolio.holdings.items()
    )


def _max_drawdown_pct(equity_curve: Sequence[float]) -> float:
    peak = 0.0
    max_drawdown = 0.0
    for equity in equity_curve:
        peak = max(peak, float(equity))
        if peak <= 0.0:
            continue
        drawdown = ((peak - float(equity)) / peak) * 100.0
        max_drawdown = max(max_drawdown, drawdown)
    return max_drawdown


def _compare_exposure_runs(
    baseline: Mapping[str, Any],
    overlay: Mapping[str, Any],
) -> dict[str, Any]:
    comparison = {
        "scenario_id": baseline["scenario_id"],
        "overlay_mode": overlay["overlay_mode"],
        "baseline": _run_summary_slice(baseline),
        "overlay": _run_summary_slice(overlay),
        "delta": {
            "ending_equity_usd": float(overlay["ending_equity_usd"])
            - float(baseline["ending_equity_usd"]),
            "return_pct": float(overlay["return_pct"]) - float(baseline["return_pct"]),
            "max_drawdown_pct": float(overlay["max_drawdown_pct"])
            - float(baseline["max_drawdown_pct"]),
            "trades": int(overlay["trades"]) - int(baseline["trades"]),
            "fees_usd": float(overlay["fees_usd"]) - float(baseline["fees_usd"]),
            "avg_exposure_pct": float(overlay["avg_exposure_pct"])
            - float(baseline["avg_exposure_pct"]),
        },
        "overlay_interventions": {
            "overlay_blocks": int(overlay["overlay_blocks"]),
            "overlay_clamps": int(overlay["overlay_clamps"]),
            "overlay_target_reductions": int(overlay["overlay_target_reductions"]),
            "overlay_interventions": int(overlay["overlay_interventions"]),
        },
    }
    comparison["promotion_checks"] = _exposure_promotion_checks(comparison)
    return comparison


def _run_summary_slice(run: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "ending_equity_usd": run["ending_equity_usd"],
        "return_pct": run["return_pct"],
        "max_drawdown_pct": run["max_drawdown_pct"],
        "trades": run["trades"],
        "fees_usd": run["fees_usd"],
        "cash_target_rebalances": run["cash_target_rebalances"],
        "active_cycles": run["active_cycles"],
        "cash_cycles": run["cash_cycles"],
        "active_cycle_pct": run["active_cycle_pct"],
        "avg_exposure_pct": run["avg_exposure_pct"],
        "max_exposure_pct": run["max_exposure_pct"],
    }


def _exposure_promotion_checks(comparison: Mapping[str, Any]) -> dict[str, Any]:
    baseline = comparison["baseline"]
    overlay = comparison["overlay"]
    delta = comparison["delta"]
    interventions = comparison["overlay_interventions"]
    return {
        "return_improved": {
            "passed": float(delta["return_pct"]) > 0.0,
            "delta_return_pct": delta["return_pct"],
        },
        "drawdown_improved": {
            "passed": float(delta["max_drawdown_pct"]) < 0.0,
            "delta_max_drawdown_pct": delta["max_drawdown_pct"],
        },
        "baseline_had_exposure": {
            "passed": float(baseline["active_cycle_pct"]) >= 50.0
            and int(baseline["trades"]) >= 2,
            "baseline_active_cycle_pct": baseline["active_cycle_pct"],
            "baseline_trades": baseline["trades"],
        },
        "overlay_not_cash_only": {
            "passed": float(overlay["active_cycle_pct"]) >= 25.0,
            "overlay_active_cycle_pct": overlay["active_cycle_pct"],
        },
        "overlay_did_intervene": {
            "passed": int(interventions["overlay_interventions"]) > 0,
            "overlay_interventions": interventions["overlay_interventions"],
        },
    }


def _summarize_exposure_research(
    *,
    start: datetime,
    end: datetime,
    pairs: list[str],
    regime_params: MarketRegimeOverlayParams,
    scenario_params: MarketRegimeExposureScenarioParams,
    scenarios: Sequence[str],
    overlay_modes: Sequence[str],
    timeline: Sequence[int],
    snapshots: Mapping[int, MarketRegimeSnapshot],
    comparisons: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    state_counts = Counter(snapshot.regime for snapshot in snapshots.values())
    reason_counts: Counter[str] = Counter()
    for snapshot in snapshots.values():
        reason_counts.update(snapshot.reason_codes)
    positive_return = [
        item for item in comparisons if float(item["delta"]["return_pct"]) > 0.0
    ]
    drawdown_improved = [
        item for item in comparisons if float(item["delta"]["max_drawdown_pct"]) < 0.0
    ]
    not_cash_only = [
        item
        for item in comparisons
        if bool(item["promotion_checks"]["overlay_not_cash_only"]["passed"])
    ]
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "pairs": pairs,
        "timeframe": regime_params.timeframe,
        "benchmark_pair": regime_params.benchmark_pair,
        "scenarios": list(scenarios),
        "overlay_modes": list(overlay_modes),
        "regime_params": asdict(regime_params),
        "scenario_params": asdict(scenario_params),
        "total_cycles": len(timeline),
        "state_counts": dict(sorted(state_counts.items())),
        "reason_counts": dict(reason_counts.most_common()),
        "comparison_count": len(comparisons),
        "positive_return_comparisons": len(positive_return),
        "drawdown_improved_comparisons": len(drawdown_improved),
        "not_cash_only_comparisons": len(not_cash_only),
        "best_by_return": _best_comparison(comparisons, key="return_pct"),
        "best_by_drawdown": _best_comparison(comparisons, key="max_drawdown_pct"),
    }


def _best_comparison(
    comparisons: Sequence[Mapping[str, Any]],
    *,
    key: str,
) -> dict[str, Any] | None:
    if not comparisons:
        return None
    if key == "return_pct":
        best = max(comparisons, key=lambda item: float(item["delta"]["return_pct"]))
    elif key == "max_drawdown_pct":
        best = min(
            comparisons, key=lambda item: float(item["delta"]["max_drawdown_pct"])
        )
    else:
        raise ValueError(f"Unsupported comparison key: {key}")
    return {
        "scenario_id": best["scenario_id"],
        "overlay_mode": best["overlay_mode"],
        "delta": copy.deepcopy(best["delta"]),
        "overlay_interventions": copy.deepcopy(best["overlay_interventions"]),
        "promotion_checks": copy.deepcopy(best["promotion_checks"]),
    }


def _parse_utc(value: str) -> datetime:
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _mean_or_none(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)
