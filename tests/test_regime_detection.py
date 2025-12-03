from unittest.mock import MagicMock

from kraken_bot.config import OHLCBar
from kraken_bot.market_data.api import MarketDataAPI
from kraken_bot.strategy.regime import MarketRegime, infer_regime


def _bars_from_closes(closes: list[float]):
    return [
        OHLCBar(
            timestamp=i,
            open=price,
            high=price * 1.01,
            low=price * 0.99,
            close=price,
            volume=1.0,
        )
        for i, price in enumerate(closes)
    ]


def test_infer_regime_classifies_pairs():
    trend_closes = [100 + i * 0.5 + (i % 3) * 0.1 for i in range(40)]
    mean_revert_closes = [100 + (-1) ** i * 0.5 for i in range(40)]
    choppy_closes = [100 + ((-1) ** i) * 0.001 for i in range(40)]

    market_data = MagicMock(spec=MarketDataAPI)
    market_data.get_ohlc.side_effect = lambda pair, *_: {
        "TREND": _bars_from_closes(trend_closes),
        "MEAN": _bars_from_closes(mean_revert_closes),
        "CHOP": _bars_from_closes(choppy_closes),
    }[pair]

    snapshot = infer_regime(market_data, ["TREND", "MEAN", "CHOP"])

    assert snapshot.per_pair["TREND"] == MarketRegime.TRENDING
    assert snapshot.per_pair["MEAN"] == MarketRegime.MEAN_REVERTING
    assert snapshot.per_pair["CHOP"] == MarketRegime.CHOPPY
