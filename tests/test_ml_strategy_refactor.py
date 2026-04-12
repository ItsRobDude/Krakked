from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest

from krakked.config import StrategyConfig
from krakked.strategy.base import StrategyContext
from krakked.strategy.ml_models import PassiveAggressiveClassifier
from krakked.strategy.strategies.ml_alt_strategy import AIPredictorAltStrategy
from krakked.strategy.strategies.ml_regression_strategy import AIRegressionStrategy
from krakked.strategy.strategies.ml_strategy import AIPredictorStrategy


@dataclass
class MockBar:
    timestamp: float
    open: float
    high: float
    low: float
    close: float
    volume: float = 100.0
    trades: int = 10


def _make_bars(start_ts, prices):
    bars = []
    for i, p in enumerate(prices):
        bars.append(
            MockBar(
                timestamp=start_ts + i * 3600,
                open=p,
                high=p + 1,
                low=p - 1,
                close=p,
            )
        )
    return bars


@pytest.fixture
def strategy():
    cfg = StrategyConfig(
        name="ai_test",
        type="ai_predictor",
        enabled=True,
        params={
            "pairs": ["XBT/USD"],
            "timeframe": "1h",
            "lookback_bars": 5,
            "short_window": 2,
            "long_window": 5,
            "continuous_learning": True,
        },
    )
    return AIPredictorStrategy(cfg)


@pytest.fixture
def regression_strategy():
    cfg = StrategyConfig(
        name="reg_test",
        type="ai_regression",
        enabled=True,
        params={
            "pairs": ["XBT/USD"],
            "timeframe": "1h",
            "lookback_bars": 5,
            "short_window": 2,
            "long_window": 5,
            "continuous_learning": True,
            "min_edge_pct": 0.05,  # 5% threshold
        },
    )
    return AIRegressionStrategy(cfg)


@pytest.fixture
def mock_ctx():
    ctx = MagicMock(spec=StrategyContext)
    ctx.now = datetime.now(timezone.utc)
    ctx.market_data = MagicMock()
    ctx.portfolio = MagicMock()
    ctx.universe = ["XBT/USD"]
    return ctx


class FakeCheckpointStore:
    def __init__(self) -> None:
        self.live_models: dict[tuple[str, str], tuple[object, datetime]] = {}
        self.training_checkpoints: dict[
            tuple[str, str, str], tuple[object, datetime, str, dict[str, Any]]
        ] = {}

    def load_ml_model(self, strategy_id: str, model_key: str):
        return self.live_models.get((strategy_id, model_key))

    def load_ml_model_checkpoint(
        self, strategy_id: str, model_key: str, *, checkpoint_kind: str
    ):
        return self.training_checkpoints.get((strategy_id, model_key, checkpoint_kind))


def test_extract_training_example(strategy, mock_ctx):
    start_ts = 1000000
    prices = [100.0 + i for i in range(10)]  # 100..109
    bars = _make_bars(start_ts, prices)
    mock_ctx.market_data.get_ohlc.return_value = bars

    features, label = strategy._extract_training_example(mock_ctx, "XBT/USD", "1h")

    assert label == 1.0
    assert len(features) == 3
    mock_ctx.market_data.get_ohlc.assert_called()


def test_extract_training_example_down(strategy, mock_ctx):
    start_ts = 1000000
    prices = [100.0] * 8 + [110.0, 105.0]
    bars = _make_bars(start_ts, prices)
    mock_ctx.market_data.get_ohlc.return_value = bars

    features, label = strategy._extract_training_example(mock_ctx, "XBT/USD", "1h")

    assert label == 0.0


def test_catch_up_model(strategy, mock_ctx):
    strategy.model = MagicMock()
    strategy.model_initialized = True
    strategy.classes = [0, 1]

    now = datetime.fromtimestamp(1000000 + 10 * 3600, tz=timezone.utc)
    last_updated = now - timedelta(hours=5)
    mock_ctx.now = now

    bars = _make_bars(1000000, [100 + i for i in range(20)])
    mock_ctx.market_data.get_ohlc.return_value = bars

    strategy._catch_up_model(mock_ctx, "1h", last_updated)

    assert strategy.model.partial_fit.called
    assert strategy.model.partial_fit.call_count >= 1


def test_generate_intents_trains_and_predicts(strategy, mock_ctx):
    strategy.model = MagicMock()
    strategy.model_initialized = True
    strategy.model.predict.return_value = [1]
    strategy.model.decision_function.return_value = [1.0]

    bars = _make_bars(1000000, [100 + i for i in range(20)])
    mock_ctx.market_data.get_ohlc.return_value = bars
    mock_ctx.market_data.get_latest_price.return_value = 120.0

    intents = strategy.generate_intents(mock_ctx)

    assert strategy.model.partial_fit.called
    assert strategy.model.predict.called
    assert len(intents) == 1
    assert intents[0].side == "long"


def test_regression_extract_training_example(regression_strategy, mock_ctx):
    # Regression label is (Close(T) - Close(T-1)) / Close(T-1)
    start_ts = 1000000
    prices = [100.0] * 8 + [100.0, 110.0]  # T-1=100, T=110. Return = 0.1
    bars = _make_bars(start_ts, prices)
    mock_ctx.market_data.get_ohlc.return_value = bars

    features, label = regression_strategy._extract_training_example(
        mock_ctx, "XBT/USD", "1h"
    )

    assert label == pytest.approx(0.1)
    assert len(features) == 3


def test_regression_min_edge_pct(regression_strategy, mock_ctx):
    # Threshold is 0.05
    regression_strategy.model = MagicMock()
    regression_strategy.model_initialized = True

    bars = _make_bars(1000000, [100 + i for i in range(20)])
    mock_ctx.market_data.get_ohlc.return_value = bars

    # 1. Prediction below threshold (0.04) -> Flat
    regression_strategy.model.predict.return_value = [0.04]
    intents = regression_strategy.generate_intents(mock_ctx)
    assert intents[0].side == "flat"

    # 2. Prediction above threshold (0.06) -> Long
    regression_strategy.model.predict.return_value = [0.06]
    intents = regression_strategy.generate_intents(mock_ctx)
    assert intents[0].side == "long"


def test_classifier_bootstrap_prefers_newer_training_checkpoint(strategy, mock_ctx):
    store = FakeCheckpointStore()
    live_model = PassiveAggressiveClassifier(max_iter=1000, tol=1e-3)
    live_model.partial_fit([[0.0, 0.0, 0.0]], [0], classes=[0, 1])
    checkpoint_model = PassiveAggressiveClassifier(max_iter=1000, tol=1e-3)
    checkpoint_model.partial_fit([[1.0, 1.0, 1.0]], [1], classes=[0, 1])

    strategy_id = strategy.id
    model_key = strategy._model_key("1h")
    store.live_models[(strategy_id, model_key)] = (
        live_model,
        datetime.now(timezone.utc) - timedelta(minutes=5),
    )
    store.training_checkpoints[(strategy_id, model_key, "training")] = (
        checkpoint_model,
        datetime.now(timezone.utc),
        "training",
        {"model_initialized": True},
    )

    mock_ctx.portfolio.store = store
    strategy._maybe_bootstrap_from_history(mock_ctx, "1h")

    assert strategy.model_initialized is True
    assert strategy.model.predict([[0.2, 0.1, 0.3]])[0] == 1


def test_alt_strategy_restores_last_observation_from_checkpoint(mock_ctx):
    cfg = StrategyConfig(
        name="ai_alt_test",
        type="ai_predictor_alt",
        enabled=True,
        params={
            "pairs": ["XBT/USD"],
            "timeframe": "1h",
            "lookback_bars": 5,
            "short_window": 2,
            "long_window": 5,
            "continuous_learning": True,
        },
    )
    strategy = AIPredictorAltStrategy(cfg)
    store = FakeCheckpointStore()
    checkpoint_model = PassiveAggressiveClassifier(max_iter=1000, tol=1e-3)

    model_key = strategy._model_key("XBT/USD", "1h")
    store.training_checkpoints[(strategy.id, model_key, "training")] = (
        checkpoint_model,
        datetime.now(timezone.utc),
        "ready",
        {
            "model_initialized": False,
            "last_observation": {
                "features": [0.1, -0.2, 0.3],
                "price": 123.45,
            },
        },
    )

    mock_ctx.portfolio.store = store
    key = ("XBT/USD", "1h")
    strategy._maybe_bootstrap_from_history(mock_ctx, key, strategy._get_model(key))

    assert strategy.model_initialized[key] is False
    assert strategy._last_observation[key] == ([0.1, -0.2, 0.3], 123.45)
