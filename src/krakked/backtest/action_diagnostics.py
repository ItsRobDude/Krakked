"""Research-only action-quality diagnostics for offline replay windows."""

from __future__ import annotations

import copy
import re
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Mapping, Sequence

from krakked.config import AppConfig
from krakked.strategy.models import ExecutionPlan, RiskAdjustedAction

from .runner import BacktestResult, run_backtest
from .strategy_activity import apply_strategy_activity_override

REPORT_TYPE_STRATEGY_ACTION_DIAGNOSTICS = "strategy_action_diagnostics"
REPORT_VERSION = 1


@dataclass
class StrategyActionDiagnosticsResult:
    generated_at: datetime
    summary: dict[str, Any]
    strategy_diagnostics: list[dict[str, Any]]
    pair_diagnostics: list[dict[str, Any]]
    fill_tape: list[dict[str, Any]]
    cycle_diagnostics: dict[str, Any]
    preflight: dict[str, Any] | None

    def to_report_dict(self) -> dict[str, Any]:
        return {
            "report_version": REPORT_VERSION,
            "report_type": REPORT_TYPE_STRATEGY_ACTION_DIAGNOSTICS,
            "generated_at": self.generated_at.astimezone(UTC).isoformat(),
            "summary": copy.deepcopy(self.summary),
            "strategy_diagnostics": copy.deepcopy(self.strategy_diagnostics),
            "pair_diagnostics": copy.deepcopy(self.pair_diagnostics),
            "fill_tape": copy.deepcopy(self.fill_tape),
            "cycle_diagnostics": copy.deepcopy(self.cycle_diagnostics),
            "preflight": copy.deepcopy(self.preflight),
        }


def run_strategy_action_diagnostics(
    config: AppConfig,
    *,
    start: datetime,
    end: datetime,
    strategies: Sequence[str] | None = None,
    timeframes: Sequence[str] | None = None,
    starting_cash_usd: float = 10_000.0,
    fee_bps: float = 25.0,
    strict_data: bool = False,
    warmup_days: float | None = None,
    max_fill_rows: int = 100,
) -> StrategyActionDiagnosticsResult:
    """Run one replay window and summarize where action quality is breaking down."""

    config_for_run = (
        apply_strategy_activity_override(config, strategies) if strategies else config
    )
    result = run_backtest(
        config_for_run,
        start=start,
        end=end,
        timeframes=list(timeframes) if timeframes else None,
        starting_cash_usd=starting_cash_usd,
        fee_bps=fee_bps,
        strict_data=strict_data,
        warmup_days=warmup_days,
    )
    return build_strategy_action_diagnostics(
        result,
        selected_strategies=list(strategies or []),
        selected_timeframes=list(timeframes or []),
        fee_bps=fee_bps,
        max_fill_rows=max_fill_rows,
    )


def build_strategy_action_diagnostics(
    result: BacktestResult,
    *,
    selected_strategies: Sequence[str] | None = None,
    selected_timeframes: Sequence[str] | None = None,
    fee_bps: float = 25.0,
    max_fill_rows: int = 100,
) -> StrategyActionDiagnosticsResult:
    """Build a report payload from an already-run backtest result."""

    if result.summary is None:
        raise ValueError("Strategy action diagnostics require a backtest summary")

    summary = result.summary.to_dict()
    strategy_rows = _strategy_rows(result.plans, summary)
    pair_rows = _pair_rows(result.plans, result.executions, fee_bps=fee_bps)
    fill_tape, fill_summary = _fill_tape(
        result.plans,
        result.executions,
        fee_bps=fee_bps,
        max_rows=max_fill_rows,
    )
    cycle_diagnostics = _cycle_diagnostics(result.plans, result.executions)

    diagnostic_summary = {
        "research_only": True,
        "runtime_config_changed": False,
        "start": summary["start"],
        "end": summary["end"],
        "selected_strategies": list(selected_strategies or []),
        "selected_timeframes": list(selected_timeframes or []),
        "trust_level": summary.get("trust_level"),
        "trust_note": summary.get("trust_note"),
        "warmup_status": summary.get("warmup_status"),
        "warmup_days": summary.get("warmup_days"),
        "total_cycles": summary.get("total_cycles"),
        "total_actions": summary.get("total_actions"),
        "blocked_actions": summary.get("blocked_actions"),
        "clamped_actions": summary.get("clamped_actions"),
        "none_actions": sum(
            1
            for plan in result.plans
            for action in plan.actions
            if action.action_type == "none"
        ),
        "executable_actions": sum(
            1
            for plan in result.plans
            for action in plan.actions
            if not action.blocked and action.action_type != "none"
        ),
        "total_orders": summary.get("total_orders"),
        "filled_orders": summary.get("filled_orders"),
        "rejected_orders": summary.get("rejected_orders"),
        "execution_errors": summary.get("execution_errors"),
        "return_pct": summary.get("return_pct"),
        "max_drawdown_pct": summary.get("max_drawdown_pct"),
        "realized_pnl_usd": summary.get("realized_pnl_usd"),
        "approx_fill_realized_pnl_usd": fill_summary["approx_realized_pnl_usd"],
        "gross_turnover_usd": fill_summary["gross_turnover_usd"],
        "blocked_reason_buckets": _reason_counts(
            result.plans,
            flag_attr="blocked",
            fallback_reason="Blocked by risk guardrails",
        ),
        "clamped_reason_buckets": _reason_counts(
            result.plans,
            flag_attr="clamped",
            fallback_reason="Clamped by risk guardrails",
        ),
        "none_reason_buckets": _none_reason_counts(result.plans),
        "action_type_counts": _action_type_counts(result.plans),
        "order_status_counts": _order_status_counts(result.executions),
        "stage_assessment": _stage_assessment(summary),
    }

    return StrategyActionDiagnosticsResult(
        generated_at=datetime.now(UTC),
        summary=diagnostic_summary,
        strategy_diagnostics=strategy_rows,
        pair_diagnostics=pair_rows,
        fill_tape=fill_tape,
        cycle_diagnostics=cycle_diagnostics,
        preflight=(
            result.preflight.to_dict() if result.preflight is not None else None
        ),
    )


def normalize_action_reason(reason: str | None) -> str:
    """Collapse amount-specific risk strings into stable diagnostic buckets."""

    text = str(reason or "").strip()
    if not text:
        return "Unspecified"

    if "Strategy " in text and " budget exceeded" in text:
        return "Strategy budget exceeded"
    if "Max open positions reached" in text:
        return "Max open positions reached"
    if "Max per asset" in text:
        return "Max per asset exposure exceeded"
    if "Max total exposure" in text:
        return "Max total exposure exceeded"
    if "Below min_liquidity_24h_usd" in text:
        return "Below minimum liquidity"
    if "Market-regime throttle" in text:
        return "Market-regime throttle"
    if text.startswith("Dust:") or ". Dust:" in text:
        return "Dust below exchange minimum"
    if "Missing price data" in text:
        return "Missing price data"
    if "Stale price data" in text:
        return "Stale price data"
    if "kill switch" in text.lower():
        return "Kill switch"

    text = re.sub(r"\([^)]*\d[^)]*\)", "(...)", text)
    text = re.sub(r"\$?\d[\d,]*(?:\.\d+)?%?", "<n>", text)
    return re.sub(r"\s+", " ", text).strip()


def _strategy_rows(
    plans: Sequence[ExecutionPlan],
    summary: Mapping[str, Any],
) -> list[dict[str, Any]]:
    per_strategy = summary.get("per_strategy") or {}
    strategies = sorted(
        set(str(strategy_id) for strategy_id in per_strategy)
        | {
            str(action.strategy_id)
            for plan in plans
            for action in plan.actions
            if action.strategy_id
        }
    )
    rows: list[dict[str, Any]] = []
    for strategy_id in strategies:
        actions = [
            action
            for plan in plans
            for action in plan.actions
            if str(action.strategy_id) == strategy_id
        ]
        engine = copy.deepcopy(per_strategy.get(strategy_id) or {})
        rows.append(
            {
                "strategy_id": strategy_id,
                "total_actions": len(actions),
                "blocked_actions": sum(1 for action in actions if action.blocked),
                "clamped_actions": sum(1 for action in actions if action.clamped),
                "none_actions": sum(
                    1 for action in actions if action.action_type == "none"
                ),
                "executable_actions": sum(
                    1
                    for action in actions
                    if not action.blocked and action.action_type != "none"
                ),
                "action_type_counts": _counter_dict(
                    action.action_type for action in actions
                ),
                "blocked_reason_buckets": _reason_counts_for_actions(
                    actions,
                    flag_attr="blocked",
                    fallback_reason="Blocked by risk guardrails",
                ),
                "clamped_reason_buckets": _reason_counts_for_actions(
                    actions,
                    flag_attr="clamped",
                    fallback_reason="Clamped by risk guardrails",
                ),
                "engine_summary": engine,
            }
        )
    return rows


def _pair_rows(
    plans: Sequence[ExecutionPlan],
    executions: Sequence[Any],
    *,
    fee_bps: float,
) -> list[dict[str, Any]]:
    actions_by_pair: dict[str, list[RiskAdjustedAction]] = {}
    for plan in plans:
        for action in plan.actions:
            actions_by_pair.setdefault(action.pair, []).append(action)

    fill_metrics = _fill_metrics_by_pair(executions, fee_bps=fee_bps)
    pairs = sorted(set(actions_by_pair) | set(fill_metrics))
    rows: list[dict[str, Any]] = []
    for pair in pairs:
        actions = actions_by_pair.get(pair, [])
        fills = fill_metrics.get(pair, {})
        rows.append(
            {
                "pair": pair,
                "total_actions": len(actions),
                "blocked_actions": sum(1 for action in actions if action.blocked),
                "clamped_actions": sum(1 for action in actions if action.clamped),
                "none_actions": sum(
                    1 for action in actions if action.action_type == "none"
                ),
                "executable_actions": sum(
                    1
                    for action in actions
                    if not action.blocked and action.action_type != "none"
                ),
                "action_type_counts": _counter_dict(
                    action.action_type for action in actions
                ),
                "blocked_reason_buckets": _reason_counts_for_actions(
                    actions,
                    flag_attr="blocked",
                    fallback_reason="Blocked by risk guardrails",
                ),
                "clamped_reason_buckets": _reason_counts_for_actions(
                    actions,
                    flag_attr="clamped",
                    fallback_reason="Clamped by risk guardrails",
                ),
                "filled_orders": int(fills.get("filled_orders", 0) or 0),
                "buy_fills": int(fills.get("buy_fills", 0) or 0),
                "sell_fills": int(fills.get("sell_fills", 0) or 0),
                "gross_turnover_usd": float(
                    fills.get("gross_turnover_usd", 0.0) or 0.0
                ),
                "approx_realized_pnl_usd": float(
                    fills.get("approx_realized_pnl_usd", 0.0) or 0.0
                ),
            }
        )
    return rows


def _cycle_diagnostics(
    plans: Sequence[ExecutionPlan],
    executions: Sequence[Any],
) -> dict[str, Any]:
    execution_by_plan = {execution.plan_id: execution for execution in executions}
    action_counts = [len(plan.actions) for plan in plans]
    active_plans = [plan for plan in plans if plan.actions]
    filled_order_cycles = 0
    for plan in plans:
        execution = execution_by_plan.get(plan.plan_id)
        if execution is None:
            continue
        if any(order.status == "filled" for order in execution.orders):
            filled_order_cycles += 1

    top_cycle_rows: list[dict[str, Any]] = []
    for plan in plans:
        if not plan.actions:
            continue
        top_cycle_rows.append(
            {
                "generated_at": plan.generated_at.astimezone(UTC).isoformat(),
                "plan_id": plan.plan_id,
                "total_actions": len(plan.actions),
                "blocked_actions": sum(1 for action in plan.actions if action.blocked),
                "clamped_actions": sum(1 for action in plan.actions if action.clamped),
                "none_actions": sum(
                    1 for action in plan.actions if action.action_type == "none"
                ),
                "filled_orders": sum(
                    1
                    for order in getattr(
                        execution_by_plan.get(plan.plan_id), "orders", []
                    )
                    if order.status == "filled"
                ),
                "action_type_counts": _counter_dict(
                    action.action_type for action in plan.actions
                ),
            }
        )

    top_cycles = sorted(
        top_cycle_rows,
        key=lambda item: (
            -int(item["total_actions"]),
            str(item["generated_at"]),
        ),
    )[:10]

    return {
        "total_cycles": len(plans),
        "active_cycles": len(active_plans),
        "filled_order_cycles": filled_order_cycles,
        "avg_actions_per_cycle": (
            sum(action_counts) / len(action_counts) if action_counts else 0.0
        ),
        "max_actions_per_cycle": max(action_counts) if action_counts else 0,
        "top_action_cycles": top_cycles,
    }


def _fill_tape(
    plans: Sequence[ExecutionPlan],
    executions: Sequence[Any],
    *,
    fee_bps: float,
    max_rows: int,
) -> tuple[list[dict[str, Any]], dict[str, float]]:
    plan_times = {plan.plan_id: plan.generated_at for plan in plans}
    fee_rate = max(float(fee_bps), 0.0) / 10_000.0
    inventory: dict[tuple[str, str], dict[str, float]] = {}
    rows: list[dict[str, Any]] = []
    gross_turnover = 0.0
    realized_pnl = 0.0

    for execution in executions:
        cycle_time = plan_times.get(execution.plan_id)
        for order in execution.orders:
            if order.status != "filled":
                continue
            base = float(
                order.cumulative_base_filled or order.requested_base_size or 0.0
            )
            price = float(order.avg_fill_price or order.requested_price or 0.0)
            notional = base * price
            if base <= 0 or price <= 0:
                continue
            key = (str(order.strategy_id or ""), str(order.pair))
            approx_pnl = 0.0
            gross_turnover += notional

            state = inventory.setdefault(key, {"base": 0.0, "cost": 0.0})
            if order.side == "buy":
                state["base"] += base
                state["cost"] += notional * (1.0 + fee_rate)
            else:
                available_base = max(state["base"], 0.0)
                matched_base = min(base, available_base)
                avg_cost = state["cost"] / available_base if available_base > 0 else 0.0
                proceeds = notional * (1.0 - fee_rate)
                approx_pnl = proceeds - (avg_cost * matched_base)
                realized_pnl += approx_pnl
                state["base"] = max(available_base - matched_base, 0.0)
                state["cost"] = max(state["cost"] - (avg_cost * matched_base), 0.0)

            if len(rows) < max_rows:
                rows.append(
                    {
                        "generated_at": (
                            cycle_time.astimezone(UTC).isoformat()
                            if cycle_time is not None
                            else None
                        ),
                        "plan_id": execution.plan_id,
                        "strategy_id": order.strategy_id,
                        "pair": order.pair,
                        "side": order.side,
                        "base_size": base,
                        "avg_fill_price": price,
                        "notional_usd": notional,
                        "approx_realized_pnl_usd": approx_pnl,
                    }
                )

    return rows, {
        "gross_turnover_usd": gross_turnover,
        "approx_realized_pnl_usd": realized_pnl,
    }


def _fill_metrics_by_pair(
    executions: Sequence[Any],
    *,
    fee_bps: float,
) -> dict[str, dict[str, float]]:
    fee_rate = max(float(fee_bps), 0.0) / 10_000.0
    inventory: dict[tuple[str, str], dict[str, float]] = {}
    metrics: dict[str, dict[str, float]] = {}

    for execution in executions:
        for order in execution.orders:
            if order.status != "filled":
                continue
            pair = str(order.pair)
            base = float(
                order.cumulative_base_filled or order.requested_base_size or 0.0
            )
            price = float(order.avg_fill_price or order.requested_price or 0.0)
            if base <= 0 or price <= 0:
                continue
            notional = base * price
            row = metrics.setdefault(
                pair,
                {
                    "filled_orders": 0.0,
                    "buy_fills": 0.0,
                    "sell_fills": 0.0,
                    "gross_turnover_usd": 0.0,
                    "approx_realized_pnl_usd": 0.0,
                },
            )
            row["filled_orders"] += 1
            row["gross_turnover_usd"] += notional
            if order.side == "buy":
                row["buy_fills"] += 1
            else:
                row["sell_fills"] += 1

            key = (str(order.strategy_id or ""), pair)
            state = inventory.setdefault(key, {"base": 0.0, "cost": 0.0})
            if order.side == "buy":
                state["base"] += base
                state["cost"] += notional * (1.0 + fee_rate)
            else:
                available_base = max(state["base"], 0.0)
                matched_base = min(base, available_base)
                avg_cost = state["cost"] / available_base if available_base > 0 else 0.0
                pnl = (notional * (1.0 - fee_rate)) - (avg_cost * matched_base)
                row["approx_realized_pnl_usd"] += pnl
                state["base"] = max(available_base - matched_base, 0.0)
                state["cost"] = max(state["cost"] - (avg_cost * matched_base), 0.0)

    return metrics


def _reason_counts(
    plans: Sequence[ExecutionPlan],
    *,
    flag_attr: str,
    fallback_reason: str,
) -> dict[str, int]:
    return _reason_counts_for_actions(
        [action for plan in plans for action in plan.actions],
        flag_attr=flag_attr,
        fallback_reason=fallback_reason,
    )


def _reason_counts_for_actions(
    actions: Sequence[RiskAdjustedAction],
    *,
    flag_attr: str,
    fallback_reason: str,
) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for action in actions:
        if not getattr(action, flag_attr, False):
            continue
        reasons = list(action.blocked_reasons or []) or [fallback_reason]
        for reason in reasons:
            counts[normalize_action_reason(reason)] += 1
    return _counter_to_sorted_dict(counts)


def _none_reason_counts(plans: Sequence[ExecutionPlan]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for plan in plans:
        for action in plan.actions:
            if action.action_type != "none":
                continue
            counts[normalize_action_reason(action.reason)] += 1
    return _counter_to_sorted_dict(counts)


def _action_type_counts(plans: Sequence[ExecutionPlan]) -> dict[str, int]:
    return _counter_dict(
        action.action_type for plan in plans for action in plan.actions
    )


def _order_status_counts(executions: Sequence[Any]) -> dict[str, int]:
    return _counter_dict(
        order.status for execution in executions for order in execution.orders
    )


def _counter_dict(values: Any) -> dict[str, int]:
    return _counter_to_sorted_dict(Counter(str(value) for value in values))


def _counter_to_sorted_dict(counter: Counter[str]) -> dict[str, int]:
    return dict(sorted(counter.items(), key=lambda item: (-item[1], item[0])))


def _stage_assessment(summary: Mapping[str, Any]) -> str:
    per_strategy = summary.get("per_strategy") or {}
    total_actions = int(summary.get("total_actions", 0) or 0)
    filled_orders = int(summary.get("filled_orders", 0) or 0)
    blocked_actions = int(summary.get("blocked_actions", 0) or 0)
    intents = sum(
        int(payload.get("intents_emitted", 0) or 0)
        for payload in per_strategy.values()
        if isinstance(payload, Mapping)
    )
    actions_after_scoring = sum(
        int(payload.get("actions_after_scoring", 0) or 0)
        for payload in per_strategy.values()
        if isinstance(payload, Mapping)
    )

    if intents <= 0:
        return "no_intents"
    if actions_after_scoring <= 0:
        return "score_filtered"
    if total_actions <= 0:
        return "risk_suppressed"
    if blocked_actions >= total_actions:
        return "fully_blocked"
    if filled_orders <= 0:
        return "no_fills"
    return "filled"
