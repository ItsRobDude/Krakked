from datetime import datetime, timedelta, timezone

import pytest

from krakked.config import RiskConfig
from krakked.strategy.allocator import (
    StrategyWeights,
    combine_weights,
    compute_manual_weights,
    compute_weights,
)
from krakked.strategy.performance import StrategyPerformance
from krakked.strategy.regime import MarketRegime, RegimeSnapshot


def _perf(strategy_id: str, pnl: float) -> StrategyPerformance:
    now = datetime.now(timezone.utc)
    return StrategyPerformance(
        strategy_id=strategy_id,
        realized_pnl_quote=pnl,
        window_start=now - timedelta(hours=72),
        window_end=now,
        trade_count=10,
        win_rate=0.5,
        max_drawdown_pct=5.0,
    )


def test_compute_weights_boosts_aligned_positive_strategies():
    performance = {
        "trend_following": _perf("trend_following", 100.0),
        "mean_reversion": _perf("mean_reversion", -50.0),
    }
    regime = RegimeSnapshot(
        per_pair={"XBTUSD": MarketRegime.TRENDING, "ETHUSD": MarketRegime.TRENDING},
        as_of="now",
    )
    config = RiskConfig(max_strategy_weight_pct=80.0)

    weights = compute_weights(performance, regime, config)

    trend_weight = weights.per_strategy_pct["trend_following"]
    mean_weight = weights.per_strategy_pct["mean_reversion"]

    assert trend_weight > mean_weight
    assert 60.0 < trend_weight < 70.0
    assert 30.0 < mean_weight < 40.0


def test_compute_weights_respects_min_max_caps():
    performance = {
        "trend_following": _perf("trend_following", 0.0),
        "neutral": _perf("neutral", 0.0),
    }
    regime = RegimeSnapshot(per_pair={"XBTUSD": MarketRegime.CHOPPY}, as_of="now")
    config = RiskConfig(max_strategy_weight_pct=30.0, min_strategy_weight_pct=10.0)

    weights = compute_weights(performance, regime, config)

    assert all(weight <= 30.0 for weight in weights.per_strategy_pct.values())
    assert all(weight >= 10.0 for weight in weights.per_strategy_pct.values())


def test_compute_manual_weights_normalizes_user_scale():
    weights = compute_manual_weights({"trend_core": 100, "dca_overlay": 50})

    assert weights.per_strategy_pct["trend_core"] == pytest.approx(66.6666, rel=1e-3)
    assert weights.per_strategy_pct["dca_overlay"] == pytest.approx(33.3333, rel=1e-3)


def test_combine_weights_respects_manual_preference_and_dynamic_signal():
    manual = StrategyWeights(
        per_strategy_pct={"trend_core": 50.0, "mean_reversion": 50.0}
    )
    dynamic = StrategyWeights(
        per_strategy_pct={"trend_core": 80.0, "mean_reversion": 20.0}
    )

    combined = combine_weights(manual, dynamic)

    assert combined.per_strategy_pct["trend_core"] > combined.per_strategy_pct[
        "mean_reversion"
    ]
