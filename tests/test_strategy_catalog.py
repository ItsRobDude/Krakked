from __future__ import annotations

from krakked.strategy.catalog import ML_STRATEGY_IDS


def test_ml_strategy_ids_include_all_canonical_ml_families() -> None:
    assert "ai_predictor" in ML_STRATEGY_IDS
    assert "ai_predictor_alt" in ML_STRATEGY_IDS
    assert "ai_regression" in ML_STRATEGY_IDS
