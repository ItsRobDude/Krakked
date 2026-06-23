"""Shared strategy evaluation diagnostics."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Tuple

from .models import StrategyIntent

StrategyEvaluationStatus = Literal[
    "awaiting_evaluation",
    "data_stale",
    "deferred_no_new_bar",
    "disabled",
    "intents_emitted",
    "intents_score_filtered",
    "invalid_bar_timestamp",
    "no_data",
    "no_pairs",
    "no_signal",
    "not_evaluated",
    "strategy_error",
]

STRATEGY_EVALUATION_INT_FIELDS: Tuple[str, ...] = (
    "cycles_evaluated",
    "contexts_evaluated",
    "intents_emitted",
    "actions_after_scoring",
    "filtered_by_score",
    "filtered_no_position_exits",
    "filtered_position_exits",
    "filtered_low_score_entries",
    "blocked_actions",
    "data_stale_contexts",
    "deferred_no_new_bar_contexts",
    "no_data_contexts",
    "invalid_bar_timestamp_contexts",
    "fresh_contexts_evaluated",
    "strategy_error_contexts",
    "skipped_no_pairs",
    "skipped_stale_timeframe_contexts",
)


@dataclass
class StrategyEvaluationResult:
    intents: List[StrategyIntent] = field(default_factory=list)
    no_signal_reasons: List[Dict[str, Any]] = field(default_factory=list)
    context_summaries: List[Dict[str, Any]] = field(default_factory=list)
    status: Optional[StrategyEvaluationStatus] = None
    message: Optional[str] = None


def new_strategy_evaluation_entry() -> Dict[str, Any]:
    return {
        "cycles_evaluated": 0,
        "contexts_evaluated": 0,
        "timeframes_evaluated": [],
        "intents_emitted": 0,
        "actions_after_scoring": 0,
        "filtered_by_score": 0,
        "filtered_no_position_exits": 0,
        "filtered_position_exits": 0,
        "filtered_low_score_entries": 0,
        "min_score": None,
        "max_score": None,
        "score_threshold": None,
        "blocked_actions": 0,
        "data_stale_contexts": 0,
        "deferred_no_new_bar_contexts": 0,
        "no_data_contexts": 0,
        "invalid_bar_timestamp_contexts": 0,
        "fresh_contexts_evaluated": 0,
        "strategy_error_contexts": 0,
        "context_summaries": [],
        "intent_summaries": [],
        "no_signal_reasons": [],
        "last_evaluation_summary": None,
        "skipped_no_pairs": 0,
        # Compatibility alias. New code should read deferred_no_new_bar_contexts.
        "skipped_stale_timeframe_contexts": 0,
    }
