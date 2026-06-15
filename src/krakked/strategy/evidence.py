"""Conservative strategy evidence labels for operator-facing UI."""

from __future__ import annotations

from typing import TypedDict


class StrategyEvidencePayload(TypedDict):
    evidence_status: str
    evidence_label: str
    evidence_note: str


_EVIDENCE_BY_STRATEGY_ID: dict[str, StrategyEvidencePayload] = {
    "trend_core": {
        "evidence_status": "research_stage",
        "evidence_label": "Research stage",
        "evidence_note": "Replay evidence has not promoted this strategy beyond research-stage operation.",
    },
    "majors_mean_rev": {
        "evidence_status": "research_stage",
        "evidence_label": "Inactive in replay",
        "evidence_note": "Configured starter strategy, but recent replay evidence showed little or no non-cash activity.",
    },
    "vol_breakout": {
        "evidence_status": "data_not_ready",
        "evidence_label": "Data not ready",
        "evidence_note": "Configured for manual research; default replay and backfill do not maintain the required 15m coverage.",
    },
    "rs_rotation": {
        "evidence_status": "research_stage",
        "evidence_label": "Research stage",
        "evidence_note": "Configured but disabled by default after replay evidence failed promotion.",
    },
    "dca_overlay": {
        "evidence_status": "utility",
        "evidence_label": "Utility overlay",
        "evidence_note": "Operational overlay rather than a promoted alpha strategy.",
    },
}

_ML_STRATEGY_IDS = {
    "ai_predictor",
    "ai_predictor_alt",
    "ai_regression",
}


def strategy_evidence_for(strategy_id: str) -> StrategyEvidencePayload:
    if strategy_id in _EVIDENCE_BY_STRATEGY_ID:
        return _EVIDENCE_BY_STRATEGY_ID[strategy_id]
    if strategy_id in _ML_STRATEGY_IDS or strategy_id.startswith("ai_"):
        return {
            "evidence_status": "research_stage",
            "evidence_label": "Research only",
            "evidence_note": "ML strategy lanes are research-only until a pre-registered evidence gate passes.",
        }
    return {
        "evidence_status": "unreviewed",
        "evidence_label": "Unreviewed",
        "evidence_note": "No current strategy evidence label is registered for this strategy.",
    }
