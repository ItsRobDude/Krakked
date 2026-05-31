"""Research-only target-source simulations over cached OHLC."""

from __future__ import annotations

import copy
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from math import sqrt
from statistics import mean
from typing import Any, Mapping, Sequence

from krakked.config import AppConfig
from krakked.market_data.models import OHLCBar
from krakked.strategy.models import ExecutionPlan, RiskAdjustedAction

from .market_regime_overlay import (
    _as_utc,
    _clean_pairs,
    _default_pairs,
    _preflight_to_dict,
    _sort_bars,
    _strict_data_message,
)
from .runner import BacktestMarketData

REPORT_TYPE_TARGET_SOURCE_RESEARCH = "target_source_research"
REPORT_TYPE_TARGET_SOURCE_SWEEP = "target_source_research_sweep"
REPORT_VERSION = 1
DEFAULT_TARGET_SOURCE_SCENARIOS = (
    "rank_top2",
    "dual_momentum_top2",
    "vol_adj_dual_momentum_top2",
    "pullback_vol_adj_top2",
    "oversold_reversion_top1",
    "hybrid_state_source",
)
SUPPORTED_TARGET_SOURCE_SCENARIOS = frozenset(DEFAULT_TARGET_SOURCE_SCENARIOS)
STARTER_TARGET_SOURCE_PAIRS = ("BTC/USD", "ETH/USD", "SOL/USD", "ADA/USD")
SUPPORTED_TARGET_SOURCE_TIMEFRAMES = frozenset(("4h",))
DEFENSIVE_ONLY_TARGET_SOURCE_SCENARIOS = frozenset(("oversold_reversion_top1",))
BASELINE_TARGET_SOURCE_SCENARIO = "rank_top2"
CURRENT_ROLLING_WINDOW_ID = "20260510-20260530"
NEAR_FLAT_RETURN_PCT = -0.10


@dataclass(frozen=True)
class TargetSourceResearchParams:
    allocation_pct: float = 20.0
    timeframe: str = "4h"
    rebalance_interval_bars: int = 6
    starting_cash_usd: float = 10_000.0
    fee_bps: float = 25.0
    long_lookback_bars: int = 63
    short_lookback_bars: int = 21
    pullback_lookback_bars: int = 6
    max_target_pairs: int = 2
    pullback_overextension_bps: float = 350.0
    oversold_threshold_bps: float = 250.0
    hybrid_risk_on_benchmark_momentum_bps: float = 0.0
    hybrid_risk_on_basket_momentum_bps: float = 0.0

    def __post_init__(self) -> None:
        if self.allocation_pct <= 0.0 or self.allocation_pct > 100.0:
            raise ValueError("allocation_pct must be greater than 0 and at most 100")
        if self.timeframe not in SUPPORTED_TARGET_SOURCE_TIMEFRAMES:
            raise ValueError(
                "timeframe must be one of "
                f"{', '.join(sorted(SUPPORTED_TARGET_SOURCE_TIMEFRAMES))}"
            )
        if int(self.rebalance_interval_bars) < 1:
            raise ValueError("rebalance_interval_bars must be at least 1")
        if self.starting_cash_usd <= 0.0:
            raise ValueError("starting_cash_usd must be greater than 0")
        if self.fee_bps < 0.0:
            raise ValueError("fee_bps must be greater than or equal to 0")
        if int(self.long_lookback_bars) < 2:
            raise ValueError("long_lookback_bars must be at least 2")
        if int(self.short_lookback_bars) < 2:
            raise ValueError("short_lookback_bars must be at least 2")
        if int(self.pullback_lookback_bars) < 2:
            raise ValueError("pullback_lookback_bars must be at least 2")
        if int(self.max_target_pairs) < 1:
            raise ValueError("max_target_pairs must be at least 1")
        if self.pullback_overextension_bps < 0.0:
            raise ValueError(
                "pullback_overextension_bps must be greater than or equal to 0"
            )
        if self.oversold_threshold_bps <= 0.0:
            raise ValueError("oversold_threshold_bps must be greater than 0")


@dataclass
class TargetSourceResearchResult:
    generated_at: datetime
    start: datetime
    end: datetime
    pairs: list[str]
    params: TargetSourceResearchParams
    summary: dict[str, Any]
    preflight: dict[str, Any] | None = None
    runs: list[dict[str, Any]] = field(default_factory=list)

    def to_report_dict(self) -> dict[str, Any]:
        return {
            "report_version": REPORT_VERSION,
            "report_type": REPORT_TYPE_TARGET_SOURCE_RESEARCH,
            "generated_at": self.generated_at.astimezone(UTC).isoformat(),
            "summary": copy.deepcopy(self.summary),
            "preflight": copy.deepcopy(self.preflight),
            "runs": copy.deepcopy(self.runs),
        }


@dataclass
class _TargetPortfolio:
    cash_usd: float
    holdings: dict[str, float]


def run_target_source_research(
    config: AppConfig,
    *,
    start: datetime,
    end: datetime,
    pairs: Sequence[str] | None = None,
    params: TargetSourceResearchParams | None = None,
    scenarios: Sequence[str] | None = None,
    strict_data: bool = False,
) -> TargetSourceResearchResult:
    params = params or TargetSourceResearchParams()
    selected_pairs = _target_source_pairs(config, pairs)
    market_data = BacktestMarketData(
        config,
        pairs=selected_pairs,
        timeframes=[params.timeframe],
        start=_as_utc(start),
        end=_as_utc(end),
    )
    try:
        preflight = market_data.get_preflight()
        if strict_data and (preflight.missing_series or preflight.partial_series):
            raise ValueError(_strict_data_message("target source research", preflight))
        market_data.set_time(_as_utc(end))
        bars_by_pair = {
            pair: market_data.get_ohlc(pair, params.timeframe, lookback=1_000_000)
            for pair in selected_pairs
        }
        return evaluate_target_source_scenarios(
            bars_by_pair,
            start=start,
            end=end,
            pairs=selected_pairs,
            params=params,
            scenarios=scenarios,
            preflight=_preflight_to_dict(preflight),
        )
    finally:
        shutdown = getattr(market_data, "shutdown", None)
        if callable(shutdown):
            shutdown()


def evaluate_target_source_scenarios(
    bars_by_pair: Mapping[str, Sequence[OHLCBar]],
    *,
    start: datetime,
    end: datetime,
    pairs: Sequence[str],
    params: TargetSourceResearchParams | None = None,
    scenarios: Sequence[str] | None = None,
    preflight: dict[str, Any] | None = None,
) -> TargetSourceResearchResult:
    params = params or TargetSourceResearchParams()
    start = _as_utc(start)
    end = _as_utc(end)
    selected_scenarios = _validate_target_source_scenarios(
        scenarios or DEFAULT_TARGET_SOURCE_SCENARIOS
    )
    selected_pairs = _clean_pairs(pairs)
    cleaned = {pair: _sort_bars(bars_by_pair.get(pair, [])) for pair in selected_pairs}
    price_maps = _price_maps(cleaned)
    timeline = _common_timeline(
        price_maps,
        pairs=[pair for pair in selected_pairs if pair in price_maps],
        start=start,
        end=end,
    )
    if not timeline:
        raise ValueError("No common target-source bars were available")

    strict_data_ready = _strict_data_ready(preflight)
    runs = [
        _simulate_target_source_run(
            scenario_id=scenario_id,
            pairs=selected_pairs,
            price_maps=price_maps,
            timeline=timeline,
            params=params,
            strict_data_ready=strict_data_ready,
        )
        for scenario_id in selected_scenarios
    ]
    summary = {
        "research_only": True,
        "runtime_wiring_approved": False,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "pairs": selected_pairs,
        "timeframe": params.timeframe,
        "scenarios": selected_scenarios,
        "params": asdict(params),
        "total_cycles": len(timeline),
        "strict_data_ready": strict_data_ready,
    }
    return TargetSourceResearchResult(
        generated_at=datetime.now(UTC),
        start=start,
        end=end,
        pairs=selected_pairs,
        params=params,
        summary=summary,
        preflight=copy.deepcopy(preflight),
        runs=runs,
    )


def select_target_source_weights(
    scenario_id: str,
    *,
    pairs: Sequence[str],
    price_maps: Mapping[str, Mapping[int, float]],
    timeline: Sequence[int],
    index: int,
    params: TargetSourceResearchParams | None = None,
) -> dict[str, float]:
    """Return scenario target weights for one rebalance point."""

    params = params or TargetSourceResearchParams()
    _validate_target_source_scenarios([scenario_id])
    selected: list[str]
    if scenario_id == "rank_top2":
        selected = _rank_top_momentum_pairs(
            pairs,
            price_maps=price_maps,
            timeline=timeline,
            index=index,
            params=params,
            require_positive_short=False,
            require_positive_long=False,
            use_vol_adjusted_score=False,
            reject_overextension=False,
            allow_partial_long_lookback=True,
        )
    elif scenario_id == "dual_momentum_top2":
        selected = _rank_top_momentum_pairs(
            pairs,
            price_maps=price_maps,
            timeline=timeline,
            index=index,
            params=params,
            require_positive_short=True,
            require_positive_long=True,
            use_vol_adjusted_score=False,
            reject_overextension=False,
            allow_partial_long_lookback=False,
        )
    elif scenario_id == "vol_adj_dual_momentum_top2":
        selected = _rank_top_momentum_pairs(
            pairs,
            price_maps=price_maps,
            timeline=timeline,
            index=index,
            params=params,
            require_positive_short=True,
            require_positive_long=True,
            use_vol_adjusted_score=True,
            reject_overextension=False,
            allow_partial_long_lookback=False,
        )
    elif scenario_id == "pullback_vol_adj_top2":
        selected = _rank_top_momentum_pairs(
            pairs,
            price_maps=price_maps,
            timeline=timeline,
            index=index,
            params=params,
            require_positive_short=True,
            require_positive_long=True,
            use_vol_adjusted_score=True,
            reject_overextension=True,
            allow_partial_long_lookback=False,
        )
    elif scenario_id == "oversold_reversion_top1":
        selected = _oversold_reversion_pairs(
            pairs,
            price_maps=price_maps,
            timeline=timeline,
            index=index,
            params=params,
        )
    elif scenario_id == "hybrid_state_source":
        if _hybrid_state_is_risk_on(
            pairs,
            price_maps=price_maps,
            timeline=timeline,
            index=index,
            params=params,
        ):
            selected = _rank_top_momentum_pairs(
                pairs,
                price_maps=price_maps,
                timeline=timeline,
                index=index,
                params=params,
                require_positive_short=True,
                require_positive_long=True,
                use_vol_adjusted_score=True,
                reject_overextension=False,
                allow_partial_long_lookback=False,
            )
        else:
            selected = _oversold_reversion_pairs(
                pairs,
                price_maps=price_maps,
                timeline=timeline,
                index=index,
                params=params,
            )
    else:
        raise ValueError(f"Unsupported scenario: {scenario_id}")

    if not selected:
        return {}
    return _equal_target_weights(selected, allocation_pct=params.allocation_pct)


def aggregate_target_source_research_reports(
    reports: Sequence[Mapping[str, Any]],
    *,
    report_paths: Sequence[str],
    save_dir: str,
) -> dict[str, Any]:
    rows = _target_source_rows(reports, report_paths=report_paths)
    groups = _target_source_groups(rows)
    candidate_summaries = _target_source_candidate_summaries(groups)
    return {
        "report_version": REPORT_VERSION,
        "report_type": REPORT_TYPE_TARGET_SOURCE_SWEEP,
        "generated_at": datetime.now(UTC).isoformat(),
        "summary": {
            "research_only": True,
            "runtime_wiring_approved": False,
            "save_dir": str(save_dir),
            "aggregate_path": str(save_dir).rstrip("\\/") + "/aggregate.json",
            "window_sets": sorted({row["window_set"] for row in rows}),
            "report_count": len(reports),
            "row_count": len(rows),
            "rows": rows,
            "groups": groups,
            "candidate_summaries": candidate_summaries,
            "candidate_scenarios": [
                item
                for item in candidate_summaries
                if bool(item["promotion_gate"]["passed"])
            ],
        },
    }


def _target_source_pairs(
    config: AppConfig,
    pairs: Sequence[str] | None,
) -> list[str]:
    if pairs:
        return _clean_pairs(pairs)
    configured = _clean_pairs(_default_pairs(config))
    starter = [pair for pair in STARTER_TARGET_SOURCE_PAIRS if pair in configured]
    return starter or list(STARTER_TARGET_SOURCE_PAIRS)


def _validate_target_source_scenarios(values: Sequence[str]) -> list[str]:
    selected: list[str] = []
    for value in values:
        scenario_id = str(value).strip()
        if scenario_id not in SUPPORTED_TARGET_SOURCE_SCENARIOS:
            raise ValueError(
                f"Unsupported scenario: {scenario_id}. Supported values: "
                f"{', '.join(sorted(SUPPORTED_TARGET_SOURCE_SCENARIOS))}"
            )
        if scenario_id not in selected:
            selected.append(scenario_id)
    return selected


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


def _simulate_target_source_run(
    *,
    scenario_id: str,
    pairs: Sequence[str],
    price_maps: Mapping[str, Mapping[int, float]],
    timeline: Sequence[int],
    params: TargetSourceResearchParams,
    strict_data_ready: bool,
) -> dict[str, Any]:
    portfolio = _TargetPortfolio(
        cash_usd=float(params.starting_cash_usd),
        holdings={pair: 0.0 for pair in pairs},
    )
    equity_curve: list[float] = []
    exposure_curve: list[float] = []
    trades = 0
    fees_usd = 0.0
    rebalance_count = 0
    cash_target_rebalances = 0
    target_selection_counts: Counter[str] = Counter()

    for index, ts in enumerate(timeline):
        prices = {pair: float(price_maps[pair][ts]) for pair in pairs}
        if index % int(params.rebalance_interval_bars) == 0:
            rebalance_count += 1
            equity = _portfolio_equity(portfolio, prices)
            target_weights = select_target_source_weights(
                scenario_id,
                pairs=pairs,
                price_maps=price_maps,
                timeline=timeline,
                index=index,
                params=params,
            )
            if not target_weights:
                cash_target_rebalances += 1
            target_selection_counts.update(target_weights.keys())
            plan = _target_plan(
                scenario_id=scenario_id,
                timestamp=ts,
                portfolio=portfolio,
                prices=prices,
                target_weights=target_weights,
                equity_usd=equity,
            )
            executed = _execute_plan(portfolio, plan, prices, fee_bps=params.fee_bps)
            trades += int(executed["trades"])
            fees_usd += float(executed["fees_usd"])

        equity = _portfolio_equity(portfolio, prices)
        exposure = _portfolio_exposure(portfolio, prices)
        equity_curve.append(equity)
        exposure_curve.append((exposure / equity) * 100.0 if equity > 0.0 else 0.0)

    ending_equity = equity_curve[-1]
    active_cycles = sum(1 for exposure in exposure_curve if exposure > 0.01)
    cash_cycles = len(exposure_curve) - active_cycles
    return {
        "scenario_id": scenario_id,
        "research_only": True,
        "runtime_wiring_approved": False,
        "defensive_only": scenario_id in DEFENSIVE_ONLY_TARGET_SOURCE_SCENARIOS,
        "target_pairs": list(pairs),
        "allocation_pct": params.allocation_pct,
        "timeframe": params.timeframe,
        "rebalance_interval_bars": params.rebalance_interval_bars,
        "starting_cash_usd": params.starting_cash_usd,
        "ending_equity_usd": ending_equity,
        "return_pct": (
            (ending_equity - params.starting_cash_usd) / params.starting_cash_usd
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
        "target_selection_counts": dict(sorted(target_selection_counts.items())),
        "strict_data_ready": strict_data_ready,
    }


def _rank_top_momentum_pairs(
    pairs: Sequence[str],
    *,
    price_maps: Mapping[str, Mapping[int, float]],
    timeline: Sequence[int],
    index: int,
    params: TargetSourceResearchParams,
    require_positive_short: bool,
    require_positive_long: bool,
    use_vol_adjusted_score: bool,
    reject_overextension: bool,
    allow_partial_long_lookback: bool,
) -> list[str]:
    scored: list[tuple[str, float, float]] = []
    for pair in pairs:
        long_momentum = _momentum_bps_at(
            price_maps.get(pair, {}),
            timeline=timeline,
            index=index,
            lookback=int(params.long_lookback_bars),
            allow_partial_lookback=allow_partial_long_lookback,
        )
        short_momentum = _momentum_bps_at(
            price_maps.get(pair, {}),
            timeline=timeline,
            index=index,
            lookback=int(params.short_lookback_bars),
            allow_partial_lookback=False,
        )
        if long_momentum is None:
            continue
        if require_positive_long and long_momentum <= 0.0:
            continue
        if require_positive_short and (short_momentum is None or short_momentum <= 0.0):
            continue
        if reject_overextension:
            pullback_momentum = _momentum_bps_at(
                price_maps.get(pair, {}),
                timeline=timeline,
                index=index,
                lookback=int(params.pullback_lookback_bars),
                allow_partial_lookback=False,
            )
            if pullback_momentum is not None and pullback_momentum > float(
                params.pullback_overextension_bps
            ):
                continue

        score = long_momentum
        if use_vol_adjusted_score:
            volatility = _realized_volatility_bps_at(
                price_maps.get(pair, {}),
                timeline=timeline,
                index=index,
                lookback=int(params.short_lookback_bars),
            )
            if volatility is None or volatility <= 0.0:
                continue
            score = long_momentum / volatility
        scored.append((pair, score, long_momentum))

    scored.sort(key=lambda item: (-item[1], -item[2], item[0]))
    return [pair for pair, _, _ in scored[: int(params.max_target_pairs)]]


def _oversold_reversion_pairs(
    pairs: Sequence[str],
    *,
    price_maps: Mapping[str, Mapping[int, float]],
    timeline: Sequence[int],
    index: int,
    params: TargetSourceResearchParams,
) -> list[str]:
    scored: list[tuple[str, float]] = []
    for pair in pairs:
        momentum = _momentum_bps_at(
            price_maps.get(pair, {}),
            timeline=timeline,
            index=index,
            lookback=int(params.pullback_lookback_bars),
            allow_partial_lookback=False,
        )
        if momentum is None or momentum > -float(params.oversold_threshold_bps):
            continue
        scored.append((pair, momentum))
    scored.sort(key=lambda item: (item[1], item[0]))
    return [pair for pair, _ in scored[:1]]


def _hybrid_state_is_risk_on(
    pairs: Sequence[str],
    *,
    price_maps: Mapping[str, Mapping[int, float]],
    timeline: Sequence[int],
    index: int,
    params: TargetSourceResearchParams,
) -> bool:
    benchmark_pair = "BTC/USD" if "BTC/USD" in pairs else pairs[0]
    benchmark_momentum = _momentum_bps_at(
        price_maps.get(benchmark_pair, {}),
        timeline=timeline,
        index=index,
        lookback=int(params.long_lookback_bars),
        allow_partial_lookback=False,
    )
    basket_momentum = _basket_momentum_bps(
        pairs,
        price_maps=price_maps,
        timeline=timeline,
        index=index,
        lookback=int(params.long_lookback_bars),
    )
    return (
        benchmark_momentum is not None
        and basket_momentum is not None
        and benchmark_momentum > float(params.hybrid_risk_on_benchmark_momentum_bps)
        and basket_momentum > float(params.hybrid_risk_on_basket_momentum_bps)
    )


def _basket_momentum_bps(
    pairs: Sequence[str],
    *,
    price_maps: Mapping[str, Mapping[int, float]],
    timeline: Sequence[int],
    index: int,
    lookback: int,
) -> float | None:
    values = [
        value
        for pair in pairs
        if (
            value := _momentum_bps_at(
                price_maps.get(pair, {}),
                timeline=timeline,
                index=index,
                lookback=lookback,
                allow_partial_lookback=False,
            )
        )
        is not None
    ]
    if not values:
        return None
    return mean(values)


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


def _realized_volatility_bps_at(
    price_map: Mapping[int, float],
    *,
    timeline: Sequence[int],
    index: int,
    lookback: int,
) -> float | None:
    if int(lookback) < 2 or index < int(lookback) - 1:
        return None
    returns: list[float] = []
    start_index = index - int(lookback) + 1
    for previous_index in range(start_index + 1, index + 1):
        previous_ts = timeline[previous_index - 1]
        current_ts = timeline[previous_index]
        previous_price = float(price_map.get(previous_ts, 0.0) or 0.0)
        current_price = float(price_map.get(current_ts, 0.0) or 0.0)
        if previous_price <= 0.0 or current_price <= 0.0:
            return None
        returns.append((current_price - previous_price) / previous_price)
    if len(returns) < 2:
        return None
    average_return = mean(returns)
    variance = sum((value - average_return) ** 2 for value in returns) / (
        len(returns) - 1
    )
    return sqrt(variance) * 10_000.0


def _equal_target_weights(
    target_pairs: Sequence[str],
    *,
    allocation_pct: float,
) -> dict[str, float]:
    allocation = float(allocation_pct) / 100.0
    weight = allocation / len(target_pairs)
    return {pair: weight for pair in target_pairs}


def _target_plan(
    *,
    scenario_id: str,
    timestamp: int,
    portfolio: _TargetPortfolio,
    prices: Mapping[str, float],
    target_weights: Mapping[str, float],
    equity_usd: float,
) -> ExecutionPlan:
    actions: list[RiskAdjustedAction] = []
    for pair in prices:
        target_weight = float(target_weights.get(pair, 0.0) or 0.0)
        price = float(prices[pair])
        current_base = float(portfolio.holdings.get(pair, 0.0))
        target_notional = max(equity_usd * target_weight, 0.0)
        target_base = target_notional / price if price > 0.0 else 0.0
        actions.append(
            RiskAdjustedAction(
                pair=pair,
                strategy_id=f"target_source_research:{scenario_id}",
                action_type=_action_type(current_base, target_base),
                target_base_size=target_base,
                target_notional_usd=target_notional,
                current_base_size=current_base,
                reason="research-only target-source rebalance",
                blocked=False,
                blocked_reasons=[],
            )
        )
    return ExecutionPlan(
        plan_id=f"target-source-research-{scenario_id}-{timestamp}",
        generated_at=datetime.fromtimestamp(int(timestamp), tz=UTC),
        actions=actions,
        metadata={"research_only": True, "scenario_id": scenario_id},
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
    portfolio: _TargetPortfolio,
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
    portfolio: _TargetPortfolio,
    prices: Mapping[str, float],
) -> float:
    return portfolio.cash_usd + _portfolio_exposure(portfolio, prices)


def _portfolio_exposure(
    portfolio: _TargetPortfolio,
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


def _strict_data_ready(preflight: Mapping[str, Any] | None) -> bool:
    if not preflight:
        return True
    return not bool(preflight.get("missing_series") or preflight.get("partial_series"))


def _target_source_rows(
    reports: Sequence[Mapping[str, Any]],
    *,
    report_paths: Sequence[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for report, report_path in zip(reports, report_paths):
        summary = report["summary"]
        preflight = report.get("preflight") or {}
        strict_data_ready = _strict_data_ready(preflight)
        for run in report.get("runs", []):
            rows.append(
                {
                    "window_set": summary["window_set"],
                    "window_id": summary["window_id"],
                    "allocation_pct": float(summary["allocation_pct"]),
                    "scenario_id": run["scenario_id"],
                    "report_path": report_path,
                    "return_pct": float(run["return_pct"]),
                    "max_drawdown_pct": float(run["max_drawdown_pct"]),
                    "trades": int(run["trades"]),
                    "fees_usd": float(run["fees_usd"]),
                    "cash_target_rebalances": int(run["cash_target_rebalances"]),
                    "active_cycle_pct": float(run["active_cycle_pct"]),
                    "avg_exposure_pct": float(run["avg_exposure_pct"]),
                    "target_selection_counts": copy.deepcopy(
                        run.get("target_selection_counts") or {}
                    ),
                    "strict_data_ready": bool(
                        strict_data_ready and run.get("strict_data_ready", True)
                    ),
                    "research_only": bool(run.get("research_only")),
                    "runtime_wiring_approved": bool(run.get("runtime_wiring_approved")),
                    "defensive_only": bool(run.get("defensive_only")),
                }
            )

    baseline_by_key = {
        (row["window_set"], row["window_id"], row["allocation_pct"]): row
        for row in rows
        if row["scenario_id"] == BASELINE_TARGET_SOURCE_SCENARIO
    }
    for row in rows:
        baseline = baseline_by_key.get(
            (row["window_set"], row["window_id"], row["allocation_pct"])
        )
        if baseline is None:
            row["baseline_rank_return_pct"] = None
            row["baseline_rank_max_drawdown_pct"] = None
            row["delta_return_pct_vs_rank_top2"] = None
            row["delta_max_drawdown_pct_vs_rank_top2"] = None
            continue
        row["baseline_rank_return_pct"] = baseline["return_pct"]
        row["baseline_rank_max_drawdown_pct"] = baseline["max_drawdown_pct"]
        row["delta_return_pct_vs_rank_top2"] = (
            row["return_pct"] - baseline["return_pct"]
        )
        row["delta_max_drawdown_pct_vs_rank_top2"] = (
            row["max_drawdown_pct"] - baseline["max_drawdown_pct"]
        )
    return rows


def _target_source_groups(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    group_keys = sorted(
        {(row["window_set"], row["allocation_pct"], row["scenario_id"]) for row in rows}
    )
    for window_set, allocation_pct, scenario_id in group_keys:
        items = [
            row
            for row in rows
            if row["window_set"] == window_set
            and row["allocation_pct"] == allocation_pct
            and row["scenario_id"] == scenario_id
        ]
        if not items:
            continue
        return_deltas = [
            float(row["delta_return_pct_vs_rank_top2"])
            for row in items
            if row["delta_return_pct_vs_rank_top2"] is not None
        ]
        drawdown_deltas = [
            float(row["delta_max_drawdown_pct_vs_rank_top2"])
            for row in items
            if row["delta_max_drawdown_pct_vs_rank_top2"] is not None
        ]
        window_count = len(items)
        required_windows = _required_positive_windows(window_set, window_count)
        positive_or_near_flat_windows = sum(
            1 for row in items if float(row["return_pct"]) >= NEAR_FLAT_RETURN_PCT
        )
        avg_return = mean(float(row["return_pct"]) for row in items)
        avg_drawdown = mean(float(row["max_drawdown_pct"]) for row in items)
        avg_exposure = mean(float(row["avg_exposure_pct"]) for row in items)
        avg_baseline_return = (
            mean(float(row["baseline_rank_return_pct"]) for row in items)
            if all(row["baseline_rank_return_pct"] is not None for row in items)
            else None
        )
        avg_baseline_drawdown = (
            mean(float(row["baseline_rank_max_drawdown_pct"]) for row in items)
            if all(row["baseline_rank_max_drawdown_pct"] is not None for row in items)
            else None
        )
        current_row = next(
            (row for row in items if row["window_id"] == CURRENT_ROLLING_WINDOW_ID),
            None,
        )
        current_not_obvious_failure = True
        current_result: dict[str, Any] | None = None
        if current_row is not None:
            current_result = {
                "window_id": CURRENT_ROLLING_WINDOW_ID,
                "return_pct": current_row["return_pct"],
                "max_drawdown_pct": current_row["max_drawdown_pct"],
            }
            current_not_obvious_failure = (
                float(current_row["return_pct"]) >= -0.50
                and float(current_row["max_drawdown_pct"]) <= 2.0
            )
        defensive_only = all(bool(row["defensive_only"]) for row in items)
        primary_allocation = abs(float(allocation_pct) - 20.0) <= 1e-9
        required_avg_exposure_pct = float(allocation_pct) * 0.25
        exposure_adequate = defensive_only or avg_exposure >= required_avg_exposure_pct
        gate = {
            "primary_allocation_20_pct": primary_allocation,
            "beats_rank_top2_avg_return": (
                scenario_id != BASELINE_TARGET_SOURCE_SCENARIO
                and len(return_deltas) == window_count
                and mean(return_deltas) > 0.0
            ),
            "beats_rank_top2_avg_drawdown": (
                scenario_id != BASELINE_TARGET_SOURCE_SCENARIO
                and len(drawdown_deltas) == window_count
                and mean(drawdown_deltas) < 0.0
            ),
            "positive_or_near_flat_windows": (
                positive_or_near_flat_windows >= required_windows
            ),
            "current_window_not_obvious_failure": current_not_obvious_failure,
            "exposure_adequate": exposure_adequate,
            "strict_data_ready": all(bool(row["strict_data_ready"]) for row in items),
            "research_flags": all(
                bool(row["research_only"]) and not bool(row["runtime_wiring_approved"])
                for row in items
            ),
        }
        gate["passed"] = all(gate.values())
        groups.append(
            {
                "window_set": window_set,
                "allocation_pct": float(allocation_pct),
                "scenario_id": scenario_id,
                "defensive_only": defensive_only,
                "scale_sensitivity_only": not primary_allocation,
                "window_count": window_count,
                "required_positive_or_near_flat_windows": required_windows,
                "avg_return_pct": avg_return,
                "avg_max_drawdown_pct": avg_drawdown,
                "avg_rank_top2_return_pct": avg_baseline_return,
                "avg_rank_top2_max_drawdown_pct": avg_baseline_drawdown,
                "avg_delta_return_pct_vs_rank_top2": (
                    mean(return_deltas) if return_deltas else None
                ),
                "avg_delta_max_drawdown_pct_vs_rank_top2": (
                    mean(drawdown_deltas) if drawdown_deltas else None
                ),
                "positive_or_near_flat_windows": positive_or_near_flat_windows,
                "avg_exposure_pct": avg_exposure,
                "required_avg_exposure_pct": required_avg_exposure_pct,
                "current_window_result": current_result,
                "promotion_gate": gate,
            }
        )
    return groups


def _target_source_candidate_summaries(
    groups: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    keys = sorted({(group["allocation_pct"], group["scenario_id"]) for group in groups})
    summaries: list[dict[str, Any]] = []
    for allocation_pct, scenario_id in keys:
        items = [
            group
            for group in groups
            if group["allocation_pct"] == allocation_pct
            and group["scenario_id"] == scenario_id
        ]
        if not items:
            continue
        gate = {
            "all_window_sets_passed": all(
                bool(group["promotion_gate"]["passed"]) for group in items
            ),
            "strict_data_ready": all(
                bool(group["promotion_gate"]["strict_data_ready"]) for group in items
            ),
            "research_flags": all(
                bool(group["promotion_gate"]["research_flags"]) for group in items
            ),
        }
        gate["passed"] = all(gate.values())
        summaries.append(
            {
                "allocation_pct": float(allocation_pct),
                "scenario_id": scenario_id,
                "window_sets": [group["window_set"] for group in items],
                "window_set_gate_status": {
                    group["window_set"]: bool(group["promotion_gate"]["passed"])
                    for group in items
                },
                "promotion_gate": gate,
            }
        )
    return summaries


def _required_positive_windows(window_set: str, window_count: int) -> int:
    if window_set == "recent_20d":
        return 3
    if window_set == "long_4h":
        return 4
    return 3 if window_count <= 5 else 4
