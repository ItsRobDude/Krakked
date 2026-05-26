from datetime import datetime, timezone
from unittest.mock import MagicMock

from krakked.config import OHLCBar, StrategyConfig
from krakked.market_data.api import MarketDataAPI
from krakked.portfolio.models import SpotPosition
from krakked.strategy.base import StrategyContext
from krakked.strategy.strategies.vol_breakout import VolBreakoutStrategy
from tests.runtime_mocks import make_portfolio_service_mock


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
    portfolio = make_portfolio_service_mock()
    ctx = StrategyContext(
        now=datetime.now(timezone.utc),
        universe=["XBTUSD"],
        market_data=market,
        portfolio=portfolio,
        timeframe="1h",
    )
    return ctx, market, portfolio


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

    ctx, market, _portfolio = _build_context()

    compressed_skip_bars = [
        _make_bar(ts, 100 + ts * 0.01, 0.05, 0.05) for ts in range(12)
    ]
    market.get_ohlc.return_value = compressed_skip_bars

    intents = strat.generate_intents(ctx)

    assert intents == []

    compressed_breakout_bars = [
        _make_bar(ts, 100.001, 0.0005, 0.0005) for ts in range(11)
    ]
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

    ctx, market, _portfolio = _build_context()
    market.get_ohlc.return_value = [
        _make_bar(ts, 100.0, 0.01, 0.01) for ts in range(5)
    ]

    intents = strat.generate_intents(ctx)

    assert intents == []


def test_vol_breakout_does_not_exit_without_owned_position():
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

    ctx, market, portfolio = _build_context()
    portfolio.get_positions.return_value = [
        SpotPosition(
            pair="XBTUSD",
            base_asset="XBT",
            quote_asset="USD",
            base_size=1.0,
            avg_entry_price=100.0,
            realized_pnl_base=0.0,
            fees_paid_base=0.0,
            strategy_tag="other_strategy",
        )
    ]
    compressed_no_breakout_bars = [
        _make_bar(ts, 100.001, 0.0005, 0.0005) for ts in range(12)
    ]
    market.get_ohlc.return_value = compressed_no_breakout_bars

    assert strat.generate_intents(ctx) == []


def test_vol_breakout_exits_owned_position_when_breakout_fails():
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

    ctx, market, portfolio = _build_context()
    portfolio.get_positions.return_value = [
        SpotPosition(
            pair="XBTUSD",
            base_asset="XBT",
            quote_asset="USD",
            base_size=1.0,
            avg_entry_price=100.0,
            realized_pnl_base=0.0,
            fees_paid_base=0.0,
            strategy_tag="vol_breakout",
        )
    ]
    compressed_no_breakout_bars = [
        _make_bar(ts, 100.001, 0.0005, 0.0005) for ts in range(12)
    ]
    market.get_ohlc.return_value = compressed_no_breakout_bars

    intents = strat.generate_intents(ctx)

    assert len(intents) == 1
    assert intents[0].side == "flat"
    assert intents[0].intent_type == "exit"


def test_vol_breakout_matches_display_pair_to_canonical_owned_position_for_exit():
    cfg = StrategyConfig(
        name="vol_breakout",
        type="vol_breakout",
        enabled=True,
        params={
            "pairs": ["BTC/USD"],
            "timeframes": ["1h"],
            "lookback_bars": 10,
            "min_compression_bps": 5.0,
            "breakout_multiple": 1.5,
        },
        userref=999,
    )
    strat = VolBreakoutStrategy(cfg)

    ctx, market, portfolio = _build_context()
    market.normalize_pair.side_effect = lambda pair: {
        "BTC/USD": "XBTUSD",
        "XBTUSD": "XBTUSD",
    }.get(pair, str(pair).replace("/", "").upper())
    portfolio.get_positions.return_value = [
        SpotPosition(
            pair="XBTUSD",
            base_asset="XBT",
            quote_asset="USD",
            base_size=1.0,
            avg_entry_price=100.0,
            realized_pnl_base=0.0,
            fees_paid_base=0.0,
            strategy_tag="vol_breakout",
        )
    ]
    market.get_ohlc.return_value = [
        _make_bar(ts, 100.001, 0.0005, 0.0005) for ts in range(12)
    ]

    intents = strat.generate_intents(ctx)

    assert len(intents) == 1
    assert intents[0].pair == "BTC/USD"
    assert intents[0].side == "flat"
    assert intents[0].intent_type == "exit"


def test_vol_breakout_matches_display_pair_to_canonical_owned_position_for_increase():
    cfg = StrategyConfig(
        name="vol_breakout",
        type="vol_breakout",
        enabled=True,
        params={
            "pairs": ["BTC/USD"],
            "timeframes": ["1h"],
            "lookback_bars": 10,
            "min_compression_bps": 5.0,
            "breakout_multiple": 1.5,
        },
        userref=999,
    )
    strat = VolBreakoutStrategy(cfg)

    ctx, market, portfolio = _build_context()
    market.normalize_pair.side_effect = lambda pair: {
        "BTC/USD": "XBTUSD",
        "XBTUSD": "XBTUSD",
    }.get(pair, str(pair).replace("/", "").upper())
    portfolio.get_positions.return_value = [
        SpotPosition(
            pair="XBTUSD",
            base_asset="XBT",
            quote_asset="USD",
            base_size=1.0,
            avg_entry_price=100.0,
            realized_pnl_base=0.0,
            fees_paid_base=0.0,
            strategy_tag="vol_breakout",
        )
    ]
    breakout_bars = [_make_bar(ts, 100.001, 0.0005, 0.0005) for ts in range(11)]
    breakout_bars.append(_make_bar(11, 100.0035, 0.0002, 0.0006))
    market.get_ohlc.return_value = breakout_bars

    intents = strat.generate_intents(ctx)

    assert len(intents) == 1
    assert intents[0].pair == "BTC/USD"
    assert intents[0].side == "long"
    assert intents[0].intent_type == "increase"
