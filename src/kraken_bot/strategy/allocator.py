# src/kraken_bot/strategy/allocator.py

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from kraken_bot.config import RiskConfig

from .performance import StrategyPerformance
from .regime import MarketRegime, RegimeSnapshot


@dataclass
class StrategyWeights:
    per_strategy_pct: Dict[str, float]


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

    total_score = sum(scores.values())
    weights: Dict[str, float] = {}

    for strategy_id, score in scores.items():
        pct = (score / total_score * 100.0) if total_score > 0 else 0.0
        pct = max(pct, config.min_strategy_weight_pct)
        pct = min(pct, config.max_strategy_weight_pct)
        weights[strategy_id] = pct

    return StrategyWeights(per_strategy_pct=weights)
