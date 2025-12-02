from datetime import datetime, timezone
from unittest.mock import MagicMock

from kraken_bot.config import OHLCBar, StrategyConfig
from kraken_bot.market_data.api import MarketDataAPI
from kraken_bot.portfolio.manager import PortfolioService
from kraken_bot.strategy.base import StrategyContext
from kraken_bot.strategy.strategies.vol_breakout import VolBreakoutStrategy


def _make_bar(ts: int, close: float, high_offset: float, low_offset: float) -> OHLCBar:
    return OHLCBar(
        timestamp=ts,
        open=close,
        high=close + high_offset,
        low=close - low_offset,
        close=close,
        volume=1.0,
    )


def _build_context():
    market = MagicMock(spec=MarketDataAPI)
    portfolio = MagicMock(spec=PortfolioService)
    ctx = StrategyContext(
        now=datetime.now(timezone.utc),
        universe=["XBTUSD"],
        market_data=market,
        portfolio=portfolio,
        timeframe="1h",
    )
    return ctx, market


def test_vol_breakout_strategy_handles_compression_and_breakout():
    cfg = StrategyConfig(
        name="vol_breakout",
        type="vol_breakout",
        enabled=True,
        params={
            "pairs": ["XBTUSD"],
            "timeframes": ["1h"],
            "lookback_bars": 10,
            "min_compression_bps": 5.0,
            "breakout_multiple": 1.5,
        },
        userref=999,
    )
    strat = VolBreakoutStrategy(cfg)

    ctx, market = _build_context()

    compressed_skip_bars = [
        _make_bar(ts, 100 + ts * 0.01, 0.05, 0.05)
        for ts in range(12)
    ]
    market.get_ohlc.return_value = compressed_skip_bars

    intents = strat.generate_intents(ctx)

    assert intents == []

    compressed_breakout_bars = [_make_bar(ts, 100.001, 0.0005, 0.0005) for ts in range(11)]
    compressed_breakout_bars.append(_make_bar(11, 100.0035, 0.0002, 0.0006))
    market.get_ohlc.return_value = compressed_breakout_bars

    intents = strat.generate_intents(ctx)

    assert len(intents) == 1
    intent = intents[0]
    assert intent.pair == "XBTUSD"
    assert intent.side == "long"
    assert intent.intent_type == "enter"
    assert intent.desired_exposure_usd is None


def test_vol_breakout_strategy_requires_sufficient_bars():
    cfg = StrategyConfig(
        name="vol_breakout",
        type="vol_breakout",
        enabled=True,
        params={
            "pairs": ["XBTUSD"],
            "timeframes": ["1h"],
            "lookback_bars": 10,
            "min_compression_bps": 5.0,
            "breakout_multiple": 1.5,
        },
        userref=999,
    )
    strat = VolBreakoutStrategy(cfg)

    ctx, market = _build_context()
    market.get_ohlc.return_value = [_make_bar(ts, 100.0, 0.01, 0.01) for ts in range(5)]

    intents = strat.generate_intents(ctx)

    assert intents == []
