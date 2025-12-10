
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock
from dataclasses import dataclass

from kraken_bot.config import StrategyConfig
from kraken_bot.strategy.strategies.ml_strategy import AIPredictorStrategy
from kraken_bot.strategy.base import StrategyContext
from kraken_bot.market_data.models import OHLCBar

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
        bars.append(MockBar(
            timestamp=start_ts + i * 3600,
            open=p, high=p+1, low=p-1, close=p,
        ))
    return bars

@pytest.fixture
def strategy():
    cfg = StrategyConfig(name="ai_test", type="ai_predictor", enabled=True, params={
        "pairs": ["XBT/USD"],
        "timeframe": "1h",
        "lookback_bars": 5,
        "short_window": 2,
        "long_window": 5,
        "continuous_learning": True
    })
    return AIPredictorStrategy(cfg)

@pytest.fixture
def mock_ctx():
    ctx = MagicMock(spec=StrategyContext)
    ctx.now = datetime.now(timezone.utc)
    ctx.market_data = MagicMock()
    ctx.portfolio = MagicMock()
    ctx.universe = ["XBT/USD"]
    return ctx

def test_extract_training_example(strategy, mock_ctx):
    # Setup OHLC: 10 bars.
    # T (latest closed) = index 9. T-1 = index 8.
    # Prices: [100, 101, 102, ... 109]
    start_ts = 1000000
    prices = [100.0 + i for i in range(10)] # 100..109
    bars = _make_bars(start_ts, prices)

    mock_ctx.market_data.get_ohlc.return_value = bars

    # We expect features for T-1 (bar index 8, price 108).
    # Label is 1 if Close(T) > Close(T-1) -> 109 > 108 -> 1.0.

    features, label = strategy._extract_training_example(mock_ctx, "XBT/USD", "1h")

    assert label == 1.0
    assert len(features) == 3 # pct_change, trend_diff, volatility

    # Verify features computed on window up to T-1 (index 8).
    # Last close in features should be 108.
    # Check mock call
    mock_ctx.market_data.get_ohlc.assert_called()

def test_extract_training_example_down(strategy, mock_ctx):
    start_ts = 1000000
    prices = [100.0] * 8 + [110.0, 105.0] # T-1=110, T=105
    bars = _make_bars(start_ts, prices)
    mock_ctx.market_data.get_ohlc.return_value = bars

    features, label = strategy._extract_training_example(mock_ctx, "XBT/USD", "1h")

    assert label == 0.0 # 105 < 110

def test_catch_up_model(strategy, mock_ctx):
    # Mock model
    strategy.model = MagicMock()
    strategy.model_initialized = True
    strategy.classes = [0, 1]

    # Gap of 5 hours
    now = datetime.fromtimestamp(1000000 + 10 * 3600, tz=timezone.utc)
    last_updated = now - timedelta(hours=5)
    mock_ctx.now = now

    # Provide enough history to cover gap + lookback
    # 20 bars
    bars = _make_bars(1000000, [100 + i for i in range(20)])
    mock_ctx.market_data.get_ohlc.return_value = bars

    strategy._catch_up_model(mock_ctx, "1h", last_updated)

    # Should call partial_fit for bars in the gap
    assert strategy.model.partial_fit.called
    assert strategy.model.partial_fit.call_count >= 1

def test_generate_intents_trains_and_predicts(strategy, mock_ctx):
    strategy.model = MagicMock()
    strategy.model_initialized = True
    strategy.model.predict.return_value = [1] # Predict UP
    strategy.model.decision_function.return_value = [1.0]

    bars = _make_bars(1000000, [100 + i for i in range(20)])
    mock_ctx.market_data.get_ohlc.return_value = bars
    mock_ctx.market_data.get_latest_price.return_value = 120.0

    intents = strategy.generate_intents(mock_ctx)

    # verify training called (deterministic T-1)
    assert strategy.model.partial_fit.called

    # verify prediction called (T)
    assert strategy.model.predict.called

    assert len(intents) == 1
    assert intents[0].side == "long"
