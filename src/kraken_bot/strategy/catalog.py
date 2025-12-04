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
}

CANONICAL_STRATEGY_TYPES = {
    definition.type for definition in CANONICAL_STRATEGIES.values()
}

__all__ = ["CANONICAL_STRATEGIES", "CANONICAL_STRATEGY_TYPES", "StrategyDefinition"]
