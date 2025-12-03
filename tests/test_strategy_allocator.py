from datetime import datetime, timedelta, timezone

from kraken_bot.config import RiskConfig
from kraken_bot.strategy.allocator import compute_weights
from kraken_bot.strategy.performance import StrategyPerformance
from kraken_bot.strategy.regime import MarketRegime, RegimeSnapshot


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
