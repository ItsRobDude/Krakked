"""Runtime market-regime throttle comparison for offline replays."""

from __future__ import annotations

import copy
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Mapping, Sequence

from krakked.config import AppConfig, MarketRegimeThrottleConfig
from krakked.market_regime import (
    MarketRegimeOverlayParams,
    _as_utc,
    _clean_pairs,
    _default_pairs,
)
from krakked.strategy.models import ExecutionPlan

from .runner import BacktestResult, _default_backtest_timeframes, run_backtest

REPORT_TYPE_THROTTLE_BACKTEST = "market_regime_throttle_backtest"
REPORT_VERSION = 1


@dataclass
class MarketRegimeThrottleBacktestResult:
    generated_at: datetime
    params: MarketRegimeOverlayParams
    unavailable_policy: str
    summary: dict[str, Any]
    baseline: BacktestResult
    throttle: BacktestResult

    def to_report_dict(self) -> dict[str, Any]:
        return {
            "report_version": REPORT_VERSION,
            "report_type": REPORT_TYPE_THROTTLE_BACKTEST,
            "generated_at": self.generated_at.astimezone(UTC).isoformat(),
            "summary": copy.deepcopy(self.summary),
            "baseline": self.baseline.to_report_dict(),
            "throttle": self.throttle.to_report_dict(),
        }


def run_market_regime_throttle_backtest(
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
    warmup_days: float | None = None,
    unavailable_policy: str = "block_new_risk",
) -> MarketRegimeThrottleBacktestResult:
    """Compare normal replay against the real runtime throttle risk plumbing."""

    start = _as_utc(start)
    end = _as_utc(end)
    params = params or _params_from_runtime_throttle(config)
    selected_pairs = _clean_pairs(list(pairs or _default_pairs(config)))
    if params.benchmark_pair not in selected_pairs:
        selected_pairs.insert(0, params.benchmark_pair)

    frames_arg = (
        list(timeframes) if timeframes else _default_backtest_timeframes(config)
    )
    if params.timeframe not in frames_arg:
        frames_arg.append(params.timeframe)

    baseline_config = _config_with_runtime_throttle(
        config,
        enabled=False,
        params=params,
        pairs=selected_pairs,
        unavailable_policy=unavailable_policy,
    )
    throttle_config = _config_with_runtime_throttle(
        config,
        enabled=True,
        params=params,
        pairs=selected_pairs,
        unavailable_policy=unavailable_policy,
    )

    baseline = run_backtest(
        baseline_config,
        start=start,
        end=end,
        timeframes=frames_arg,
        starting_cash_usd=starting_cash_usd,
        fee_bps=fee_bps,
        strict_data=strict_data,
        warmup_days=warmup_days,
    )
    throttle = run_backtest(
        throttle_config,
        start=start,
        end=end,
        timeframes=frames_arg,
        starting_cash_usd=starting_cash_usd,
        fee_bps=fee_bps,
        strict_data=strict_data,
        warmup_days=warmup_days,
    )

    if baseline.summary is None or throttle.summary is None:
        raise ValueError("Market regime throttle replay did not produce summaries")

    baseline_summary = baseline.summary.to_dict()
    throttle_summary = throttle.summary.to_dict()
    throttle_counts = summarize_market_regime_throttle_plans(throttle.plans)
    summary = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "pairs": selected_pairs,
        "timeframes": list(frames_arg),
        "params": asdict(params),
        "unavailable_policy": unavailable_policy,
        "baseline": _compact_replay_summary(baseline_summary),
        "throttle": _compact_replay_summary(throttle_summary),
        "delta": {
            "ending_equity_usd": _summary_delta(
                throttle_summary, baseline_summary, "ending_equity_usd"
            ),
            "return_pct": _summary_delta(
                throttle_summary, baseline_summary, "return_pct"
            ),
            "max_drawdown_pct": _summary_delta(
                throttle_summary, baseline_summary, "max_drawdown_pct"
            ),
            "filled_orders": _summary_delta(
                throttle_summary, baseline_summary, "filled_orders"
            ),
            "blocked_actions": _summary_delta(
                throttle_summary, baseline_summary, "blocked_actions"
            ),
            "clamped_actions": _summary_delta(
                throttle_summary, baseline_summary, "clamped_actions"
            ),
        },
        "throttle_interventions": throttle_counts,
        "promotion_checks": _build_runtime_throttle_promotion_checks(
            baseline_summary,
            throttle_summary,
            throttle_counts,
            strict_data=strict_data,
        ),
        "research_only": True,
        "runtime_enablement": "blocked_pending_operator_review",
    }
    return MarketRegimeThrottleBacktestResult(
        generated_at=datetime.now(UTC),
        params=params,
        unavailable_policy=unavailable_policy,
        summary=summary,
        baseline=baseline,
        throttle=throttle,
    )


def summarize_market_regime_throttle_plans(
    plans: Sequence[ExecutionPlan],
) -> dict[str, Any]:
    state_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    cycles_with_metadata = 0
    unavailable_cycles = 0
    throttled_actions = 0
    blocked_actions = 0
    clamped_actions = 0
    intervention_cycles = 0

    for plan in plans:
        metadata = plan.metadata or {}
        if "market_regime_throttle" not in metadata:
            continue
        payload = metadata.get("market_regime_throttle") or {}
        if not isinstance(payload, dict):
            continue

        cycles_with_metadata += 1
        regime = str(payload.get("regime") or "unknown")
        state_counts[regime] += 1
        if not bool(payload.get("available", True)):
            unavailable_cycles += 1
        reason_counts.update(
            str(reason) for reason in (payload.get("reason_codes") or []) if str(reason)
        )
        cycle_throttled = int(payload.get("throttled_actions", 0) or 0)
        cycle_blocked = int(payload.get("blocked_actions", 0) or 0)
        cycle_clamped = int(payload.get("clamped_actions", 0) or 0)
        throttled_actions += cycle_throttled
        blocked_actions += cycle_blocked
        clamped_actions += cycle_clamped
        if cycle_throttled or cycle_blocked or cycle_clamped:
            intervention_cycles += 1

    return {
        "cycles_with_metadata": cycles_with_metadata,
        "state_counts": dict(sorted(state_counts.items())),
        "reason_counts": dict(reason_counts.most_common()),
        "unavailable_cycles": unavailable_cycles,
        "throttled_actions": throttled_actions,
        "blocked_actions": blocked_actions,
        "clamped_actions": clamped_actions,
        "intervention_cycles": intervention_cycles,
    }


def _params_from_runtime_throttle(config: AppConfig) -> MarketRegimeOverlayParams:
    throttle = getattr(config.risk, "market_regime_throttle", None)
    if throttle is None:
        throttle = MarketRegimeThrottleConfig()

    return MarketRegimeOverlayParams(
        timeframe=throttle.timeframe,
        benchmark_pair=throttle.benchmark_pair,
        momentum_lookback_bars=throttle.momentum_lookback_bars,
        basket_momentum_lookback_bars=throttle.basket_momentum_lookback_bars,
        volatility_lookback_bars=throttle.volatility_lookback_bars,
        drawdown_lookback_bars=throttle.drawdown_lookback_bars,
        neutral_allocation_multiplier=throttle.neutral_allocation_multiplier,
        risk_off_allocation_multiplier=throttle.risk_off_allocation_multiplier,
        neutral_benchmark_momentum_bps=throttle.neutral_benchmark_momentum_bps,
        neutral_basket_momentum_bps=throttle.neutral_basket_momentum_bps,
        risk_off_benchmark_momentum_bps=throttle.risk_off_benchmark_momentum_bps,
        risk_off_basket_momentum_bps=throttle.risk_off_basket_momentum_bps,
        neutral_benchmark_drawdown_pct=throttle.neutral_benchmark_drawdown_pct,
        risk_off_benchmark_drawdown_pct=throttle.risk_off_benchmark_drawdown_pct,
        neutral_volatility_pct=throttle.neutral_volatility_pct,
        risk_off_volatility_pct=throttle.risk_off_volatility_pct,
    )


def _config_with_runtime_throttle(
    config: AppConfig,
    *,
    enabled: bool,
    params: MarketRegimeOverlayParams,
    pairs: Sequence[str],
    unavailable_policy: str,
) -> AppConfig:
    config_copy = copy.deepcopy(config)
    config_copy.risk.market_regime_throttle = MarketRegimeThrottleConfig(
        enabled=enabled,
        mode="target_scale",
        timeframe=params.timeframe,
        benchmark_pair=params.benchmark_pair,
        pairs=list(pairs),
        momentum_lookback_bars=params.momentum_lookback_bars,
        basket_momentum_lookback_bars=params.basket_momentum_lookback_bars,
        volatility_lookback_bars=params.volatility_lookback_bars,
        drawdown_lookback_bars=params.drawdown_lookback_bars,
        neutral_allocation_multiplier=params.neutral_allocation_multiplier,
        risk_off_allocation_multiplier=params.risk_off_allocation_multiplier,
        neutral_benchmark_momentum_bps=params.neutral_benchmark_momentum_bps,
        neutral_basket_momentum_bps=params.neutral_basket_momentum_bps,
        risk_off_benchmark_momentum_bps=params.risk_off_benchmark_momentum_bps,
        risk_off_basket_momentum_bps=params.risk_off_basket_momentum_bps,
        neutral_benchmark_drawdown_pct=params.neutral_benchmark_drawdown_pct,
        risk_off_benchmark_drawdown_pct=params.risk_off_benchmark_drawdown_pct,
        neutral_volatility_pct=params.neutral_volatility_pct,
        risk_off_volatility_pct=params.risk_off_volatility_pct,
        unavailable_policy=unavailable_policy,
    )
    return config_copy


def _compact_replay_summary(summary: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "ending_equity_usd": summary.get("ending_equity_usd"),
        "return_pct": summary.get("return_pct"),
        "max_drawdown_pct": summary.get("max_drawdown_pct"),
        "total_actions": summary.get("total_actions"),
        "blocked_actions": summary.get("blocked_actions"),
        "clamped_actions": summary.get("clamped_actions", 0),
        "total_orders": summary.get("total_orders"),
        "filled_orders": summary.get("filled_orders"),
        "execution_errors": summary.get("execution_errors"),
        "trust_level": summary.get("trust_level"),
        "trust_note": summary.get("trust_note"),
    }


def _summary_delta(
    throttle_summary: Mapping[str, Any],
    baseline_summary: Mapping[str, Any],
    key: str,
) -> float:
    return float(throttle_summary.get(key, 0.0) or 0.0) - float(
        baseline_summary.get(key, 0.0) or 0.0
    )


def _trust_rank(value: Any) -> int:
    return {"weak_signal": 0, "limited": 1, "decision_helpful": 2}.get(
        str(value or ""),
        -1,
    )


def _preflight_ready(summary: Mapping[str, Any]) -> bool:
    return not summary.get("missing_series") and not summary.get("partial_series")


def _build_runtime_throttle_promotion_checks(
    baseline_summary: Mapping[str, Any],
    throttle_summary: Mapping[str, Any],
    throttle_counts: Mapping[str, Any],
    *,
    strict_data: bool,
) -> dict[str, Any]:
    baseline_actions = int(baseline_summary.get("total_actions", 0) or 0)
    throttle_actions = int(throttle_summary.get("total_actions", 0) or 0)
    baseline_fills = int(baseline_summary.get("filled_orders", 0) or 0)
    throttle_fills = int(throttle_summary.get("filled_orders", 0) or 0)
    execution_errors = int(baseline_summary.get("execution_errors", 0) or 0) + int(
        throttle_summary.get("execution_errors", 0) or 0
    )
    interventions = int(throttle_counts.get("throttled_actions", 0) or 0)
    reason_counts = copy.deepcopy(throttle_counts.get("reason_counts") or {})
    checks: dict[str, Any] = {
        "actual_strategy_actions": {
            "passed": baseline_actions > 0 or throttle_actions > 0,
            "baseline": baseline_actions,
            "throttle": throttle_actions,
        },
        "actual_filled_orders": {
            "passed": baseline_fills > 0 or throttle_fills > 0,
            "baseline": baseline_fills,
            "throttle": throttle_fills,
        },
        "no_execution_errors": {
            "passed": execution_errors == 0,
            "execution_errors": execution_errors,
        },
        "data_ready": {
            "passed": _preflight_ready(baseline_summary)
            and _preflight_ready(throttle_summary),
            "strict_data_requested": bool(strict_data),
            "baseline_missing": list(baseline_summary.get("missing_series") or []),
            "baseline_partial": list(baseline_summary.get("partial_series") or []),
            "throttle_missing": list(throttle_summary.get("missing_series") or []),
            "throttle_partial": list(throttle_summary.get("partial_series") or []),
        },
        "trust_not_weaker": {
            "passed": _trust_rank(throttle_summary.get("trust_level"))
            >= _trust_rank(baseline_summary.get("trust_level")),
            "baseline": baseline_summary.get("trust_level"),
            "throttle": throttle_summary.get("trust_level"),
        },
        "interventions_have_reasons": {
            "passed": bool(reason_counts) or interventions == 0,
            "reason_counts": reason_counts,
            "throttled_actions": interventions,
        },
    }
    passed = all(
        bool(payload.get("passed"))
        for name, payload in checks.items()
        if name != "passed" and isinstance(payload, dict)
    )
    checks["passed"] = passed
    return checks
