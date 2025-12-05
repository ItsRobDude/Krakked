"""Canonical strategy identifiers and implementation types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict


@dataclass(frozen=True)
class StrategyDefinition:
    """Declare a canonical strategy identifier and its implementation type."""

    strategy_id: str
    type: str
    label: str


CANONICAL_STRATEGIES: Dict[str, StrategyDefinition] = {
    "trend_core": StrategyDefinition(
        strategy_id="trend_core",
        type="trend_following",
        label="Trend Core",
    ),
    "dca_overlay": StrategyDefinition(
        strategy_id="dca_overlay",
        type="dca_rebalance",
        label="DCA Overlay",
    ),
    "vol_breakout": StrategyDefinition(
        strategy_id="vol_breakout",
        type="vol_breakout",
        label="Volatility Breakout",
    ),
    "majors_mean_rev": StrategyDefinition(
        strategy_id="majors_mean_rev",
        type="mean_reversion",
        label="Majors Mean Reversion",
    ),
    "rs_rotation": StrategyDefinition(
        strategy_id="rs_rotation",
        type="relative_strength",
        label="Relative Strength Rotation",
    ),
    "ai_predictor": StrategyDefinition(
        strategy_id="ai_predictor",
        type="machine_learning",
        label="AI Predictor",
    ),
    "ai_predictor_alt": StrategyDefinition(
        strategy_id="ai_predictor_alt",
        type="machine_learning_alt",
        label="AI Predictor (Alt Model)",
    ),
    "ai_regression": StrategyDefinition(
        strategy_id="ai_regression",
        type="machine_learning_regression",
        label="AI Regression (Delta Predictor)",
    ),
}

# Anything whose type is one of the core ML families is considered part of the ML group
# for config-level summaries and tests.
ML_STRATEGY_IDS = [
    sid
    for sid, definition in CANONICAL_STRATEGIES.items()
    if definition.type in ("machine_learning", "machine_learning_regression")
]

CANONICAL_STRATEGY_TYPES = {
    definition.type for definition in CANONICAL_STRATEGIES.values()
}

__all__ = [
    "CANONICAL_STRATEGIES",
    "CANONICAL_STRATEGY_TYPES",
    "ML_STRATEGY_IDS",
    "StrategyDefinition",
]
