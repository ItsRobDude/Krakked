"""Research-only market-state overlay evaluation for offline replays."""

from __future__ import annotations

import copy
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Mapping, Sequence

from krakked.config import AppConfig
from krakked.market_data.models import OHLCBar
from krakked.market_regime import (
    DEFAULT_MARKET_REGIME_TIMEFRAME,
    MarketRegimeOverlayParams,
    MarketRegimeSnapshot,
    _as_utc,
    _clean_pairs,
    _default_pairs,
    _sort_bars,
    classify_market_regime_from_market_data,
    classify_market_regime_snapshot,
)
from krakked.strategy.models import ExecutionPlan, RiskAdjustedAction

from .runner import (
    BacktestMarketData,
    BacktestResult,
    _default_backtest_timeframes,
    run_backtest,
)

REPORT_TYPE_RESEARCH = "market_regime_research"
REPORT_TYPE_OVERLAY_BACKTEST = "market_regime_overlay_backtest"
REPORT_VERSION = 1


@dataclass
class MarketRegimeResearchResult:
    generated_at: datetime
    start: datetime
    end: datetime
    pairs: list[str]
    params: MarketRegimeOverlayParams
    summary: dict[str, Any]
    preflight: dict[str, Any] | None = None
    cycles: list[MarketRegimeSnapshot] = field(default_factory=list)

    def to_report_dict(self) -> dict[str, Any]:
        return {
            "report_version": REPORT_VERSION,
            "report_type": REPORT_TYPE_RESEARCH,
            "generated_at": self.generated_at.astimezone(UTC).isoformat(),
            "summary": copy.deepcopy(self.summary),
            "preflight": copy.deepcopy(self.preflight),
            "cycles": [cycle.to_dict() for cycle in self.cycles],
        }


@dataclass
class MarketRegimeOverlayBacktestResult:
    generated_at: datetime
    params: MarketRegimeOverlayParams
    summary: dict[str, Any]
    baseline: BacktestResult
    overlay: BacktestResult

    def to_report_dict(self) -> dict[str, Any]:
        return {
            "report_version": REPORT_VERSION,
            "report_type": REPORT_TYPE_OVERLAY_BACKTEST,
            "generated_at": self.generated_at.astimezone(UTC).isoformat(),
            "summary": copy.deepcopy(self.summary),
            "baseline": self.baseline.to_report_dict(),
            "overlay": self.overlay.to_report_dict(),
        }


def evaluate_market_regime_bars(
    bars_by_pair: Mapping[str, Sequence[OHLCBar]],
    *,
    start: datetime,
    end: datetime,
    params: MarketRegimeOverlayParams,
    preflight: dict[str, Any] | None = None,
) -> MarketRegimeResearchResult:
    start = _as_utc(start)
    end = _as_utc(end)
    cleaned = {pair: _sort_bars(bars) for pair, bars in bars_by_pair.items()}
    timeline = sorted({int(bar.timestamp) for bars in cleaned.values() for bar in bars})
    cycles = [
        classify_market_regime_snapshot(
            cleaned,
            timestamp=ts,
            params=params,
        )
        for ts in timeline
    ]
    state_counts = Counter(cycle.regime for cycle in cycles)
    reason_counts: Counter[str] = Counter()
    for cycle in cycles:
        reason_counts.update(cycle.reason_codes)

    summary = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "pairs": list(cleaned),
        "timeframe": params.timeframe,
        "benchmark_pair": params.benchmark_pair,
        "total_cycles": len(cycles),
        "state_counts": dict(sorted(state_counts.items())),
        "reason_counts": dict(reason_counts.most_common()),
        "risk_off_cycles": state_counts.get("risk_off", 0),
        "neutral_cycles": state_counts.get("neutral", 0),
        "risk_on_cycles": state_counts.get("risk_on", 0),
        "params": asdict(params),
    }
    return MarketRegimeResearchResult(
        generated_at=datetime.now(UTC),
        start=start,
        end=end,
        pairs=list(cleaned),
        params=params,
        summary=summary,
        preflight=copy.deepcopy(preflight),
        cycles=cycles,
    )


def _preflight_to_dict(preflight: Any) -> dict[str, Any]:
    to_dict = getattr(preflight, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    return copy.deepcopy(dict(preflight or {}))


def run_market_regime_research(
    config: AppConfig,
    *,
    start: datetime,
    end: datetime,
    pairs: Sequence[str] | None = None,
    params: MarketRegimeOverlayParams | None = None,
    strict_data: bool = False,
) -> MarketRegimeResearchResult:
    params = params or MarketRegimeOverlayParams()
    selected_pairs = _clean_pairs(list(pairs or _default_pairs(config)))
    if params.benchmark_pair not in selected_pairs:
        selected_pairs.insert(0, params.benchmark_pair)

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
            raise ValueError(_strict_data_message("market regime research", preflight))
        market_data.set_time(_as_utc(end))
        bars_by_pair = {
            pair: market_data.get_ohlc(pair, params.timeframe, lookback=1_000_000)
            for pair in selected_pairs
        }
        return evaluate_market_regime_bars(
            bars_by_pair,
            start=start,
            end=end,
            params=params,
            preflight=_preflight_to_dict(preflight),
        )
    finally:
        shutdown = getattr(market_data, "shutdown", None)
        if callable(shutdown):
            shutdown()


def _strict_data_message(label: str, preflight: Any) -> str:
    details: list[str] = []
    if preflight.missing_series:
        details.append("missing: " + ", ".join(preflight.missing_series))
    if preflight.partial_series:
        details.append("partial: " + ", ".join(preflight.partial_series))
    return f"{label} failed in strict mode: " + "; ".join(details)


def apply_market_regime_overlay_to_plan(
    plan: ExecutionPlan,
    snapshot: MarketRegimeSnapshot,
    *,
    market_data: BacktestMarketData | None = None,
) -> ExecutionPlan:
    adjusted_plan = copy.deepcopy(plan)
    overlay_blocked = 0
    overlay_clamped = 0
    reason_text = (
        "Market regime overlay "
        f"{snapshot.regime}: {', '.join(snapshot.reason_codes)}"
    )

    adjusted_actions: list[RiskAdjustedAction] = []
    for action in adjusted_plan.actions:
        adjusted = copy.deepcopy(action)
        if adjusted.blocked or adjusted.action_type == "none":
            adjusted_actions.append(adjusted)
            continue
        if _is_risk_reducing(adjusted, market_data):
            adjusted_actions.append(adjusted)
            continue
        if snapshot.regime == "risk_on":
            adjusted_actions.append(adjusted)
            continue

        current_notional = _current_notional_usd(adjusted, market_data)
        if snapshot.regime == "risk_off":
            adjusted.blocked = True
            adjusted.blocked_reasons = [
                *list(adjusted.blocked_reasons or []),
                reason_text,
            ]
            adjusted.reason = reason_text
            adjusted.target_base_size = adjusted.current_base_size
            adjusted.target_notional_usd = current_notional
            overlay_blocked += 1
            adjusted_actions.append(adjusted)
            continue

        multiplier = snapshot.allocation_multiplier
        original_target_base = float(adjusted.target_base_size)
        original_target_notional = float(adjusted.target_notional_usd)
        adjusted.target_base_size = float(adjusted.current_base_size) + (
            (original_target_base - float(adjusted.current_base_size)) * multiplier
        )
        adjusted.target_notional_usd = current_notional + (
            (original_target_notional - current_notional) * multiplier
        )
        adjusted.clamped = True
        adjusted.blocked_reasons = [
            *list(adjusted.blocked_reasons or []),
            reason_text,
        ]
        adjusted.reason = reason_text
        overlay_clamped += 1
        adjusted_actions.append(adjusted)

    adjusted_plan.actions = adjusted_actions
    metadata = dict(adjusted_plan.metadata or {})
    metadata["market_regime_overlay"] = {
        **snapshot.to_dict(),
        "overlay_blocked_actions": overlay_blocked,
        "overlay_clamped_actions": overlay_clamped,
        "overlay_interventions": overlay_blocked + overlay_clamped,
    }
    adjusted_plan.metadata = metadata
    return adjusted_plan


def _current_notional_usd(
    action: RiskAdjustedAction,
    market_data: BacktestMarketData | None,
) -> float:
    current_base = float(action.current_base_size)
    if market_data is None:
        implied_price = _implied_action_price(action)
        if implied_price > 0.0:
            return max(current_base * implied_price, 0.0)
        return 0.0
    try:
        price = float(market_data.get_latest_price(action.pair) or 0.0)
    except Exception:  # noqa: BLE001
        price = 0.0
    if price <= 0.0:
        implied_price = _implied_action_price(action)
        if implied_price > 0.0:
            return max(current_base * implied_price, 0.0)
        return 0.0
    return max(current_base * price, 0.0)


def _implied_action_price(action: RiskAdjustedAction) -> float:
    target_base = abs(float(action.target_base_size))
    target_notional = abs(float(action.target_notional_usd))
    if target_base <= 0.0 or target_notional <= 0.0:
        return 0.0
    return target_notional / target_base


def _is_risk_reducing(
    action: RiskAdjustedAction,
    market_data: BacktestMarketData | None,
) -> bool:
    if action.action_type in {"reduce", "close"}:
        return True
    current_notional = _current_notional_usd(action, market_data)
    return (
        float(action.target_base_size) < float(action.current_base_size)
        or float(action.target_notional_usd) < current_notional
    )


def _summarize_overlay_plans(plans: Sequence[ExecutionPlan]) -> dict[str, Any]:
    state_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    overlay_blocked = 0
    overlay_clamped = 0
    intervention_cycles = 0

    for plan in plans:
        payload = (plan.metadata or {}).get("market_regime_overlay") or {}
        if not isinstance(payload, dict):
            continue
        regime = str(payload.get("regime") or "unknown")
        state_counts[regime] += 1
        reason_counts.update(str(reason) for reason in payload.get("reason_codes", []))
        blocked = int(payload.get("overlay_blocked_actions", 0) or 0)
        clamped = int(payload.get("overlay_clamped_actions", 0) or 0)
        overlay_blocked += blocked
        overlay_clamped += clamped
        if blocked or clamped:
            intervention_cycles += 1

    return {
        "state_counts": dict(sorted(state_counts.items())),
        "reason_counts": dict(reason_counts.most_common()),
        "overlay_blocked_actions": overlay_blocked,
        "overlay_clamped_actions": overlay_clamped,
        "overlay_interventions": overlay_blocked + overlay_clamped,
        "intervention_cycles": intervention_cycles,
    }


def _summary_delta(
    overlay_summary: Mapping[str, Any],
    baseline_summary: Mapping[str, Any],
    key: str,
) -> float:
    return float(overlay_summary.get(key, 0.0) or 0.0) - float(
        baseline_summary.get(key, 0.0) or 0.0
    )


def run_market_regime_overlay_backtest(
    config: AppConfig,
    *,
    start: datetime,
    end: datetime,
    pairs: Sequence[str] | None = None,
    params: MarketRegimeOverlayParams | None = None,
    timeframes: Sequence[str] | None = None,
    starting_cash_usd: float = 10_000.0,
    fee_bps: float = 25.0,
    strict_data: bool = False,
) -> MarketRegimeOverlayBacktestResult:
    params = params or MarketRegimeOverlayParams()
    selected_pairs = _clean_pairs(list(pairs or _default_pairs(config)))
    if params.benchmark_pair not in selected_pairs:
        selected_pairs.insert(0, params.benchmark_pair)

    frames_arg = (
        list(timeframes) if timeframes else _default_backtest_timeframes(config)
    )
    if params.timeframe not in frames_arg:
        frames_arg.append(params.timeframe)

    baseline = run_backtest(
        config,
        start=start,
        end=end,
        timeframes=frames_arg,
        starting_cash_usd=starting_cash_usd,
        fee_bps=fee_bps,
        strict_data=strict_data,
    )

    def _transform(
        plan: ExecutionPlan,
        market_data: BacktestMarketData,
        now: datetime,
    ) -> ExecutionPlan:
        snapshot = classify_market_regime_from_market_data(
            market_data,
            pairs=selected_pairs,
            params=params,
            timestamp=int(now.timestamp()),
        )
        return apply_market_regime_overlay_to_plan(
            plan,
            snapshot,
            market_data=market_data,
        )

    overlay = run_backtest(
        config,
        start=start,
        end=end,
        timeframes=frames_arg,
        starting_cash_usd=starting_cash_usd,
        fee_bps=fee_bps,
        strict_data=strict_data,
        plan_transform=_transform,
    )

    if baseline.summary is None or overlay.summary is None:
        raise ValueError("Overlay backtest did not produce comparable summaries")

    baseline_summary = baseline.summary.to_dict()
    overlay_summary = overlay.summary.to_dict()
    overlay_counts = _summarize_overlay_plans(overlay.plans)
    summary = {
        "start": _as_utc(start).isoformat(),
        "end": _as_utc(end).isoformat(),
        "pairs": selected_pairs,
        "timeframes": list(frames_arg),
        "params": asdict(params),
        "baseline": {
            "ending_equity_usd": baseline_summary["ending_equity_usd"],
            "return_pct": baseline_summary["return_pct"],
            "max_drawdown_pct": baseline_summary["max_drawdown_pct"],
            "filled_orders": baseline_summary["filled_orders"],
            "blocked_actions": baseline_summary["blocked_actions"],
            "clamped_actions": baseline_summary.get("clamped_actions", 0),
            "trust_level": baseline_summary.get("trust_level"),
        },
        "overlay": {
            "ending_equity_usd": overlay_summary["ending_equity_usd"],
            "return_pct": overlay_summary["return_pct"],
            "max_drawdown_pct": overlay_summary["max_drawdown_pct"],
            "filled_orders": overlay_summary["filled_orders"],
            "blocked_actions": overlay_summary["blocked_actions"],
            "clamped_actions": overlay_summary.get("clamped_actions", 0),
            "trust_level": overlay_summary.get("trust_level"),
        },
        "delta": {
            "ending_equity_usd": _summary_delta(
                overlay_summary, baseline_summary, "ending_equity_usd"
            ),
            "return_pct": _summary_delta(
                overlay_summary, baseline_summary, "return_pct"
            ),
            "max_drawdown_pct": _summary_delta(
                overlay_summary, baseline_summary, "max_drawdown_pct"
            ),
            "filled_orders": _summary_delta(
                overlay_summary, baseline_summary, "filled_orders"
            ),
            "blocked_actions": _summary_delta(
                overlay_summary, baseline_summary, "blocked_actions"
            ),
            "clamped_actions": _summary_delta(
                overlay_summary, baseline_summary, "clamped_actions"
            ),
        },
        "overlay_interventions": overlay_counts,
        "promotion_checks": _build_overlay_promotion_checks(
            baseline_summary,
            overlay_summary,
            overlay_counts,
        ),
    }
    return MarketRegimeOverlayBacktestResult(
        generated_at=datetime.now(UTC),
        params=params,
        summary=summary,
        baseline=baseline,
        overlay=overlay,
    )


def _build_overlay_promotion_checks(
    baseline_summary: Mapping[str, Any],
    overlay_summary: Mapping[str, Any],
    overlay_counts: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "return_preserved_or_improved": {
            "passed": float(overlay_summary["return_pct"])
            >= float(baseline_summary["return_pct"]),
            "baseline": baseline_summary["return_pct"],
            "overlay": overlay_summary["return_pct"],
        },
        "drawdown_improved_or_preserved": {
            "passed": float(overlay_summary["max_drawdown_pct"])
            <= float(baseline_summary["max_drawdown_pct"]),
            "baseline": baseline_summary["max_drawdown_pct"],
            "overlay": overlay_summary["max_drawdown_pct"],
        },
        "no_weak_signal_regression": {
            "passed": not (
                baseline_summary.get("trust_level") == "decision_helpful"
                and overlay_summary.get("trust_level") == "weak_signal"
            ),
            "baseline": baseline_summary.get("trust_level"),
            "overlay": overlay_summary.get("trust_level"),
        },
        "interventions_have_reasons": {
            "passed": bool(overlay_counts.get("reason_counts"))
            or int(overlay_counts.get("overlay_interventions", 0) or 0) == 0,
            "reason_counts": copy.deepcopy(overlay_counts.get("reason_counts") or {}),
        },
    }
