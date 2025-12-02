# src/kraken_bot/market_data/universe.py

import logging
from typing import Any, Dict, List

from kraken_bot.config import PairMetadata, RegionProfile, UniverseConfig
from kraken_bot.connection.rest_client import KrakenRESTClient

logger = logging.getLogger(__name__)


def _is_valid_usd_spot_pair(
    raw_name: str, pair_data: Dict[str, Any], region_profile: RegionProfile
) -> bool:
    """
    Checks if a given asset pair from Kraken's API response is a valid USD spot pair
    based on hard validity rules. Soft concerns (e.g., leverage or margin markers)
    are evaluated later in the pipeline.
    """
    # 1. Quote asset must match the region default (Kraken may prefix fiat quotes with "Z")
    allowed_quotes = {region_profile.default_quote, f"Z{region_profile.default_quote}"}
    if pair_data.get("quote") not in allowed_quotes:
        return False

    # 2. Status must be "online"
    if pair_data.get("status") != "online":
        return False

    # 3. Asset class must be spot currency
    # Kraken uses 'currency' for spot assets in the 'aclass_base' field.
    if pair_data.get("aclass_base") != "currency":
        return False

    return True


def _create_pair_metadata(raw_name: str, pair_data: Dict[str, Any]) -> PairMetadata:
    """
    Constructs a PairMetadata object from the raw API response data.
    """
    altname = pair_data.get("altname") or raw_name
    base_raw = str(pair_data.get("base") or "")
    quote_raw = str(pair_data.get("quote") or "")

    # Normalize quote to "USD" if it's ZUSD or USD (which we've already filtered for)
    quote_normalized = "USD" if quote_raw in ["ZUSD", "USD"] else quote_raw

    min_order_size_raw = pair_data.get("ordermin", 0.0)
    try:
        min_order_size = float(min_order_size_raw)
    except (TypeError, ValueError):
        min_order_size = 0.0

    ws_symbol = pair_data.get("wsname") or altname
    price_decimals = int(pair_data.get("pair_decimals") or 0)
    volume_decimals = int(pair_data.get("lot_decimals") or 0)
    status = str(pair_data.get("status") or "unknown")

    return PairMetadata(
        canonical=altname,
        base=base_raw,
        quote=quote_normalized,
        rest_symbol=altname,
        ws_symbol=ws_symbol,
        raw_name=raw_name,
        price_decimals=price_decimals,
        volume_decimals=volume_decimals,
        lot_size=float(pair_data.get("lot_multiplier", 1.0)),
        min_order_size=min_order_size,
        status=status,
    )


def _filter_by_volume(
    client: KrakenRESTClient, pairs: List[PairMetadata], min_volume: float
) -> List[PairMetadata]:
    """
    Filters a list of pairs based on their 24-hour trading volume in USD.
    """
    if not pairs:
        return []

    logger.info(
        f"Filtering {len(pairs)} pairs by minimum 24h volume: ${min_volume:,.2f}"
    )

    pair_names = [p.rest_symbol for p in pairs]
    try:
        # Kraken's Ticker endpoint accepts multiple pairs, comma-separated
        ticker_response = client.get_public(
            "Ticker", params={"pair": ",".join(pair_names)}
        )
    except Exception as e:
        logger.error(f"Failed to fetch ticker data for volume filtering: {e}")
        return pairs  # Return unfiltered list on error

    retained_pairs = []
    for pair in pairs:
        # Kraken sometimes returns the raw name, sometimes the altname/rest_symbol.
        # Check both to be safe.
        ticker_info = ticker_response.get(pair.raw_name) or ticker_response.get(
            pair.rest_symbol
        )

        if not ticker_info:
            logger.warning(
                f"Could not find ticker info for {pair.canonical}. Retaining."
            )
            retained_pairs.append(pair)
            continue

        # Volume is in the 'v' field, index 1 is today's volume
        volume_24h_base = float(ticker_info["v"][1])
        # Last trade price is in 'c' field, index 0
        last_price = float(ticker_info["c"][0])
        volume_24h_usd = volume_24h_base * last_price

        if volume_24h_usd >= min_volume:
            pair.liquidity_24h_usd = volume_24h_usd
            retained_pairs.append(pair)
        else:
            logger.debug(
                f"Excluding {pair.canonical} due to low volume: ${volume_24h_usd:,.2f}"
            )

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
    raw_pairs_by_altname = {}
    for raw_name, pair_data in asset_pairs_response.items():
        altname = pair_data.get("altname")
        if altname:
            raw_pairs_by_altname[altname] = (raw_name, pair_data)

        if _is_valid_usd_spot_pair(raw_name, pair_data, region_profile):
            metadata = _create_pair_metadata(raw_name, pair_data)
            candidate_pairs[metadata.canonical] = metadata

    logger.info(
        f"Found {len(candidate_pairs)} candidate USD spot pairs after initial filtering."
    )

    # 3. Apply include/exclude overrides with hard validity handling
    forced_includes = set()
    universe_after_overrides = set(candidate_pairs.keys())

    if universe_config.include_pairs:
        for pair in universe_config.include_pairs:
            raw_pair_entry = raw_pairs_by_altname.get(pair)
            if raw_pair_entry:
                raw_name_for_alt, pair_data_for_alt = raw_pair_entry
                if _is_valid_usd_spot_pair(
                    raw_name_for_alt, pair_data_for_alt, region_profile
                ):
                    forced_includes.add(pair)
                    universe_after_overrides.add(pair)
                else:
                    logger.warning(
                        f"Pair '{pair}' in 'include_pairs' failed hard validity checks and will be ignored."
                    )
            else:
                logger.warning(
                    f"Pair '{pair}' in 'include_pairs' failed hard validity checks and will be ignored."
                )

    if universe_config.exclude_pairs:
        excluded = set(universe_config.exclude_pairs)
        forced_includes -= excluded
        universe_after_overrides -= excluded

    logger.info(
        f"Universe size after include/exclude overrides: {len(universe_after_overrides)}"
    )

    # Create metadata objects for the pairs that will undergo soft filtering
    pairs_to_filter = [
        candidate_pairs[p] for p in universe_after_overrides if p not in forced_includes
    ]

    # 4. Implement 24h volume filtering (soft filter)
    if universe_config.min_24h_volume_usd > 0:
        filtered_pairs = _filter_by_volume(
            client, pairs_to_filter, universe_config.min_24h_volume_usd
        )
    else:
        filtered_pairs = pairs_to_filter

    # Merge forced includes back into the final set
    final_pairs = filtered_pairs + [candidate_pairs[p] for p in forced_includes]

    # 5. Return the final list of metadata objects
    result = sorted(final_pairs, key=lambda p: p.canonical)
    logger.info(f"Final universe contains {len(result)} pairs.")

    return result
