from __future__ import annotations

from krakked.config import OHLCBar
from krakked.market_data.ohlc_refresh import refresh_ohlc_tails


class FakeMarketData:
    def __init__(self) -> None:
        self.bars: dict[tuple[str, str], list[OHLCBar]] = {}
        self.backfill_calls: list[tuple[str, str, int | None]] = []
        self.failures: set[tuple[str, str]] = set()

    def get_universe(self) -> list[str]:
        return ["BTC/USD"]

    def get_ohlc(self, pair: str, timeframe: str, lookback: int) -> list[OHLCBar]:
        bars = self.bars.get((pair, timeframe), [])
        return bars[-lookback:]

    def backfill_ohlc(self, pair: str, timeframe: str, since: int | None = None) -> int:
        self.backfill_calls.append((pair, timeframe, since))
        if (pair, timeframe) in self.failures:
            raise RuntimeError("kraken unavailable")
        key = (pair, timeframe)
        if key not in self.bars:
            self.bars[key] = [OHLCBar(1000, 1, 1, 1, 1, 1)]
            return 1
        self.bars[key].append(OHLCBar(self.bars[key][-1].timestamp + 60, 2, 2, 2, 2, 2))
        return 1


def test_tail_refresh_uses_latest_stored_timestamp_as_since() -> None:
    market_data = FakeMarketData()
    market_data.bars[("BTC/USD", "1h")] = [OHLCBar(1000, 1, 1, 1, 1, 1)]

    result = refresh_ohlc_tails(market_data, pairs=["BTC/USD"], timeframes=["1h"])

    assert result.success is True
    assert market_data.backfill_calls == [("BTC/USD", "1h", 1000)]
    assert result.series[0].prior_latest_timestamp == 1000
    assert result.series[0].new_latest_timestamp == 1060
    assert result.series[0].status == "refreshed"


def test_tail_refresh_seeds_empty_local_series_without_since() -> None:
    market_data = FakeMarketData()

    result = refresh_ohlc_tails(market_data, pairs=["BTC/USD"], timeframes=["4h"])

    assert result.success is True
    assert market_data.backfill_calls == [("BTC/USD", "4h", None)]
    assert result.series[0].prior_latest_timestamp is None
    assert result.series[0].new_latest_timestamp == 1000


def test_tail_refresh_supports_explicit_since_override() -> None:
    market_data = FakeMarketData()
    market_data.bars[("BTC/USD", "1h")] = [OHLCBar(1000, 1, 1, 1, 1, 1)]

    result = refresh_ohlc_tails(
        market_data, pairs=["BTC/USD"], timeframes=["1h"], since=900
    )

    assert result.success is True
    assert market_data.backfill_calls == [("BTC/USD", "1h", 900)]
    assert result.series[0].since_timestamp == 900


def test_tail_refresh_reports_unsupported_timeframe_without_fetching() -> None:
    market_data = FakeMarketData()

    result = refresh_ohlc_tails(market_data, pairs=["BTC/USD"], timeframes=["2h"])

    assert result.success is False
    assert result.failed_count == 1
    assert result.series[0].status == "failed"
    assert "Unsupported timeframe" in (result.series[0].error or "")
    assert market_data.backfill_calls == []


def test_tail_refresh_surfaces_per_series_failure() -> None:
    market_data = FakeMarketData()
    market_data.failures.add(("BTC/USD", "1h"))

    result = refresh_ohlc_tails(market_data, pairs=["BTC/USD"], timeframes=["1h"])

    assert result.success is False
    assert result.failed_count == 1
    assert result.series[0].status == "failed"
    assert result.series[0].error == "kraken unavailable"
