# src/kraken_bot/market_data/ohlc_fetcher.py

import logging
from typing import Any, Dict, Iterator, List, Optional

from kraken_bot.config import OHLCBar, PairMetadata
from kraken_bot.connection.rate_limiter import RateLimiter
from kraken_bot.connection.rest_client import KrakenRESTClient
from kraken_bot.market_data.ohlc_store import OHLCStore

logger = logging.getLogger(__name__)

TIMEFRAME_MAP = {
    "1m": 1,
    "5m": 5,
    "15m": 15,
    "1h": 60,
    "4h": 240,
    "1d": 1440,
}


def _parse_ohlc_response(response: Dict[str, Any], pair: str) -> List[OHLCBar]:
    """
    Parses the raw OHLC data from the Kraken API into a list of OHLCBar objects.
    Note: The last entry in the response is the current, running candle. We exclude it.
    """
    # The key for the pair data in the response can be the raw_name or altname
    pair_key = next((key for key in response.keys() if key != "last"), None)
    if not pair_key:
        return []

    # Exclude the last item, which is the incomplete running candle
    raw_data = response[pair_key][:-1]

    return [
        OHLCBar(
            timestamp=int(row[0]),
            open=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
            volume=float(row[6]),  # vwap is row[5], volume is row[6]
        )
        for row in raw_data
    ]


def _fetch_ohlc_pages(
    client: KrakenRESTClient,
    pair_metadata: PairMetadata,
    timeframe: str,
    since: Optional[int],
) -> Iterator[List[OHLCBar]]:
    """
    Yields batches of OHLC bars handling pagination.
    Stops when no more data is available or an error occurs.
    """
    current_since = since

    while True:
        params = {
            "pair": pair_metadata.rest_symbol,
            "interval": TIMEFRAME_MAP[timeframe],
        }
        if current_since is not None:
            params["since"] = current_since

        try:
            response = client.get_public("OHLC", params)
        except Exception as e:
            logger.error(
                f"Failed to fetch OHLC data for {pair_metadata.canonical}: {e}"
            )
            break

        if not response:
            logger.info(
                f"No more OHLC data returned for {pair_metadata.canonical}. Backfill complete."
            )
            break

        bars = _parse_ohlc_response(response, pair_metadata.canonical)
        if not bars:
            logger.info(
                f"No new closed candles for {pair_metadata.canonical}. Backfill complete."
            )
            break

        yield bars

        last_raw = response.get("last")
        try:
            last_ts = int(last_raw) if last_raw is not None else None
        except (TypeError, ValueError):
            logger.warning(
                f"Received non-numeric 'last' value from OHLC response for {pair_metadata.canonical}: {last_raw}"
            )
            break

        if last_ts is None:
            break

        if current_since is None or last_ts > current_since:
            current_since = last_ts
        else:
            break


def backfill_ohlc(
    pair_metadata: PairMetadata,
    timeframe: str,
    since: Optional[int] = None,
    client: Optional[KrakenRESTClient] = None,
    rate_limiter: Optional[RateLimiter] = None,
    store: Optional[OHLCStore] = None,
) -> int:
    """
    Fetches historical OHLC data for a given pair and timeframe and stores it.

    This function handles pagination by repeatedly calling the API until all data since
    the 'since' timestamp has been fetched.

    Returns the number of bars successfully backfilled.
    """
    if timeframe not in TIMEFRAME_MAP:
        raise ValueError(
            f"Unsupported timeframe: {timeframe}. Supported: {list(TIMEFRAME_MAP.keys())}"
        )

    if client is None:
        client = KrakenRESTClient(rate_limiter=rate_limiter)

    logger.info(
        f"Backfilling OHLC for {pair_metadata.canonical} ({timeframe}), since timestamp {since}"
    )

    total_bars_fetched = 0

    for bars in _fetch_ohlc_pages(client, pair_metadata, timeframe, since):
        if store:
            store.append_bars(pair_metadata.canonical, timeframe, bars)
        total_bars_fetched += len(bars)

    if total_bars_fetched > 0:
        logger.info(
            f"Completed backfill for {pair_metadata.canonical} ({timeframe}). Fetched {total_bars_fetched} new bars."
        )

    return total_bars_fetched
