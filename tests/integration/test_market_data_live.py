# tests/integration/test_market_data_live.py

import os
import time

import pytest

from kraken_bot.config import (
    AppConfig,
    MarketDataConfig,
    RegionCapabilities,
    RegionProfile,
    UniverseConfig,
)
from kraken_bot.market_data.api import MarketDataAPI


@pytest.mark.skipif(
    os.environ.get("KRAKEN_LIVE_TESTS") != "1",
    reason="Skipping live integration tests (KRAKEN_LIVE_TESTS!=1)",
)
def test_live_market_data_flow(tmp_path):
    """
    A live smoke test that hits the real Kraken public API.
    Requirements: Internet access.
    """
    # 1. Setup minimal configuration
    # Use a temporary directory for the OHLC store to avoid polluting the user's home dir
    ohlc_dir = tmp_path / "kraken_ohlc"

    config = AppConfig(
        region=RegionProfile(
            code="US_CA",
            capabilities=RegionCapabilities(False, False, False),
            default_quote="USD",
        ),
        universe=UniverseConfig(
            include_pairs=["XBTUSD"],  # Focus on a major pair
            exclude_pairs=[],
            min_24h_volume_usd=0,  # Don't filter by volume to keep it simple
        ),
        market_data=MarketDataConfig(
            ws={"stale_tolerance_seconds": 60},
            ohlc_store={"root_dir": str(ohlc_dir), "backend": "parquet"},
            backfill_timeframes=[],  # Disable auto-backfill on init
            ws_timeframes=["1m"],
        ),
    )

    api = MarketDataAPI(config)

    try:
        # 2. Initialize (Build Universe)
        print("\n[Live Test] Initializing and building universe...")
        api.initialize(backfill=False)

        universe = api.get_universe()
        assert len(universe) > 0, "Universe should not be empty"

        # Verify XBTUSD is in there
        assert "XBTUSD" in universe, "XBTUSD should be in the universe"
        print(f"[Live Test] Universe size: {len(universe)}. Found XBTUSD.")

        universe_metadata = api.get_universe_metadata()
        xbt_usd = next((p for p in universe_metadata if p.canonical == "XBTUSD"), None)
        assert xbt_usd is not None, "XBTUSD metadata should be available"

        # 3. Backfill OHLC
        # Fetch a small amount of 1h data (e.g., last 24 hours)
        # We align 'since' to the top of the hour to reduce off-by-one errors due to boundary semantics.
        interval = 3600  # 1h
        now = int(time.time())
        aligned_now = now - (now % interval)
        since = aligned_now - (48 * interval)

        print(f"[Live Test] Backfilling XBTUSD 1h data since {since}...")
        count = api.backfill_ohlc("XBTUSD", "1h", since=since)
        print(f"[Live Test] Backfilled {count} bars.")

        assert count > 0, "Should have fetched some bars"

        # 4. Retrieve OHLC
        print("[Live Test] Retrieving bars from store...")
        bars = api.get_ohlc("XBTUSD", "1h", lookback=10)
        assert len(bars) > 0
        assert len(bars) <= 10

        # Verify bars are valid
        last_bar = bars[-1]
        print(f"[Live Test] Last bar: {last_bar}")
        assert last_bar.close > 0
        assert last_bar.timestamp > since

        # 5. Retrieve OHLC since
        bars_since = api.get_ohlc_since("XBTUSD", "1h", since_ts=since)

        # We allow an off-by-one difference because Kraken's `since` semantics
        # (fetching based on ID/time) and our timestamp filter (>= since)
        # may not line up perfectly at the boundary.
        assert (
            abs(len(bars_since) - count) <= 1
        ), f"Retrieved {len(bars_since)} bars, expected approx {count}"

        print("[Live Test] Success!")

    finally:
        api.shutdown()
