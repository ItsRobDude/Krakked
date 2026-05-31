"""Research-only pair-local source proof over cached OHLC."""

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

from .market_regime_overlay import (
    _as_utc,
    _clean_pairs,
    _default_pairs,
    _preflight_to_dict,
    _sort_bars,
    _strict_data_message,
)
from .runner import BacktestMarketData
from .target_source_research import STARTER_TARGET_SOURCE_PAIRS

REPORT_TYPE_PAIR_LOCAL_SOURCE_RESEARCH = "pair_local_source_research"
REPORT_TYPE_PAIR_LOCAL_SOURCE_SWEEP = "pair_local_source_research_sweep"
REPORT_VERSION = 1
DEFAULT_PAIR_LOCAL_SOURCE_SCENARIOS = (
    "pair_dual_momentum",
    "pair_vol_adj_momentum",
    "pair_trend_pullback",
    "pair_oversold_reversion",
    "pair_breakout_continuation",
)
SUPPORTED_PAIR_LOCAL_SOURCE_SCENARIOS = frozenset(DEFAULT_PAIR_LOCAL_SOURCE_SCENARIOS)
SUPPORTED_PAIR_LOCAL_TIMEFRAMES = frozenset(("4h",))
CURRENT_ROLLING_WINDOW_ID = "20260510-20260530"
NEAR_FLAT_RETURN_PCT = -0.10


@dataclass(frozen=True)
class PairLocalSourceResearchParams:
    allocation_pct: float = 20.0
    timeframe: str = "4h"
    rebalance_interval_bars: int = 6
    starting_cash_usd: float = 10_000.0
    fee_bps: float = 25.0
    long_lookback_bars: int = 63
    short_lookback_bars: int = 21
    pullback_lookback_bars: int = 6
    min_long_momentum_bps: float = 150.0
    min_short_momentum_bps: float = 0.0
    min_vol_adjusted_score: float = 1.0
    pullback_min_bps: float = 50.0
    pullback_max_bps: float = 350.0
    oversold_threshold_bps: float = 250.0
    breakout_min_bps: float = 150.0
    breakout_max_bps: float = 500.0

    def __post_init__(self) -> None:
        if self.allocation_pct <= 0.0 or self.allocation_pct > 100.0:
            raise ValueError("allocation_pct must be greater than 0 and at most 100")
        if self.timeframe not in SUPPORTED_PAIR_LOCAL_TIMEFRAMES:
            raise ValueError(
                "timeframe must be one of "
                f"{', '.join(sorted(SUPPORTED_PAIR_LOCAL_TIMEFRAMES))}"
            )
        if int(self.rebalance_interval_bars) < 1:
            raise ValueError("rebalance_interval_bars must be at least 1")
        if self.starting_cash_usd <= 0.0:
            raise ValueError("starting_cash_usd must be greater than 0")
        if self.fee_bps < 0.0:
            raise ValueError("fee_bps must be greater than or equal to 0")
        for field_name in (
            "long_lookback_bars",
            "short_lookback_bars",
            "pullback_lookback_bars",
        ):
            if int(getattr(self, field_name)) < 2:
                raise ValueError(f"{field_name} must be at least 2")
        if self.min_vol_adjusted_score < 0.0:
            raise ValueError(
                "min_vol_adjusted_score must be greater than or equal to 0"
            )
        if self.pullback_min_bps < 0.0 or self.pullback_max_bps < self.pullback_min_bps:
            raise ValueError("pullback thresholds must be nonnegative and ordered")
        if self.oversold_threshold_bps <= 0.0:
            raise ValueError("oversold_threshold_bps must be greater than 0")
        if self.breakout_min_bps < 0.0 or self.breakout_max_bps < self.breakout_min_bps:
            raise ValueError("breakout thresholds must be nonnegative and ordered")


@dataclass
class PairLocalSourceResearchResult:
    generated_at: datetime
    start: datetime
    end: datetime
    pairs: list[str]
    params: PairLocalSourceResearchParams
    summary: dict[str, Any]
    preflight: dict[str, Any] | None = None
    runs: list[dict[str, Any]] = field(default_factory=list)

    def to_report_dict(self) -> dict[str, Any]:
        return {
            "report_version": REPORT_VERSION,
            "report_type": REPORT_TYPE_PAIR_LOCAL_SOURCE_RESEARCH,
            "generated_at": self.generated_at.astimezone(UTC).isoformat(),
            "summary": copy.deepcopy(self.summary),
            "preflight": copy.deepcopy(self.preflight),
            "runs": copy.deepcopy(self.runs),
        }


@dataclass
class _PairLocalPortfolio:
    cash_usd: float
    base_size: float = 0.0


def run_pair_local_source_research(
    config: AppConfig,
    *,
    start: datetime,
    end: datetime,
    pairs: Sequence[str] | None = None,
    params: PairLocalSourceResearchParams | None = None,
    scenarios: Sequence[str] | None = None,
    strict_data: bool = False,
) -> PairLocalSourceResearchResult:
    params = params or PairLocalSourceResearchParams()
    selected_pairs = _pair_local_pairs(config, pairs)
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
            raise ValueError(
                _strict_data_message("pair-local source research", preflight)
            )
        market_data.set_time(_as_utc(end))
        bars_by_pair = {
            pair: market_data.get_ohlc(pair, params.timeframe, lookback=1_000_000)
            for pair in selected_pairs
        }
        return evaluate_pair_local_source_scenarios(
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


def evaluate_pair_local_source_scenarios(
    bars_by_pair: Mapping[str, Sequence[OHLCBar]],
    *,
    start: datetime,
    end: datetime,
    pairs: Sequence[str],
    params: PairLocalSourceResearchParams | None = None,
    scenarios: Sequence[str] | None = None,
    preflight: dict[str, Any] | None = None,
) -> PairLocalSourceResearchResult:
    params = params or PairLocalSourceResearchParams()
    start = _as_utc(start)
    end = _as_utc(end)
    selected_pairs = _clean_pairs(pairs)
    selected_scenarios = _validate_pair_local_scenarios(
        scenarios or DEFAULT_PAIR_LOCAL_SOURCE_SCENARIOS
    )
    cleaned = {pair: _sort_bars(bars_by_pair.get(pair, [])) for pair in selected_pairs}
    strict_data_ready = _strict_data_ready(preflight)
    runs: list[dict[str, Any]] = []
    for scenario_id in selected_scenarios:
        for pair in selected_pairs:
            timeline = _pair_timeline(cleaned.get(pair, []), start=start, end=end)
            if not timeline:
                continue
            price_map = _price_map(cleaned.get(pair, []))
            runs.append(
                _simulate_pair_local_run(
                    scenario_id=scenario_id,
                    pair=pair,
                    price_map=price_map,
                    timeline=timeline,
                    params=params,
                    strict_data_ready=strict_data_ready,
                )
            )
    if not runs:
        raise ValueError("No pair-local source runs had usable cached bars")

    summary = {
        "research_only": True,
        "runtime_wiring_approved": False,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "pairs": selected_pairs,
        "timeframe": params.timeframe,
        "scenarios": selected_scenarios,
        "params": asdict(params),
        "strict_data_ready": strict_data_ready,
        "run_count": len(runs),
    }
    return PairLocalSourceResearchResult(
        generated_at=datetime.now(UTC),
        start=start,
        end=end,
        pairs=selected_pairs,
        params=params,
        summary=summary,
        preflight=copy.deepcopy(preflight),
        runs=runs,
    )


def pair_local_signal_active(
    scenario_id: str,
    *,
    features: Mapping[str, float | None],
    params: PairLocalSourceResearchParams | None = None,
) -> tuple[bool, list[str]]:
    params = params or PairLocalSourceResearchParams()
    _validate_pair_local_scenarios([scenario_id])
    long_momentum = features.get("long_momentum_bps")
    short_momentum = features.get("short_momentum_bps")
    pullback_momentum = features.get("pullback_momentum_bps")
    volatility = features.get("realized_volatility_bps")
    score = features.get("vol_adjusted_score")
    reasons: list[str] = []

    if scenario_id == "pair_dual_momentum":
        if long_momentum is None or long_momentum < params.min_long_momentum_bps:
            reasons.append("long_momentum_below_threshold")
        if short_momentum is None or short_momentum <= params.min_short_momentum_bps:
            reasons.append("short_momentum_below_threshold")
    elif scenario_id == "pair_vol_adj_momentum":
        if long_momentum is None or long_momentum < params.min_long_momentum_bps:
            reasons.append("long_momentum_below_threshold")
        if short_momentum is None or short_momentum <= params.min_short_momentum_bps:
            reasons.append("short_momentum_below_threshold")
        if volatility is None or volatility <= 0.0 or score is None:
            reasons.append("missing_or_zero_realized_volatility")
        elif score < params.min_vol_adjusted_score:
            reasons.append("vol_adjusted_score_below_threshold")
    elif scenario_id == "pair_trend_pullback":
        if long_momentum is None or long_momentum < params.min_long_momentum_bps:
            reasons.append("long_momentum_below_threshold")
        if short_momentum is None or short_momentum <= params.min_short_momentum_bps:
            reasons.append("short_momentum_below_threshold")
        if pullback_momentum is None:
            reasons.append("missing_pullback_momentum")
        elif not (
            -params.pullback_max_bps <= pullback_momentum <= -params.pullback_min_bps
        ):
            reasons.append("pullback_not_in_entry_band")
    elif scenario_id == "pair_oversold_reversion":
        if (
            pullback_momentum is None
            or pullback_momentum > -params.oversold_threshold_bps
        ):
            reasons.append("oversold_threshold_not_met")
    elif scenario_id == "pair_breakout_continuation":
        if short_momentum is None:
            reasons.append("missing_short_momentum")
        elif not (params.breakout_min_bps <= short_momentum <= params.breakout_max_bps):
            reasons.append("breakout_momentum_not_in_entry_band")
        if long_momentum is None or long_momentum < params.min_long_momentum_bps:
            reasons.append("long_momentum_below_threshold")
    else:
        raise ValueError(f"Unsupported scenario: {scenario_id}")
    return not reasons, reasons


def aggregate_pair_local_source_research_reports(
    reports: Sequence[Mapping[str, Any]],
    *,
    report_paths: Sequence[str],
    save_dir: str,
) -> dict[str, Any]:
    rows = _pair_local_rows(reports, report_paths=report_paths)
    groups = _pair_local_groups(rows)
    candidate_summaries = _pair_local_candidate_summaries(groups)
    candidates = [
        item for item in candidate_summaries if bool(item["promotion_gate"]["passed"])
    ]
    return {
        "report_version": REPORT_VERSION,
        "report_type": REPORT_TYPE_PAIR_LOCAL_SOURCE_SWEEP,
        "generated_at": datetime.now(UTC).isoformat(),
        "summary": {
            "research_only": True,
            "runtime_wiring_approved": False,
            "promote_pair_local_source": bool(candidates),
            "save_dir": str(save_dir),
            "aggregate_path": str(save_dir).rstrip("\\/") + "/aggregate.json",
            "window_sets": sorted({row["window_set"] for row in rows}),
            "report_count": len(reports),
            "row_count": len(rows),
            "rows": rows,
            "groups": groups,
            "candidate_summaries": candidate_summaries,
            "candidate_sources": candidates,
        },
    }


def _pair_local_pairs(config: AppConfig, pairs: Sequence[str] | None) -> list[str]:
    if pairs:
        return _clean_pairs(pairs)
    configured = _clean_pairs(_default_pairs(config))
    starter = [pair for pair in STARTER_TARGET_SOURCE_PAIRS if pair in configured]
    return starter or list(STARTER_TARGET_SOURCE_PAIRS)


def _validate_pair_local_scenarios(values: Sequence[str]) -> list[str]:
    selected: list[str] = []
    for value in values:
        scenario_id = str(value).strip()
        if scenario_id not in SUPPORTED_PAIR_LOCAL_SOURCE_SCENARIOS:
            raise ValueError(
                f"Unsupported scenario: {scenario_id}. Supported values: "
                f"{', '.join(sorted(SUPPORTED_PAIR_LOCAL_SOURCE_SCENARIOS))}"
            )
        if scenario_id not in selected:
            selected.append(scenario_id)
    return selected


def _price_map(bars: Sequence[OHLCBar]) -> dict[int, float]:
    return {
        int(bar.timestamp): float(bar.close) for bar in bars if float(bar.close) > 0.0
    }


def _pair_timeline(
    bars: Sequence[OHLCBar],
    *,
    start: datetime,
    end: datetime,
) -> list[int]:
    start_ts = int(start.timestamp())
    end_ts = int(end.timestamp())
    return [
        int(bar.timestamp)
        for bar in bars
        if start_ts <= int(bar.timestamp) <= end_ts and float(bar.close) > 0.0
    ]


def _simulate_pair_local_run(
    *,
    scenario_id: str,
    pair: str,
    price_map: Mapping[int, float],
    timeline: Sequence[int],
    params: PairLocalSourceResearchParams,
    strict_data_ready: bool,
) -> dict[str, Any]:
    portfolio = _PairLocalPortfolio(cash_usd=float(params.starting_cash_usd))
    equity_curve: list[float] = []
    exposure_curve: list[float] = []
    trace: list[dict[str, Any]] = []
    trades = 0
    fees_usd = 0.0

    for index, ts in enumerate(timeline):
        price = float(price_map[ts])
        if index % int(params.rebalance_interval_bars) == 0:
            equity_before = _pair_equity(portfolio, price)
            exposure_before = _pair_exposure(portfolio, price)
            held_before = portfolio.base_size > 1e-12
            features = _feature_payload(
                price_map,
                timeline=timeline,
                index=index,
                params=params,
            )
            active, inactive_reasons = pair_local_signal_active(
                scenario_id,
                features=features,
                params=params,
            )
            target_weight = float(params.allocation_pct) / 100.0 if active else 0.0
            executed = _rebalance_pair(
                portfolio,
                price=price,
                equity_usd=equity_before,
                target_weight=target_weight,
                fee_bps=params.fee_bps,
            )
            trades += int(executed["trades"])
            fees_usd += float(executed["fees_usd"])
            equity_after = _pair_equity(portfolio, price)
            exposure_after = _pair_exposure(portfolio, price)
            period_end_index = min(
                index + int(params.rebalance_interval_bars),
                len(timeline) - 1,
            )
            forward_return = _forward_return_pct(
                price_map,
                start_ts=ts,
                end_ts=timeline[period_end_index],
            )
            trace.append(
                {
                    "scenario_id": scenario_id,
                    "pair": pair,
                    "rebalance_index": len(trace),
                    "cycle_index": index,
                    "timestamp": int(ts),
                    "time": datetime.fromtimestamp(int(ts), tz=UTC).isoformat(),
                    "period_end_timestamp": int(timeline[period_end_index]),
                    "period_end_time": datetime.fromtimestamp(
                        int(timeline[period_end_index]), tz=UTC
                    ).isoformat(),
                    "price": price,
                    "features": features,
                    "signal_active": active,
                    "inactive_reasons": inactive_reasons,
                    "held_before": held_before,
                    "held_after": portfolio.base_size > 1e-12,
                    "target_weight": target_weight,
                    "forward_return_pct": forward_return,
                    "equity_before_usd": equity_before,
                    "equity_after_usd": equity_after,
                    "exposure_before_pct": (
                        (exposure_before / equity_before) * 100.0
                        if equity_before > 0.0
                        else 0.0
                    ),
                    "exposure_after_pct": (
                        (exposure_after / equity_after) * 100.0
                        if equity_after > 0.0
                        else 0.0
                    ),
                    "trades": int(executed["trades"]),
                    "fees_usd": float(executed["fees_usd"]),
                    "cumulative_fees_usd": fees_usd,
                }
            )

        equity = _pair_equity(portfolio, price)
        exposure = _pair_exposure(portfolio, price)
        equity_curve.append(equity)
        exposure_curve.append((exposure / equity) * 100.0 if equity > 0.0 else 0.0)

    ending_equity = equity_curve[-1]
    active_cycles = sum(1 for exposure in exposure_curve if exposure > 0.01)
    cash_cycles = len(equity_curve) - active_cycles
    run = {
        "scenario_id": scenario_id,
        "pair": pair,
        "research_only": True,
        "runtime_wiring_approved": False,
        "allocation_pct": params.allocation_pct,
        "timeframe": params.timeframe,
        "rebalance_interval_bars": params.rebalance_interval_bars,
        "starting_cash_usd": params.starting_cash_usd,
        "ending_equity_usd": ending_equity,
        "return_pct": (
            (ending_equity - params.starting_cash_usd) / params.starting_cash_usd
        )
        * 100.0,
        "gross_return_before_fees_pct": (
            (ending_equity + fees_usd - params.starting_cash_usd)
            / params.starting_cash_usd
        )
        * 100.0,
        "max_drawdown_pct": _max_drawdown_pct(equity_curve),
        "trades": trades,
        "fees_usd": fees_usd,
        "fee_drag_pct_of_starting_cash": (
            fees_usd / float(params.starting_cash_usd) * 100.0
        ),
        "rebalance_count": len(trace),
        "active_rebalance_count": sum(1 for row in trace if bool(row["signal_active"])),
        "active_rebalance_pct": (
            sum(1 for row in trace if bool(row["signal_active"])) / len(trace) * 100.0
            if trace
            else 0.0
        ),
        "total_cycles": len(equity_curve),
        "active_cycles": active_cycles,
        "cash_cycles": cash_cycles,
        "active_cycle_pct": (
            (active_cycles / len(equity_curve)) * 100.0 if equity_curve else 0.0
        ),
        "avg_exposure_pct": mean(exposure_curve) if exposure_curve else 0.0,
        "max_exposure_pct": max(exposure_curve) if exposure_curve else 0.0,
        "strict_data_ready": strict_data_ready,
        "rebalance_trace": trace,
    }
    run["diagnostics"] = _pair_local_diagnostics(run, trace, params)
    return run


def _pair_local_diagnostics(
    run: Mapping[str, Any],
    trace: Sequence[Mapping[str, Any]],
    params: PairLocalSourceResearchParams,
) -> dict[str, Any]:
    active = [row for row in trace if bool(row["signal_active"])]
    inactive = [row for row in trace if not bool(row["signal_active"])]
    active_forward = [
        float(row["forward_return_pct"])
        for row in active
        if row.get("forward_return_pct") is not None
    ]
    inactive_forward = [
        float(row["forward_return_pct"])
        for row in inactive
        if row.get("forward_return_pct") is not None
    ]
    losing_active = [value for value in active_forward if value < 0.0]
    missed_upside = [value for value in inactive_forward if value > 0.50]
    late_chase = [
        row
        for row in active
        if row.get("forward_return_pct") is not None
        and float(row["forward_return_pct"]) < 0.0
        and (
            row.get("features", {}).get("pullback_momentum_bps") is not None
            and float(row["features"]["pullback_momentum_bps"])
            > params.pullback_max_bps
        )
    ]
    slow_exit = [
        row
        for row in active
        if bool(row.get("held_before"))
        and row.get("forward_return_pct") is not None
        and float(row["forward_return_pct"]) < 0.0
        and row.get("features", {}).get("short_momentum_bps") is not None
        and float(row["features"]["short_momentum_bps"]) < 0.0
    ]
    active_count = len(active)
    return_pct = float(run["return_pct"])
    fee_drag = float(run["fee_drag_pct_of_starting_cash"])
    loss_magnitude = abs(min(return_pct, 0.0))
    failure_reasons: list[str] = []
    if return_pct <= 0.0:
        if active_count and len(losing_active) / active_count >= 0.45:
            failure_reasons.append("negative_active_forward_returns")
        if len(missed_upside) >= max(2, len(inactive) * 0.20):
            failure_reasons.append("missed_pair_upside_while_cash")
        if len(late_chase) >= max(1, active_count * 0.20):
            failure_reasons.append("late_or_chasing_entries")
        if len(slow_exit) >= max(1, active_count * 0.20):
            failure_reasons.append("slow_exit_or_negative_momentum_hold")
        if float(run["active_rebalance_pct"]) < 10.0:
            failure_reasons.append("sparse_pair_exposure")
        if loss_magnitude > 0.0 and fee_drag >= max(loss_magnitude * 0.50, 0.05):
            failure_reasons.append("fee_churn_drag")
        if not failure_reasons:
            failure_reasons.append("weak_or_negative_pair_edge")
    return {
        "failure_reasons": failure_reasons,
        "avg_active_forward_return_pct": _mean_or_none(active_forward),
        "avg_inactive_forward_return_pct": _mean_or_none(inactive_forward),
        "negative_active_forward_count": len(losing_active),
        "missed_upside_count": len(missed_upside),
        "late_chase_count": len(late_chase),
        "slow_exit_count": len(slow_exit),
        "fee_drag_pct_of_starting_cash": fee_drag,
        "fees_to_abs_loss_ratio": (
            fee_drag / loss_magnitude if loss_magnitude > 0.0 else None
        ),
    }


def _feature_payload(
    price_map: Mapping[int, float],
    *,
    timeline: Sequence[int],
    index: int,
    params: PairLocalSourceResearchParams,
) -> dict[str, float | None]:
    long_momentum = _momentum_bps_at(
        price_map,
        timeline=timeline,
        index=index,
        lookback=int(params.long_lookback_bars),
    )
    short_momentum = _momentum_bps_at(
        price_map,
        timeline=timeline,
        index=index,
        lookback=int(params.short_lookback_bars),
    )
    pullback_momentum = _momentum_bps_at(
        price_map,
        timeline=timeline,
        index=index,
        lookback=int(params.pullback_lookback_bars),
    )
    volatility = _realized_volatility_bps_at(
        price_map,
        timeline=timeline,
        index=index,
        lookback=int(params.short_lookback_bars),
    )
    return {
        "long_momentum_bps": long_momentum,
        "short_momentum_bps": short_momentum,
        "pullback_momentum_bps": pullback_momentum,
        "realized_volatility_bps": volatility,
        "vol_adjusted_score": (
            long_momentum / volatility
            if long_momentum is not None and volatility is not None and volatility > 0
            else None
        ),
    }


def _rebalance_pair(
    portfolio: _PairLocalPortfolio,
    *,
    price: float,
    equity_usd: float,
    target_weight: float,
    fee_bps: float,
) -> dict[str, Any]:
    current_base = float(portfolio.base_size)
    target_notional = max(float(equity_usd) * float(target_weight), 0.0)
    target_base = target_notional / float(price) if price > 0.0 else 0.0
    delta_base = target_base - current_base
    trade_notional = abs(delta_base) * float(price)
    if trade_notional <= 1e-8:
        return {"trades": 0, "fees_usd": 0.0}
    fee = trade_notional * (float(fee_bps) / 10_000.0)
    if delta_base > 0.0:
        portfolio.cash_usd -= trade_notional + fee
    else:
        portfolio.cash_usd += trade_notional - fee
    portfolio.base_size = target_base
    return {"trades": 1, "fees_usd": fee}


def _pair_equity(portfolio: _PairLocalPortfolio, price: float) -> float:
    return float(portfolio.cash_usd) + _pair_exposure(portfolio, price)


def _pair_exposure(portfolio: _PairLocalPortfolio, price: float) -> float:
    return max(float(portfolio.base_size), 0.0) * float(price)


def _momentum_bps_at(
    price_map: Mapping[int, float],
    *,
    timeline: Sequence[int],
    index: int,
    lookback: int,
) -> float | None:
    actual_lookback = int(lookback)
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
        previous_price = float(price_map.get(timeline[previous_index - 1], 0.0) or 0.0)
        current_price = float(price_map.get(timeline[previous_index], 0.0) or 0.0)
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


def _forward_return_pct(
    price_map: Mapping[int, float],
    *,
    start_ts: int,
    end_ts: int,
) -> float | None:
    start_price = float(price_map.get(int(start_ts), 0.0) or 0.0)
    end_price = float(price_map.get(int(end_ts), 0.0) or 0.0)
    if start_price <= 0.0 or end_price <= 0.0:
        return None
    return ((end_price - start_price) / start_price) * 100.0


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


def _mean_or_none(values: Sequence[float]) -> float | None:
    return mean(values) if values else None


def _strict_data_ready(preflight: Mapping[str, Any] | None) -> bool:
    if not preflight:
        return True
    return not bool(preflight.get("missing_series") or preflight.get("partial_series"))


def _pair_local_rows(
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
            diagnostics = run.get("diagnostics") or {}
            rows.append(
                {
                    "window_set": summary["window_set"],
                    "window_id": summary["window_id"],
                    "allocation_pct": float(summary["allocation_pct"]),
                    "scenario_id": run["scenario_id"],
                    "pair": run["pair"],
                    "report_path": report_path,
                    "return_pct": float(run["return_pct"]),
                    "gross_return_before_fees_pct": float(
                        run["gross_return_before_fees_pct"]
                    ),
                    "max_drawdown_pct": float(run["max_drawdown_pct"]),
                    "trades": int(run["trades"]),
                    "fees_usd": float(run["fees_usd"]),
                    "fee_drag_pct_of_starting_cash": float(
                        run["fee_drag_pct_of_starting_cash"]
                    ),
                    "active_cycle_pct": float(run["active_cycle_pct"]),
                    "active_rebalance_pct": float(run["active_rebalance_pct"]),
                    "avg_exposure_pct": float(run["avg_exposure_pct"]),
                    "strict_data_ready": bool(
                        strict_data_ready and run.get("strict_data_ready", True)
                    ),
                    "research_only": bool(run.get("research_only")),
                    "runtime_wiring_approved": bool(run.get("runtime_wiring_approved")),
                    "failure_reasons": list(diagnostics.get("failure_reasons") or []),
                    "primary_failure_reason": (
                        list(diagnostics.get("failure_reasons") or [None])[0]
                    ),
                    "avg_active_forward_return_pct": diagnostics.get(
                        "avg_active_forward_return_pct"
                    ),
                    "avg_inactive_forward_return_pct": diagnostics.get(
                        "avg_inactive_forward_return_pct"
                    ),
                    "missed_upside_count": int(
                        diagnostics.get("missed_upside_count", 0) or 0
                    ),
                    "negative_active_forward_count": int(
                        diagnostics.get("negative_active_forward_count", 0) or 0
                    ),
                }
            )
    return rows


def _pair_local_groups(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    group_keys = sorted(
        {
            (row["window_set"], row["allocation_pct"], row["scenario_id"], row["pair"])
            for row in rows
        }
    )
    for window_set, allocation_pct, scenario_id, pair in group_keys:
        items = [
            row
            for row in rows
            if row["window_set"] == window_set
            and row["allocation_pct"] == allocation_pct
            and row["scenario_id"] == scenario_id
            and row["pair"] == pair
        ]
        if not items:
            continue
        window_count = len(items)
        required_windows = _required_positive_windows(window_set, window_count)
        positive_or_near_flat_windows = sum(
            1 for row in items if float(row["return_pct"]) >= NEAR_FLAT_RETURN_PCT
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
        avg_return = mean(float(row["return_pct"]) for row in items)
        avg_gross_return = mean(
            float(row["gross_return_before_fees_pct"]) for row in items
        )
        avg_drawdown = mean(float(row["max_drawdown_pct"]) for row in items)
        avg_active_rebalance = mean(float(row["active_rebalance_pct"]) for row in items)
        failure_reason_counts: Counter[str] = Counter()
        for row in items:
            failure_reason_counts.update(row.get("failure_reasons") or [])
        primary_allocation = abs(float(allocation_pct) - 20.0) <= 1e-9
        gate = {
            "primary_allocation_20_pct": primary_allocation,
            "average_return_positive": avg_return > 0.0,
            "gross_return_positive": avg_gross_return > 0.0,
            "positive_or_near_flat_windows": (
                positive_or_near_flat_windows >= required_windows
            ),
            "average_drawdown_acceptable": avg_drawdown <= 2.0,
            "current_window_not_obvious_failure": current_not_obvious_failure,
            "active_enough": avg_active_rebalance >= 10.0,
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
                "pair": pair,
                "scale_sensitivity_only": not primary_allocation,
                "window_count": window_count,
                "required_positive_or_near_flat_windows": required_windows,
                "avg_return_pct": avg_return,
                "avg_gross_return_before_fees_pct": avg_gross_return,
                "avg_max_drawdown_pct": avg_drawdown,
                "positive_or_near_flat_windows": positive_or_near_flat_windows,
                "avg_active_rebalance_pct": avg_active_rebalance,
                "avg_active_cycle_pct": mean(
                    float(row["active_cycle_pct"]) for row in items
                ),
                "avg_fee_drag_pct_of_starting_cash": mean(
                    float(row["fee_drag_pct_of_starting_cash"]) for row in items
                ),
                "failure_reason_counts": dict(failure_reason_counts.most_common()),
                "current_window_result": current_result,
                "promotion_gate": gate,
            }
        )
    return groups


def _pair_local_candidate_summaries(
    groups: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    keys = sorted(
        {
            (group["allocation_pct"], group["scenario_id"], group["pair"])
            for group in groups
        }
    )
    summaries: list[dict[str, Any]] = []
    for allocation_pct, scenario_id, pair in keys:
        items = [
            group
            for group in groups
            if group["allocation_pct"] == allocation_pct
            and group["scenario_id"] == scenario_id
            and group["pair"] == pair
        ]
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
                "pair": pair,
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
