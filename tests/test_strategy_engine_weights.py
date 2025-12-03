from datetime import datetime, timezone
from types import SimpleNamespace

from kraken_bot.config import RiskConfig
from kraken_bot.strategy.allocator import StrategyWeights
from kraken_bot.strategy.engine import StrategyEngine
from kraken_bot.strategy.performance import StrategyPerformance
from kraken_bot.strategy.regime import MarketRegime, RegimeSnapshot


def _build_engine(risk_config: RiskConfig, portfolio: object) -> StrategyEngine:
    engine = StrategyEngine.__new__(StrategyEngine)
    engine.config = SimpleNamespace(risk=risk_config)
    engine.portfolio = portfolio
    return engine


def _sample_performance() -> StrategyPerformance:
    now = datetime.now(timezone.utc)
    return StrategyPerformance(
        strategy_id="trend_following",
        realized_pnl_quote=10.0,
        window_start=now,
        window_end=now,
        trade_count=2,
        win_rate=0.5,
        max_drawdown_pct=1.0,
    )


def _regime() -> RegimeSnapshot:
    return RegimeSnapshot(
        per_pair={"XBT/USD": MarketRegime.TRENDING},
        as_of=datetime.now(timezone.utc).isoformat(),
    )


def test_compute_strategy_weights_uses_performance(monkeypatch):
    performance = {"trend_following": _sample_performance()}
    calls: dict = {}

    def fake_get_strategy_performance(window_hours: int):
        calls["window_hours"] = window_hours
        return performance

    def fake_compute_weights(perf, regime, config):
        calls["perf"] = perf
        calls["regime"] = regime
        calls["config"] = config
        return StrategyWeights(per_strategy_pct={"trend_following": 42.0})

    portfolio = SimpleNamespace(get_strategy_performance=fake_get_strategy_performance)
    risk_config = RiskConfig(
        dynamic_allocation_enabled=True,
        dynamic_allocation_lookback_hours=48,
        max_strategy_weight_pct=100.0,
    )
    engine = _build_engine(risk_config, portfolio)

    monkeypatch.setattr(
        "kraken_bot.strategy.engine.compute_weights", fake_compute_weights
    )

    weights = engine._compute_strategy_weights(_regime())

    assert weights.per_strategy_pct == {"trend_following": 42.0}
    assert calls["window_hours"] == 48
    assert calls["perf"] is performance
    assert calls["config"] is risk_config
    assert isinstance(calls["regime"], RegimeSnapshot)


def test_compute_strategy_weights_skips_when_disabled():
    portfolio = SimpleNamespace(
        get_strategy_performance=lambda _: (_ for _ in ()).throw(
            AssertionError("should not be called")
        )
    )
    risk_config = RiskConfig(dynamic_allocation_enabled=False)
    engine = _build_engine(risk_config, portfolio)

    weights = engine._compute_strategy_weights(_regime())

    assert weights is None
