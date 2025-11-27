# src/kraken_bot/market_data/universe.py

from typing import Any, Dict, List
import logging
from kraken_bot.config import RegionProfile, UniverseConfig, PairMetadata
from kraken_bot.connection.rest_client import KrakenRESTClient

logger = logging.getLogger(__name__)

def _is_usd_spot_pair(pair_data: Dict[str, Any], region_profile: RegionProfile) -> bool:
    """
    Checks if a given asset pair from Kraken's API response is a valid USD spot pair
    that meets the criteria defined in the design contract.
    """
    # 1. Quote asset must be USD
    if pair_data.get("quote") not in ["ZUSD", "USD"]:
        return False

    # 2. Status must be "online"
    if pair_data.get("status") != "online":
        return False

    # 3. Asset class must be spot currency
    # Kraken uses 'currency' for spot assets in the 'aclass_base' field.
    if pair_data.get("aclass_base") != "currency":
        return False

    # 4. No leverage is allowed
    if pair_data.get("leverage_buy") or pair_data.get("leverage_sell"):
        return False

    # 5. Apply region profile constraint (redundant with above, but good for clarity)
    if not region_profile.capabilities.supports_margin and (pair_data.get("leverage_buy") or pair_data.get("leverage_sell")):
        logger.warning(f"Pair {pair_data.get('altname')} has leverage but profile does not support margin. Excluding.")
        return False

    return True

def _create_pair_metadata(raw_name: str, pair_data: Dict[str, Any]) -> PairMetadata:
    """
    Constructs a PairMetadata object from the raw API response data.
    """
    altname = pair_data.get("altname")
    base, quote = altname.split("USD") if "USD" in altname else (None, None)

    return PairMetadata(
        canonical=altname,
        base=base,
        quote="USD", # We filter for USD pairs
        rest_symbol=altname,
        ws_symbol=pair_data.get("wsname"),
        raw_name=raw_name,
        price_decimals=pair_data.get("pair_decimals"),
        volume_decimals=pair_data.get("lot_decimals"),
        lot_size=float(pair_data.get("lot_multiplier", 1.0)),
        status=pair_data.get("status"),
    )

def _filter_by_volume(
    client: KrakenRESTClient,
    pairs: List[PairMetadata],
    min_volume: float
) -> List[PairMetadata]:
    """
    Filters a list of pairs based on their 24-hour trading volume in USD.
    """
    if not pairs:
        return []

    logger.info(f"Filtering {len(pairs)} pairs by minimum 24h volume: ${min_volume:,.2f}")

    pair_names = [p.rest_symbol for p in pairs]
    try:
        # Kraken's Ticker endpoint accepts multiple pairs, comma-separated
        ticker_response = client.get_public("Ticker", params={"pair": ",".join(pair_names)})
    except Exception as e:
        logger.error(f"Failed to fetch ticker data for volume filtering: {e}")
        return pairs # Return unfiltered list on error

    retained_pairs = []
    for pair in pairs:
        ticker_info = ticker_response.get(pair.raw_name) # Ticker response uses the raw name
        if not ticker_info:
            logger.warning(f"Could not find ticker info for {pair.canonical}. Retaining.")
            retained_pairs.append(pair)
            continue

        # Volume is in the 'v' field, index 1 is today's volume
        volume_24h_base = float(ticker_info["v"][1])
        # Last trade price is in 'c' field, index 0
        last_price = float(ticker_info["c"][0])
        volume_24h_usd = volume_24h_base * last_price

        if volume_24h_usd >= min_volume:
            retained_pairs.append(pair)
        else:
            logger.debug(f"Excluding {pair.canonical} due to low volume: ${volume_24h_usd:,.2f}")

    logger.info(f"{len(retained_pairs)} pairs remain after volume filtering.")
    return retained_pairs

def build_universe(
    client: KrakenRESTClient,
    region_profile: RegionProfile,
    universe_config: UniverseConfig,
) -> List[PairMetadata]:
    """
    Fetches all tradable asset pairs from Kraken, filters them to create the pair universe,
    and applies configuration overrides.
    """
    logger.info("Building pair universe...")

    # 1. Fetch all asset pairs from the API
    try:
        asset_pairs_response = client.get_public("AssetPairs")
    except Exception as e:
        logger.error(f"Failed to fetch asset pairs from Kraken: {e}")
        return []

    # 2. Apply filtering logic
    candidate_pairs = {}
    for raw_name, pair_data in asset_pairs_response.items():
        if _is_usd_spot_pair(pair_data, region_profile):
            metadata = _create_pair_metadata(raw_name, pair_data)
            candidate_pairs[metadata.canonical] = metadata

    logger.info(f"Found {len(candidate_pairs)} candidate USD spot pairs after initial filtering.")

    # 3. Apply overrides from config
    universe_after_overrides = set(candidate_pairs.keys())

    if universe_config.include_pairs:
        for pair in universe_config.include_pairs:
            if pair in candidate_pairs:
                universe_after_overrides.add(pair)
            else:
                logger.warning(f"Pair '{pair}' in 'include_pairs' not found in valid asset pairs from Kraken.")

    if universe_config.exclude_pairs:
        universe_after_overrides -= set(universe_config.exclude_pairs)

    logger.info(f"Universe size after include/exclude overrides: {len(universe_after_overrides)}")

    # Create metadata objects for the pairs that passed the override stage
    pairs_to_filter = [candidate_pairs[p] for p in universe_after_overrides]

    # 4. Implement 24h volume filtering
    if universe_config.min_24h_volume_usd > 0:
        final_pairs = _filter_by_volume(client, pairs_to_filter, universe_config.min_24h_volume_usd)
    else:
        final_pairs = pairs_to_filter

    # 5. Return the final list of metadata objects
    result = sorted(final_pairs, key=lambda p: p.canonical)
    logger.info(f"Final universe contains {len(result)} pairs.")

    return result
