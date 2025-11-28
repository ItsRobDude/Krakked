# tests/test_portfolio_service_features.py

import pytest
from unittest.mock import MagicMock
from kraken_bot.config import AppConfig, PortfolioConfig, RegionProfile, RegionCapabilities, UniverseConfig, MarketDataConfig
from kraken_bot.portfolio.manager import PortfolioService
from kraken_bot.portfolio.models import PortfolioSnapshot

@pytest.fixture
def mock_config():
    return AppConfig(
        region=RegionProfile("US", RegionCapabilities(False, False, False)),
        universe=UniverseConfig([], [], 0),
        market_data=MarketDataConfig({}, {}, [], []),
        portfolio=PortfolioConfig()
    )

@pytest.fixture
def mock_market_data():
    md = MagicMock()
    # Setup some pairs
    pair_meta = MagicMock()
    pair_meta.canonical = "XBTUSD"
    pair_meta.base = "XBT"
    pair_meta.quote = "USD"
    md.get_pair_metadata.return_value = pair_meta

    # Prices
    md.get_latest_price.side_effect = lambda pair: 50000.0 if "XBT" in pair else 1.0
    return md

@pytest.fixture
def service(mock_config, mock_market_data, tmp_path):
    db_path = tmp_path / "test_features.db"
    svc = PortfolioService(mock_config, mock_market_data, str(db_path))
    svc.rest_client = MagicMock()
    return svc

def test_sync_pagination(service):
    # Mock TradesHistory to return 2 pages
    # Page 1: 50 items
    page1_trades = {f"T{i}": {"pair": "XBTUSD", "time": 1000+i, "type": "buy", "price": "50000", "cost": "50000", "fee": "0", "vol": "1"} for i in range(50)}
    # Page 2: 10 items
    page2_trades = {f"T{i}": {"pair": "XBTUSD", "time": 2000+i, "type": "buy", "price": "50000", "cost": "50000", "fee": "0", "vol": "1"} for i in range(50, 60)}

    # Side effect for get_private
    def side_effect(endpoint, params=None):
        if endpoint == "TradesHistory":
            params = params or {}
            start = float(params.get("start", 0))

            # Combine all trades
            all_trades = {**page1_trades, **page2_trades}

            # Filter by start (exclusive behavior emulation)
            filtered_trades = {k: v for k, v in all_trades.items() if v['time'] > start}

            # Limit to 50
            # Convert to list to sort and slice
            sorted_items = sorted(filtered_trades.items(), key=lambda x: x[1]['time'])
            sliced_items = sorted_items[:50]

            result_dict = {k: v for k, v in sliced_items}
            return {"trades": result_dict, "count": len(all_trades)}
        # Mock other calls
        if endpoint == "Balance":
            return {}
        return {}

    service.rest_client.get_private.side_effect = side_effect
    service.rest_client.get_ledgers.return_value = {} # No ledgers

    result = service.sync()

    # Total trades should be 60
    assert result["new_trades"] == 60

    # Verify DB
    trades_in_db = service.store.get_trades()
    assert len(trades_in_db) == 60

def test_create_snapshot(service):
    # Setup some state
    from kraken_bot.portfolio.models import AssetBalance
    service.balances = {"XBT": AssetBalance("XBT", 1.0, 0, 1.0)}

    snapshot = service.create_snapshot()

    assert isinstance(snapshot, PortfolioSnapshot)
    assert snapshot.equity_base == 50000.0
    assert len(snapshot.asset_valuations) == 1
    assert snapshot.asset_valuations[0].asset == "XBT"

    # Verify DB persistence
    snapshots_db = service.store.get_snapshots()
    assert len(snapshots_db) == 1
    assert snapshots_db[0].equity_base == 50000.0
