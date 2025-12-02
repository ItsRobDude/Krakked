from datetime import datetime, timezone
from unittest.mock import MagicMock

from kraken_bot.config import OHLCBar, StrategyConfig
from kraken_bot.market_data.api import MarketDataAPI
from kraken_bot.portfolio.manager import PortfolioService
from kraken_bot.portfolio.models import EquityView, SpotPosition
from kraken_bot.strategy.base import StrategyContext
from kraken_bot.strategy.strategies.relative_strength import RelativeStrengthStrategy


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
        timeframe="4h",
    )
    return ctx, market, portfolio


def test_relative_strength_prefers_top_return():
    cfg = StrategyConfig(
        name="rs_rotation",
        type="relative_strength",
        enabled=True,
        params={
            "pairs": ["BTC/USD", "ETH/USD"],
            "lookback_bars": 3,
            "timeframe": "4h",
            "rebalance_interval_hours": 1,
            "top_n": 1,
            "total_allocation_pct": 20.0,
        },
        userref=1005,
    )
    strat = RelativeStrengthStrategy(cfg)

    ctx, market, portfolio = _build_context()
    equity = EquityView(
        equity_base=1000.0,
        cash_base=800.0,
        realized_pnl_base_total=0.0,
        unrealized_pnl_base_total=0.0,
        drift_flag=False,
    )
    portfolio.get_equity.return_value = equity

    portfolio.get_positions.return_value = [
        SpotPosition(
            pair="BTC/USD",
            base_asset="BTC",
            quote_asset="USD",
            base_size=1.0,
            avg_entry_price=100.0,
            realized_pnl_base=0.0,
            fees_paid_base=0.0,
            strategy_tag="rs_rotation",
        ),
        SpotPosition(
            pair="ETH/USD",
            base_asset="ETH",
            quote_asset="USD",
            base_size=1.0,
            avg_entry_price=100.0,
            realized_pnl_base=0.0,
            fees_paid_base=0.0,
            strategy_tag="rs_rotation",
        ),
    ]

    btc_bars = [_make_bar(ts, 100 + (ts * 5)) for ts in range(3)]
    eth_bars = [_make_bar(ts, 100 - ts) for ts in range(3)]

    def _get_ohlc(pair: str, timeframe: str, lookback: int):
        return btc_bars if pair == "BTC/USD" else eth_bars

    market.get_ohlc.side_effect = _get_ohlc
    market.get_latest_price.side_effect = lambda pair: (
        105.0 if pair == "BTC/USD" else 99.0
    )

    intents = strat.generate_intents(ctx)

    assert len(intents) == 2

    btc_intent = next(intent for intent in intents if intent.pair == "BTC/USD")
    eth_intent = next(intent for intent in intents if intent.pair == "ETH/USD")

    assert btc_intent.desired_exposure_usd > eth_intent.desired_exposure_usd
    assert btc_intent.desired_exposure_usd == 200.0
    assert eth_intent.desired_exposure_usd == 0.0
    assert btc_intent.intent_type in ["enter", "increase"]
    assert eth_intent.intent_type == "exit"
