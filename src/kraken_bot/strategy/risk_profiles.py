"""Risk profile presets for strategies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict


@dataclass(frozen=True)
class RiskProfileDefinition:
    """Mapping of a risk profile to allocation constraints."""

    max_per_strategy_pct: float
    risk_per_trade_pct: float


RISK_PROFILES: Dict[str, RiskProfileDefinition] = {
    "conservative": RiskProfileDefinition(
        max_per_strategy_pct=5.0,
        risk_per_trade_pct=0.25,
    ),
    "balanced": RiskProfileDefinition(
        max_per_strategy_pct=10.0,
        risk_per_trade_pct=0.5,
    ),
    "aggressive": RiskProfileDefinition(
        max_per_strategy_pct=20.0,
        risk_per_trade_pct=1.0,
    ),
}


def profile_to_definition(profile: str) -> RiskProfileDefinition:
    """Return the configured :class:`RiskProfileDefinition` for a profile name."""

    return RISK_PROFILES.get(profile, RISK_PROFILES["balanced"])
