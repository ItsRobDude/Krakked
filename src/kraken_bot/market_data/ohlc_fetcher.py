# src/kraken_bot/market_data/ohlc_fetcher.py

import time
import logging
from typing import List, Dict, Any
from kraken_bot.config import OHLCBar, PairMetadata
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
    bars = []
    # The key for the pair data in the response can be the raw_name or altname
    pair_key = next(iter(response))

    # Exclude the last item, which is the incomplete running candle
    for row in response[pair_key][:-1]:
        bars.append(OHLCBar(
            timestamp=int(row[0]),
            open=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
            volume=float(row[6]), # vwap is row[5], volume is row[6]
        ))
    return bars

def backfill_ohlc(
    pair_metadata: PairMetadata,
    timeframe: str,
    since: int = None,
    client: KrakenRESTClient = None,
    store: OHLCStore = None,
) -> int:
    """
    Fetches historical OHLC data for a given pair and timeframe and stores it.

    This function handles pagination by repeatedly calling the API until all data since
    the 'since' timestamp has been fetched.

    Returns the number of bars successfully backfilled.
    """
    if timeframe not in TIMEFRAME_MAP:
        raise ValueError(f"Unsupported timeframe: {timeframe}. Supported: {list(TIMEFRAME_MAP.keys())}")

    if client is None:
        client = KrakenRESTClient()

    logger.info(f"Backfilling OHLC for {pair_metadata.canonical} ({timeframe}), since timestamp {since}")

    total_bars_fetched = 0

    while True:
        params = {
            "pair": pair_metadata.rest_symbol,
            "interval": TIMEFRAME_MAP[timeframe],
        }
        if since:
            params["since"] = since

        try:
            response = client.get_public("OHLC", params)
        except Exception as e:
            logger.error(f"Failed to fetch OHLC data for {pair_metadata.canonical}: {e}")
            break # Exit on error

        if not response or next(iter(response)) not in response:
            logger.info(f"No more OHLC data returned for {pair_metadata.canonical}. Backfill complete.")
            break

        bars = _parse_ohlc_response(response, pair_metadata.canonical)

        if not bars:
            logger.info(f"No new closed candles for {pair_metadata.canonical}. Backfill complete.")
            break

        if store:
            store.append_bars(pair_metadata.canonical, timeframe, bars)

        total_bars_fetched += len(bars)

        # Kraken's pagination uses the 'last' timestamp from the response
        last_val = response.get("last")
        last_ts = int(last_val) if last_val is not None else 0

        # If 'since' was None (first page), we must continue.
        # If 'since' is not None, we stop if the timestamp isn't advancing.
        if since is not None and last_ts <= since:
            break

        since = last_ts

    logger.info(f"Completed backfill for {pair_metadata.canonical} ({timeframe}). Fetched {total_bars_fetched} new bars.")
    return total_bars_fetched
