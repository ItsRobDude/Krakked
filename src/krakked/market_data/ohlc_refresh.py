"""Operator-facing OHLC tail refresh helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Optional, Protocol, Sequence

from krakked.config import OHLCBar
from krakked.market_data.ohlc_fetcher import TIMEFRAME_MAP


class OHLCTailRefreshMarketData(Protocol):
    def get_universe(self) -> list[str]: ...

    def get_ohlc(self, pair: str, timeframe: str, lookback: int) -> list[OHLCBar]: ...

    def backfill_ohlc(
        self, pair: str, timeframe: str, since: Optional[int] = None
    ) -> int: ...


@dataclass(frozen=True)
class OHLCTailRefreshSeriesResult:
    pair: str
    timeframe: str
    prior_latest_timestamp: Optional[int]
    since_timestamp: Optional[int]
    new_latest_timestamp: Optional[int]
    fetched_bars: int
    status: str
    error: Optional[str] = None

    def to_dict(self) -> dict[str, object]:
        return {
            "pair": self.pair,
            "timeframe": self.timeframe,
            "prior_latest_timestamp": self.prior_latest_timestamp,
            "since_timestamp": self.since_timestamp,
            "new_latest_timestamp": self.new_latest_timestamp,
            "fetched_bars": self.fetched_bars,
            "status": self.status,
            "error": self.error,
        }


@dataclass(frozen=True)
class OHLCTailRefreshSummary:
    generated_at: datetime
    pairs: list[str]
    timeframes: list[str]
    series: list[OHLCTailRefreshSeriesResult]

    @property
    def failed_count(self) -> int:
        return sum(1 for item in self.series if item.status == "failed")

    @property
    def success(self) -> bool:
        return self.failed_count == 0

    @property
    def fetched_bars(self) -> int:
        return sum(item.fetched_bars for item in self.series)

    def to_dict(self) -> dict[str, object]:
        return {
            "generated_at": self.generated_at.isoformat(),
            "success": self.success,
            "failed_count": self.failed_count,
            "fetched_bars": self.fetched_bars,
            "pairs": list(self.pairs),
            "timeframes": list(self.timeframes),
            "series": [item.to_dict() for item in self.series],
        }


def _latest_timestamp(
    market_data: OHLCTailRefreshMarketData, pair: str, timeframe: str
) -> Optional[int]:
    bars = market_data.get_ohlc(pair, timeframe, 1)
    if not bars:
        return None
    return int(bars[-1].timestamp)


def refresh_ohlc_tails(
    market_data: OHLCTailRefreshMarketData,
    *,
    pairs: Optional[Sequence[str]] = None,
    timeframes: Optional[Sequence[str]] = None,
    since: Optional[int] = None,
) -> OHLCTailRefreshSummary:
    """Refresh configured OHLC tails and return per-series operator results."""

    requested_pairs = [str(pair) for pair in (pairs or market_data.get_universe())]
    requested_timeframes = [str(timeframe) for timeframe in (timeframes or [])]

    series_results: list[OHLCTailRefreshSeriesResult] = []
    for pair in requested_pairs:
        for timeframe in requested_timeframes:
            if timeframe not in TIMEFRAME_MAP:
                series_results.append(
                    OHLCTailRefreshSeriesResult(
                        pair=pair,
                        timeframe=timeframe,
                        prior_latest_timestamp=None,
                        since_timestamp=since,
                        new_latest_timestamp=None,
                        fetched_bars=0,
                        status="failed",
                        error=(
                            f"Unsupported timeframe: {timeframe}. "
                            f"Supported: {sorted(TIMEFRAME_MAP)}"
                        ),
                    )
                )
                continue

            prior_latest: Optional[int] = None
            effective_since: Optional[int] = since
            try:
                prior_latest = _latest_timestamp(market_data, pair, timeframe)
                effective_since = since if since is not None else prior_latest
                fetched = market_data.backfill_ohlc(pair, timeframe, effective_since)
                new_latest = _latest_timestamp(market_data, pair, timeframe)
            except Exception as exc:  # noqa: BLE001 - surfaced per series
                series_results.append(
                    OHLCTailRefreshSeriesResult(
                        pair=pair,
                        timeframe=timeframe,
                        prior_latest_timestamp=prior_latest,
                        since_timestamp=effective_since,
                        new_latest_timestamp=None,
                        fetched_bars=0,
                        status="failed",
                        error=str(exc),
                    )
                )
                continue

            if new_latest != prior_latest:
                status = "refreshed"
            elif prior_latest is None and new_latest is None:
                status = "empty"
            else:
                status = "unchanged"

            series_results.append(
                OHLCTailRefreshSeriesResult(
                    pair=pair,
                    timeframe=timeframe,
                    prior_latest_timestamp=prior_latest,
                    since_timestamp=effective_since,
                    new_latest_timestamp=new_latest,
                    fetched_bars=fetched,
                    status=status,
                )
            )

    return OHLCTailRefreshSummary(
        generated_at=datetime.now(UTC),
        pairs=requested_pairs,
        timeframes=requested_timeframes,
        series=series_results,
    )
