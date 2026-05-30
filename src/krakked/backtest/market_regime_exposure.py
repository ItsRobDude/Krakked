"""Controlled exposure scenarios for market-regime overlay research."""

from __future__ import annotations

import copy
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from statistics import mean
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
from .runner import BacktestMarketData

REPORT_TYPE_EXPOSURE_RESEARCH = "market_regime_exposure_research"
REPORT_VERSION = 1
DEFAULT_EXPOSURE_SCENARIOS = (
    "starter_equal_weight",
    "btc_only",
    "alt_equal_weight",
    "trend_proxy",
)
DEFAULT_EXPOSURE_OVERLAY_MODES = ("entry_guard", "target_scale")
SUPPORTED_EXPOSURE_SCENARIOS = frozenset(DEFAULT_EXPOSURE_SCENARIOS)
SUPPORTED_EXPOSURE_OVERLAY_MODES = frozenset(("entry_guard", "target_scale"))


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
    elif scenario_id == "trend_proxy":
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
            target_selection_counts.update(base_weights)
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


def _momentum_bps_at(
    price_map: Mapping[int, float],
    *,
    timeline: Sequence[int],
    index: int,
    lookback: int,
) -> float | None:
    if index < lookback - 1:
        return None
    start_ts = timeline[index - lookback + 1]
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
) -> ExecutionPlan:
    actions: list[RiskAdjustedAction] = []
    for pair in prices:
        target_weight = target_weights.get(pair, 0.0)
        price = float(prices[pair])
        current_base = float(portfolio.holdings.get(pair, 0.0))
        target_notional = max(equity_usd * float(target_weight), 0.0)
        target_base = target_notional / price if price > 0.0 else 0.0
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
