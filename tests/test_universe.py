# tests/test_universe.py

from unittest.mock import MagicMock

import pytest

from kraken_bot.config import RegionCapabilities, RegionProfile, UniverseConfig
from kraken_bot.market_data.universe import build_universe


@pytest.fixture
def mock_region_profile() -> RegionProfile:
    """Provides a standard US/CA region profile for testing."""
    return RegionProfile(
        code="US_CA",
        capabilities=RegionCapabilities(
            supports_margin=False, supports_futures=False, supports_staking=False
        ),
        default_quote="USD",
    )


@pytest.fixture
def mock_kraken_asset_pairs_response() -> dict:
    """Provides a sample response from the Kraken GetTradableAssetPairs endpoint."""
    return {
        "XXBTZUSD": {
            "altname": "XBTUSD",
            "wsname": "XBT/USD",
            "aclass_base": "currency",
            "base": "XXBT",
            "aclass_quote": "currency",
            "quote": "ZUSD",
            "lot": "unit",
            "pair_decimals": 1,
            "lot_decimals": 8,
            "lot_multiplier": 1,
            "leverage_buy": [],
            "leverage_sell": [],
            "fees": [],
            "fees_maker": [],
            "fee_volume_currency": "ZUSD",
            "margin_call": 80,
            "margin_stop": 40,
            "ordermin": "0.0001",
            "status": "online",
        },
        "XETHZUSD": {
            "altname": "ETHUSD",
            "wsname": "ETH/USD",
            "aclass_base": "currency",
            "base": "XETH",
            "aclass_quote": "currency",
            "quote": "ZUSD",
            "lot": "unit",
            "pair_decimals": 2,
            "lot_decimals": 8,
            "lot_multiplier": 1,
            "leverage_buy": [],
            "leverage_sell": [],
            "fees": [],
            "fees_maker": [],
            "fee_volume_currency": "ZUSD",
            "margin_call": 80,
            "margin_stop": 40,
            "ordermin": "0.002",
            "status": "online",
        },
        "DOGEUSD": {  # Excluded by default due to quote=USD not ZUSD, but good for include test
            "altname": "DOGEUSD",
            "wsname": "DOGE/USD",
            "aclass_base": "currency",
            "base": "XDG",
            "aclass_quote": "currency",
            "quote": "USD",
            "lot": "unit",
            "pair_decimals": 7,
            "lot_decimals": 8,
            "lot_multiplier": 1,
            "leverage_buy": [],
            "leverage_sell": [],
            "fees": [],
            "fees_maker": [],
            "fee_volume_currency": "ZUSD",
            "margin_call": 80,
            "margin_stop": 40,
            "ordermin": "30",
            "status": "online",
        },
        "XXBTZEUR": {  # Not a USD pair
            "altname": "XBTEUR",
            "wsname": "XBT/EUR",
            "aclass_base": "currency",
            "quote": "ZEUR",
            "status": "online",
            "leverage_buy": [],
            "leverage_sell": [],
        },
        "XBTUSDT": {  # Not a USD pair
            "altname": "XBTUSDT",
            "wsname": "XBT/USDT",
            "aclass_base": "currency",
            "quote": "USDT",
            "status": "online",
            "leverage_buy": [],
            "leverage_sell": [],
        },
        "ETHUSD.M": {  # Simulated Margin pair (with marker)
            "altname": "ETHUSDM",
            "wsname": "ETH/USD.M",
            "aclass_base": "currency",
            "quote": "ZUSD",
            "status": "online",
            "leverage_buy": [],
            "leverage_sell": [],
        },
        "XMRUSD": {  # Leverage enabled
            "altname": "XMRUSD",
            "wsname": "XMR/USD",
            "aclass_base": "currency",
            "quote": "ZUSD",
            "status": "online",
            "leverage_buy": [2, 5],
            "leverage_sell": [2, 5],
        },
        "ADAUSD.F": {  # Futures-style marker
            "altname": "ADAUSDF",
            "wsname": "ADA/USD.F",
            "aclass_base": "currency",
            "quote": "ZUSD",
            "status": "online",
            "leverage_buy": [],
            "leverage_sell": [],
        },
        "ADAUSD": {  # Cancel-only pair
            "altname": "ADAUSD",
            "wsname": "ADA/USD",
            "aclass_base": "currency",
            "quote": "ZUSD",
            "status": "cancel_only",
            "leverage_buy": [],
            "leverage_sell": [],
        },
    }


def test_universe_filtering(mock_region_profile, mock_kraken_asset_pairs_response):
    """Tests the core filtering logic for USD spot pairs with region capability constraints."""
    mock_client = MagicMock()
    mock_client.get_public.return_value = mock_kraken_asset_pairs_response

    universe_config = UniverseConfig(
        include_pairs=[], exclude_pairs=[], min_24h_volume_usd=0
    )

    universe = build_universe(mock_client, mock_region_profile, universe_config)

    assert len(universe) == 6
    pair_names = {p.canonical for p in universe}
    assert "XBTUSD" in pair_names
    assert "ETHUSD" in pair_names
    assert "DOGEUSD" in pair_names
    assert "ETHUSDM" in pair_names  # Margin-marked; handled downstream
    assert "ADAUSDF" in pair_names  # Futures-marked; handled downstream
    assert "XMRUSD" in pair_names  # Leverage-capable pair passes initial filter

    pair_map = {p.canonical: p for p in universe}
    assert pair_map["XBTUSD"].min_order_size == pytest.approx(0.0001)
    assert pair_map["ETHUSD"].min_order_size == pytest.approx(0.002)
    assert pair_map["ETHUSDM"].min_order_size == 0.0

    assert "XBTEUR" not in pair_names  # Non-USD
    assert "ADAUSD" not in pair_names  # cancel_only


def test_universe_overrides(
    mock_region_profile, mock_kraken_asset_pairs_response, caplog
):
    """Tests the include_pairs and exclude_pairs configuration overrides."""
    caplog.set_level("WARNING")
    mock_client = MagicMock()
    mock_client.get_public.return_value = mock_kraken_asset_pairs_response

    # Test exclude override
    exclude_config = UniverseConfig(
        include_pairs=[], exclude_pairs=["XBTUSD"], min_24h_volume_usd=0
    )
    universe_excluded = build_universe(mock_client, mock_region_profile, exclude_config)
    assert len(universe_excluded) == 5
    assert "XBTUSD" not in {p.canonical for p in universe_excluded}

    # Test that include cannot bypass hard validity and emits a warning
    mock_kraken_asset_pairs_response["DOGEUSD"]["status"] = "cancel_only"
    mock_client.get_public.return_value = mock_kraken_asset_pairs_response

    include_config = UniverseConfig(
        include_pairs=["DOGEUSD"],
        exclude_pairs=["XBTUSD", "ETHUSD"],
        min_24h_volume_usd=0,
    )
    universe_included = build_universe(mock_client, mock_region_profile, include_config)

    assert any("DOGEUSD" in message for message in caplog.messages)
    assert len(universe_included) == 3
    assert "DOGEUSD" not in {p.canonical for p in universe_included}


def test_universe_volume_filtering(
    mock_region_profile, mock_kraken_asset_pairs_response
):
    """Tests that the volume filter correctly removes low-liquidity pairs."""
    mock_client = MagicMock()
    # Filter response to only include what we expect to pass the initial filter
    filtered_response = {
        k: v
        for k, v in mock_kraken_asset_pairs_response.items()
        if v["quote"] in ["ZUSD", "USD"]
        and v.get("aclass_base") == "currency"
        and v["status"] == "online"
    }

    mock_client.get_public.side_effect = [
        # First call for AssetPairs
        filtered_response,
        # Second call for Ticker
        {
            "XXBTZUSD": {
                "v": ["1000", "2500.5"],
                "c": ["50000.0", "1"],
            },  # vol=2500.5, price=50k -> >125M USD
            "XETHZUSD": {
                "v": ["500", "10.0"],
                "c": ["2000.0", "1"],
            },  # vol=10, price=2k -> 20k USD
            "DOGEUSD": {
                "v": ["100000", "500000.0"],
                "c": ["0.1", "1"],
            },  # vol=500k, price=0.1 -> 50k USD
            "ETHUSDM": {
                "v": ["100", "1.0"],
                "c": ["2000.0", "1"],
            },  # vol=1, price=2k -> 2k USD
            "XMRUSD": {
                "v": ["100", "5.0"],
                "c": ["150.0", "1"],
            },  # vol=5, price=150 -> 750 USD
            "ADAUSDF": {
                "v": ["100000", "20000.0"],
                "c": ["0.3", "1"],
            },  # vol=20k, price=0.3 -> 6k USD
        },
    ]

    # Set a min volume of $100,000 USD
    config = UniverseConfig(
        include_pairs=[], exclude_pairs=[], min_24h_volume_usd=100000.0
    )

    universe = build_universe(mock_client, mock_region_profile, config)

    pair_names = {p.canonical for p in universe}

    assert len(universe) == 1
    assert "XBTUSD" in pair_names
    assert "ETHUSD" not in pair_names  # Excluded due to low volume
    assert "DOGEUSD" not in pair_names  # Excluded due to low volume
    assert "ETHUSDM" not in pair_names  # Excluded due to low volume
    assert "XMRUSD" not in pair_names  # Excluded due to low volume
    assert "ADAUSDF" not in pair_names  # Excluded due to low volume

    # Verify that get_public was called twice
    assert mock_client.get_public.call_count == 2
    mock_client.get_public.assert_any_call("AssetPairs")

    # Check the Ticker call in an order-independent way
    ticker_call_args = mock_client.get_public.call_args_list[1]
    assert ticker_call_args[0][0] == "Ticker"
    called_pairs = set(ticker_call_args[1]["params"]["pair"].split(","))
    expected_pairs = {"XBTUSD", "ETHUSD", "DOGEUSD", "ETHUSDM", "XMRUSD", "ADAUSDF"}
    assert called_pairs == expected_pairs


def test_include_pairs_bypass_volume_filter(
    mock_region_profile, mock_kraken_asset_pairs_response
):
    """Valid include_pairs should skip soft volume filtering."""
    mock_client = MagicMock()
    filtered_response = {
        k: v
        for k, v in mock_kraken_asset_pairs_response.items()
        if v["quote"] in ["ZUSD", "USD"]
        and v.get("aclass_base") == "currency"
        and v["status"] == "online"
    }

    mock_client.get_public.side_effect = [
        filtered_response,
        {
            "XXBTZUSD": {"v": ["1000", "2500.5"], "c": ["50000.0", "1"]},
            "XETHZUSD": {"v": ["500", "10.0"], "c": ["2000.0", "1"]},
            "DOGEUSD": {"v": ["100000", "500000.0"], "c": ["0.1", "1"]},
            "ETHUSDM": {"v": ["100", "1.0"], "c": ["2000.0", "1"]},
            "XMRUSD": {"v": ["100", "5.0"], "c": ["150.0", "1"]},
            "ADAUSDF": {"v": ["100000", "20000.0"], "c": ["0.3", "1"]},
        },
    ]

    config = UniverseConfig(
        include_pairs=["ETHUSD"], exclude_pairs=[], min_24h_volume_usd=100000.0
    )

    universe = build_universe(mock_client, mock_region_profile, config)
    pair_names = {p.canonical for p in universe}

    assert pair_names == {"XBTUSD", "ETHUSD"}

    ticker_call_args = mock_client.get_public.call_args_list[1]
    called_pairs = set(ticker_call_args[1]["params"]["pair"].split(","))
    assert "ETHUSD" not in called_pairs
    assert called_pairs == {"XBTUSD", "DOGEUSD", "ETHUSDM", "XMRUSD", "ADAUSDF"}


def test_exclude_applies_to_forced_includes(
    mock_region_profile, mock_kraken_asset_pairs_response
):
    """exclude_pairs should remove pairs even if they are in include_pairs."""
    mock_client = MagicMock()
    mock_client.get_public.return_value = mock_kraken_asset_pairs_response

    config = UniverseConfig(
        include_pairs=["ETHUSD"], exclude_pairs=["ETHUSD"], min_24h_volume_usd=0
    )

    universe = build_universe(mock_client, mock_region_profile, config)

    assert "ETHUSD" not in {p.canonical for p in universe}


def test_universe_volume_filtering_missing_ticker(
    mock_region_profile, mock_kraken_asset_pairs_response
):
    """Tests that pairs with missing ticker data are retained (fallback behavior)."""
    mock_client = MagicMock()
    filtered_response = {
        k: v
        for k, v in mock_kraken_asset_pairs_response.items()
        if v["quote"] in ["ZUSD", "USD"]
        and v.get("aclass_base") == "currency"
        and v["status"] == "online"
    }

    mock_client.get_public.side_effect = [
        filtered_response,
        # Ticker response missing ETHUSDM
        {
            "XXBTZUSD": {"v": ["1000", "2500.5"], "c": ["50000.0", "1"]},  # >100k
            "XETHZUSD": {"v": ["500", "10.0"], "c": ["2000.0", "1"]},  # <100k
            "DOGEUSD": {"v": ["100000", "500000.0"], "c": ["0.1", "1"]},  # <100k
            # ETHUSDM, XMRUSD, ADAUSDF are missing from ticker
        },
    ]

    # Set a min volume of $100,000 USD
    config = UniverseConfig(
        include_pairs=[], exclude_pairs=[], min_24h_volume_usd=100000.0
    )

    universe = build_universe(mock_client, mock_region_profile, config)

    pair_names = {p.canonical for p in universe}

    assert len(universe) == 4
    assert "XBTUSD" in pair_names
    assert "ETHUSD" not in pair_names
    assert "DOGEUSD" not in pair_names
    assert "ETHUSDM" in pair_names  # Retained due to missing ticker data
    assert "XMRUSD" in pair_names  # Retained due to missing ticker data
    assert "ADAUSDF" in pair_names  # Retained due to missing ticker data
