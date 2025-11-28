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
    # Set precision for rounding tests
    pair_meta.price_decimals = 1
    pair_meta.volume_decimals = 8

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

    service._process_trade(trade)

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
    service._process_trade(buy)

    # 2. Sell 0.5 BTC @ 60k
    sell = {
        "id": "T2", "ordertxid": "O2", "pair": "XBTUSD", "time": 1001,
        "type": "sell", "ordertype": "limit", "price": 60000, "cost": 30000,
        "fee": 10, "vol": 0.5, "margin": 0, "misc": ""
    }

    service._process_trade(sell)

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

def test_strategy_tagging(service):
    # 1. Store an order with userref
    order = {
        "id": "O100",
        "status": "closed",
        "userref": 999,
        "descr": {"pair": "XBTUSD", "type": "buy"}
    }
    service.store.save_orders([order])

    # 2. Process a trade linked to that order
    trade = {
        "id": "T100", "ordertxid": "O100", "pair": "XBTUSD", "time": 1000,
        "type": "sell", "price": 50000, "cost": 50000, "fee": 0, "vol": 1.0
    }
    # Need a position to sell against or just force it (shorting not supported but math runs)
    # Let's seed a position first
    service.positions["XBTUSD"] = SpotPosition("XBTUSD", "XBT", "USD", 1.0, 40000, 0, 0)

    service._process_trade(trade)

    # Verify tag
    rec = service.realized_pnl_history[0]
    assert rec.strategy_tag == "userref:999"

def test_rounding(service):
    # pair_meta has price_decimals=1, vol_decimals=8

    trade = {
        "id": "T1", "ordertxid": "O1", "pair": "XBTUSD", "time": 1000,
        "type": "buy", "price": 50000.1234, "cost": 50000,
        "fee": 0, "vol": 1.123456789
    }

    service._process_trade(trade)

    pos = service.positions["XBTUSD"]
    # Volume should be rounded to 8 decimals
    # 1.123456789 -> 1.12345679 (round half up default? Python round)
    # Python round(1.123456789, 8) -> 1.12345679
    assert pos.base_size == 1.12345679

def test_get_trade_history_filtering(service):
    # Setup some dummy trades in store
    trades = [
        {"id": "T_MANUAL", "ordertxid": "O_MANUAL", "pair": "XBTUSD", "time": 1000, "type": "buy", "price": 50000, "cost": 50000, "fee": 0, "vol": 1},
        {"id": "T_BOT", "ordertxid": "O_BOT", "pair": "XBTUSD", "time": 1001, "type": "buy", "price": 50000, "cost": 50000, "fee": 0, "vol": 1}
    ]
    service.store.save_trades(trades)

    # Setup orders for tagging
    orders = [
        {"id": "O_MANUAL", "status": "closed", "userref": None}, # No userref = manual
        {"id": "O_BOT", "status": "closed", "userref": 123} # Userref = bot
    ]
    service.store.save_orders(orders)

    # Pre-populate cache so _resolve_strategy_tag works without full rebuild logic if needed
    # But _resolve_strategy_tag calls store.get_order which works on SQLite.

    # 1. Default (None) -> Include All
    hist = service.get_trade_history(include_manual=None)
    assert len(hist) == 2

    # 2. False -> Exclude Manual
    hist = service.get_trade_history(include_manual=False)
    assert len(hist) == 1
    assert hist[0]['id'] == "T_BOT"

    # 3. True -> Include All (Explicit)
    hist = service.get_trade_history(include_manual=True)
    assert len(hist) == 2
