# tests/test_portfolio_service.py

import pytest
from unittest.mock import MagicMock, patch
from kraken_bot.config import AppConfig, PortfolioConfig, RegionProfile, RegionCapabilities, UniverseConfig, MarketDataConfig
from kraken_bot.portfolio.manager import PortfolioService
from kraken_bot.portfolio.models import SpotPosition

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
    # Use memory DB or temp file
    db_path = tmp_path / "test_service.db"
    svc = PortfolioService(mock_config, mock_market_data, str(db_path))

    # Mock REST client
    svc.rest_client = MagicMock()
    return svc

def test_process_trade_buy(service):
    trade = {
        "id": "T1", "ordertxid": "O1", "pair": "XBTUSD", "time": 1000,
        "type": "buy", "ordertype": "limit", "price": 50000, "cost": 50000,
        "fee": 100, "vol": 1.0, "margin": 0, "misc": ""
    }

    # Setup Market Data for fee conversion (USD fee -> USD)
    service.market_data.get_latest_price.return_value = 1.0 # USDUSD? No, fee is usually Quote (USD)

    service.portfolio.ingest_trades([trade], persist=False)

    pos = service.positions["XBTUSD"]
    assert pos.base_size == 1.0
    assert pos.avg_entry_price == 50000.0
    assert pos.fees_paid_base == 100.0

def test_process_trade_sell_pnl(service):
    # 1. Buy 1 BTC @ 50k
    buy = {
        "id": "T1", "ordertxid": "O1", "pair": "XBTUSD", "time": 1000,
        "type": "buy", "ordertype": "limit", "price": 50000, "cost": 50000,
        "fee": 0, "vol": 1.0, "margin": 0, "misc": ""
    }
    service.portfolio.ingest_trades([buy], persist=False)

    # 2. Sell 0.5 BTC @ 60k
    sell = {
        "id": "T2", "ordertxid": "O2", "pair": "XBTUSD", "time": 1001,
        "type": "sell", "ordertype": "limit", "price": 60000, "cost": 30000,
        "fee": 10, "vol": 0.5, "margin": 0, "misc": ""
    }

    service.portfolio.ingest_trades([sell], persist=False)

    pos = service.positions["XBTUSD"]
    assert pos.base_size == 0.5
    # Avg entry price should remain 50k
    assert pos.avg_entry_price == 50000.0

    # Realized PnL: (60k - 50k) * 0.5 = 5000. Less fees (10) = 4990
    assert pos.realized_pnl_base == 4990.0

    # Check history
    assert len(service.realized_pnl_history) == 1
    rec = service.realized_pnl_history[0]
    assert rec.pnl_quote == 4990.0

def test_reconciliation_drift(service):
    # Setup internal state
    pos = SpotPosition("XBTUSD", "XBT", "USD", 1.0, 50000, 0, 0)
    service.positions["XBTUSD"] = pos

    # Mock Balance response
    # Case 1: Match
    service.rest_client.get_private.return_value = {"XXBT": "1.0"}
    service._reconcile()
    assert service.drift_flag == False

    # Case 2: Drift
    service.rest_client.get_private.return_value = {"XXBT": "0.5"}
    # Tolerance is 1.0 USD. Drift is 0.5 BTC * 50k = 25k USD.
    service._reconcile()
    assert service.drift_flag == True

def test_get_equity(service):
    # Setup balances
    from kraken_bot.portfolio.models import AssetBalance
    service.balances = {
        "USD": AssetBalance("USD", 10000, 0, 10000),
        "XBT": AssetBalance("XBT", 1.0, 0, 1.0)
    }
    # Mock prices: BTC=50k
    service.market_data.get_latest_price.side_effect = lambda p: 50000.0 if "XBT" in p else 1.0

    equity = service.get_equity()
    # 10k USD + 1 BTC(50k) = 60k
    assert equity.equity_base == 60000.0
    assert equity.cash_base == 10000.0


def test_equity_uses_fallback_price(service):
    pos = SpotPosition("XBTUSD", "XBT", "USD", 1.0, 100.0, 0, 0)
    service.positions["XBTUSD"] = pos
    service.balances = {}

    service.market_data.get_latest_price.return_value = None
    service.market_data._get_cached_price_from_store = MagicMock(return_value=120.0)

    equity = service.get_equity()

    pos = service.positions["XBTUSD"]
    assert pos.current_value_base == pytest.approx(120.0)
    assert equity.unrealized_pnl_base_total == pytest.approx(20.0)
    assert equity.drift_flag is True


def test_equity_sets_drift_when_no_price(service):
    pos = SpotPosition("XBTUSD", "XBT", "USD", 1.0, 100.0, 0, 0)
    service.positions["XBTUSD"] = pos
    service.balances = {}

    service.market_data.get_latest_price.return_value = None
    service.market_data._get_cached_price_from_store = MagicMock(return_value=None)
    service.market_data._get_rest_ticker_price = MagicMock(return_value=None)

    equity = service.get_equity()

    pos = service.positions["XBTUSD"]
    assert pos.current_value_base == pytest.approx(100.0)
    assert equity.unrealized_pnl_base_total == pytest.approx(0.0)
    assert equity.drift_flag is True
