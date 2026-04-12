# src/krakked/strategy/allocator.py

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from krakked.config import RiskConfig

from .performance import StrategyPerformance
from .regime import MarketRegime, RegimeSnapshot


@dataclass
class StrategyWeights:
    per_strategy_pct: Dict[str, float]

    def factor_for(self, strategy_id: str) -> float:
        """Return a score multiplier relative to an equal-weight baseline."""

        if not self.per_strategy_pct:
            return 1.0

        pct = self.per_strategy_pct.get(strategy_id)
        if pct is None:
            return 1.0

        baseline_pct = 100.0 / len(self.per_strategy_pct)
        if baseline_pct <= 0:
            return 1.0

        return pct / baseline_pct


def _normalize_percentages(scores: Dict[str, float]) -> StrategyWeights:
    total = sum(max(score, 0.0) for score in scores.values())
    if total <= 0:
        return StrategyWeights(per_strategy_pct={})

    return StrategyWeights(
        per_strategy_pct={
            strategy_id: (max(score, 0.0) / total) * 100.0
            for strategy_id, score in scores.items()
        }
    )


def compute_manual_weights(configured_weights: Dict[str, int]) -> StrategyWeights:
    if not configured_weights:
        return StrategyWeights(per_strategy_pct={})

    scores = {
        strategy_id: float(max(weight, 1))
        for strategy_id, weight in configured_weights.items()
    }
    return _normalize_percentages(scores)


def combine_weights(
    manual: StrategyWeights,
    dynamic: StrategyWeights | None,
) -> StrategyWeights:
    if not manual.per_strategy_pct:
        return StrategyWeights(per_strategy_pct={})

    if dynamic is None or not dynamic.per_strategy_pct:
        return manual

    active_count = len(manual.per_strategy_pct)
    neutral_dynamic_pct = 100.0 / active_count if active_count else 100.0

    combined_scores: Dict[str, float] = {}
    for strategy_id, manual_pct in manual.per_strategy_pct.items():
        dynamic_pct = dynamic.per_strategy_pct.get(strategy_id, neutral_dynamic_pct)
        combined_scores[strategy_id] = manual_pct * dynamic_pct

    return _normalize_percentages(combined_scores)


def _dominant_regime(regime: RegimeSnapshot) -> MarketRegime:
    if not regime.per_pair:
        return MarketRegime.CHOPPY

    counts: Dict[MarketRegime, int] = {}
    for value in regime.per_pair.values():
        counts[value] = counts.get(value, 0) + 1

    return max(counts, key=lambda regime: counts[regime])


def _preferred_regime(strategy_id: str) -> MarketRegime | None:
    lowered = strategy_id.lower()
    if "trend" in lowered:
        return MarketRegime.TRENDING
    if "mean" in lowered:
        return MarketRegime.MEAN_REVERTING
    return None


def compute_weights(
    performance: Dict[str, StrategyPerformance],
    regime: RegimeSnapshot,
    config: RiskConfig,
) -> StrategyWeights:
    if not performance:
        return StrategyWeights(per_strategy_pct={})

    dominant = _dominant_regime(regime)
    scores: Dict[str, float] = {}

    for strategy_id, stats in performance.items():
        score = 1.0

        if stats.realized_pnl_quote < 0:
            score *= 0.7
        elif stats.realized_pnl_quote > 0:
            score *= 1.1

        preferred = _preferred_regime(strategy_id)
        if dominant == MarketRegime.PANIC:
            score *= 0.6
        elif preferred is not None:
            if dominant == preferred:
                score *= 1.1
            else:
                score *= 0.9

        scores[strategy_id] = max(score, 0.01)

    normalized = _normalize_percentages(scores)
    weights: Dict[str, float] = {}

    for strategy_id, pct in normalized.per_strategy_pct.items():
        pct = max(pct, config.min_strategy_weight_pct)
        pct = min(pct, config.max_strategy_weight_pct)
        weights[strategy_id] = pct

    return StrategyWeights(per_strategy_pct=weights)
