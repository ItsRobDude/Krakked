from datetime import datetime, timezone
from unittest.mock import MagicMock

from kraken_bot.config import OHLCBar, StrategyConfig
from kraken_bot.market_data.api import MarketDataAPI
from kraken_bot.portfolio.manager import PortfolioService
from kraken_bot.portfolio.models import SpotPosition
from kraken_bot.strategy.base import StrategyContext
from kraken_bot.strategy.strategies.mean_reversion import MeanReversionStrategy


def _make_bar(ts: int, close: float) -> OHLCBar:
    return OHLCBar(
        timestamp=ts,
        open=close,
        high=close + 1.0,
        low=close - 1.0,
        close=close,
        volume=1.0,
    )


def _build_context():
    market = MagicMock(spec=MarketDataAPI)
    portfolio = MagicMock(spec=PortfolioService)
    ctx = StrategyContext(
        now=datetime.now(timezone.utc),
        universe=["BTC/USD", "ETH/USD"],
        market_data=market,
        portfolio=portfolio,
        timeframe="1h",
    )
    return ctx, market, portfolio


def test_mean_reversion_enters_on_lower_band_break():
    cfg = StrategyConfig(
        name="majors_mean_rev",
        type="mean_reversion",
        enabled=True,
        params={
            "pairs": ["BTC/USD"],
            "timeframe": "1h",
            "lookback_bars": 5,
            "band_width_bps": 150.0,
            "max_positions": 2,
        },
        userref=1004,
    )
    strat = MeanReversionStrategy(cfg)

    ctx, market, portfolio = _build_context()
    portfolio.get_positions.return_value = []

    bars = [_make_bar(ts, 100.0) for ts in range(4)]
    bars.append(_make_bar(4, 97.0))
    market.get_ohlc.return_value = bars

    intents = strat.generate_intents(ctx)

    assert len(intents) == 1
    intent = intents[0]
    assert intent.side == "long"
    assert intent.intent_type == "enter"
    assert intent.desired_exposure_usd is None


def test_mean_reversion_exits_on_reversion_to_ma():
    cfg = StrategyConfig(
        name="majors_mean_rev",
        type="mean_reversion",
        enabled=True,
        params={
            "pairs": ["BTC/USD"],
            "timeframe": "1h",
            "lookback_bars": 5,
            "band_width_bps": 150.0,
            "max_positions": 2,
        },
        userref=1004,
    )
    strat = MeanReversionStrategy(cfg)

    ctx, market, portfolio = _build_context()
    position = SpotPosition(
        pair="BTC/USD",
        base_asset="BTC",
        quote_asset="USD",
        base_size=0.5,
        avg_entry_price=100.0,
        realized_pnl_base=0.0,
        fees_paid_base=0.0,
    )
    portfolio.get_positions.return_value = [position]

    bars = [_make_bar(ts, 100.0) for ts in range(5)]
    market.get_ohlc.return_value = bars

    intents = strat.generate_intents(ctx)

    assert len(intents) == 1
    intent = intents[0]
    assert intent.side == "flat"
    assert intent.intent_type == "exit"
    assert intent.desired_exposure_usd == 0.0
