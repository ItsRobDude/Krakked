# tests/test_portfolio_service.py

from unittest.mock import MagicMock

import pytest

from krakked.config import (
    AppConfig,
    ExecutionConfig,
    MarketDataConfig,
    PortfolioConfig,
    RegionCapabilities,
    RegionProfile,
    SessionConfig,
    UniverseConfig,
)
from krakked.portfolio.manager import PortfolioService
from krakked.portfolio.models import AssetBalance, BalanceSnapshot, SpotPosition


@pytest.fixture
def mock_config():
    return AppConfig(
        region=RegionProfile("US", RegionCapabilities(False, False, False)),
        universe=UniverseConfig([], [], 0),
        market_data=MarketDataConfig({}, {}, [], []),
        portfolio=PortfolioConfig(),
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
    md.get_latest_price.side_effect = lambda pair: (
        50000.0 if "XBT" in str(pair) else 1.0
    )

    # Asset Normalization & Valuation
    def _norm(asset):
        asset = str(asset)
        if "XBT" in asset:
            return "XBT"
        if "USD" in asset:
            return "USD"
        return asset

    md.normalize_asset.side_effect = _norm

    def _val_pair(asset):
        asset = str(asset)
        if "XBT" in asset:
            return "XBTUSD"
        return None  # USD has no pair

    md.get_valuation_pair.side_effect = _val_pair

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
        "id": "T1",
        "ordertxid": "O1",
        "pair": "XBTUSD",
        "time": 1000,
        "type": "buy",
        "ordertype": "limit",
        "price": 50000,
        "cost": 50000,
        "fee": 100,
        "vol": 1.0,
        "margin": 0,
        "misc": "",
    }

    # Setup Market Data for fee conversion (USD fee -> USD)
    service.market_data.get_latest_price.return_value = (
        1.0  # USDUSD? No, fee is usually Quote (USD)
    )

    service.portfolio.ingest_trades([trade], persist=False)

    pos = service.positions["XBTUSD"]
    assert pos.base_size == 1.0
    assert pos.avg_entry_price == 50000.0
    assert pos.fees_paid_base == 100.0


def test_process_trade_sell_pnl(service):
    # 1. Buy 1 BTC @ 50k
    buy = {
        "id": "T1",
        "ordertxid": "O1",
        "pair": "XBTUSD",
        "time": 1000,
        "type": "buy",
        "ordertype": "limit",
        "price": 50000,
        "cost": 50000,
        "fee": 0,
        "vol": 1.0,
        "margin": 0,
        "misc": "",
    }
    service.portfolio.ingest_trades([buy], persist=False)

    # 2. Sell 0.5 BTC @ 60k
    sell = {
        "id": "T2",
        "ordertxid": "O2",
        "pair": "XBTUSD",
        "time": 1001,
        "type": "sell",
        "ordertype": "limit",
        "price": 60000,
        "cost": 30000,
        "fee": 10,
        "vol": 0.5,
        "margin": 0,
        "misc": "",
    }

    service.portfolio.ingest_trades([sell], persist=False)

    pos = service.positions["XBTUSD"]
    assert pos.base_size == 0.5
    # Avg entry price should remain 50k
    assert pos.avg_entry_price == 50000.0

    # Realized PnL: (60k - 50k) * 0.5 = 5000. Less fees (10) = 4990
    assert pos.realized_pnl_base == 4990.0

    # Check history (1 buy record + 1 sell record)
    assert len(service.realized_pnl_history) == 2
    # The second record is the sell
    rec = service.realized_pnl_history[1]
    assert rec.pnl_quote == 4990.0


def test_reconciliation_drift(service):
    # Setup internal state (Positions)
    pos = SpotPosition("XBTUSD", "XBT", "USD", 1.0, 50000, 0, 0)
    service.positions["XBTUSD"] = pos

    # Setup Ledger Balances (Source of Truth)
    from krakked.portfolio.models import AssetBalance

    service.balances["XBT"] = AssetBalance("XBT", 1.0, 0.0, 1.0)

    # Mock Balance response (Live)
    # Case 1: Match
    # Live matches Ledger (1.0 XBT)
    # Positions match Ledger (1.0 XBT)
    service.rest_client.get_private.return_value = {"XXBT": "1.0"}
    service._reconcile()
    assert not service.drift_flag

    # Case 2: Live Drift (Live Balance != Ledger Balance)
    service.rest_client.get_private.return_value = {"XXBT": "0.5"}
    # Tolerance is 1.0 USD. Drift is 0.5 BTC * 50k = 25k USD.
    service._reconcile()
    assert service.drift_flag

    # Case 3: Position Drift (Positions != Ledger Balance)
    # Reset Live to match Ledger so we isolate Position drift
    service.rest_client.get_private.return_value = {"XXBT": "1.0"}
    # Change Ledger Balance to mismatch Position (Position=1.0, Ledger=2.0)
    service.balances["XBT"].total = 2.0
    service._reconcile()
    assert service.drift_flag


def test_reconciliation_uses_relative_material_drift_threshold(service):
    service.config.reconciliation_tolerance = 1.0
    service.config.reconciliation_relative_tolerance_pct = 0.10
    service.balances["USD"] = AssetBalance("USD", 10000.0, 0.0, 10000.0)

    service.rest_client.get_private.return_value = {"ZUSD": "9995.0"}
    service._reconcile()
    assert service.drift_flag is False
    status = service.get_drift_status()
    assert status.expected_ledger_equity_base == pytest.approx(10000.0)
    assert status.relative_tolerance_base == pytest.approx(10.0)
    assert status.effective_tolerance_base == pytest.approx(10.0)

    service.rest_client.get_private.return_value = {"ZUSD": "9989.0"}
    service._reconcile()
    assert service.drift_flag is True
    assert service.get_drift_status().mismatched_assets[
        0
    ].difference_base == pytest.approx(11.0)


def test_reconciliation_aggregates_subthreshold_valued_mismatches(service):
    service.config.reconciliation_tolerance = 1.0
    service.config.reconciliation_relative_tolerance_pct = 0.10
    service.balances["USD"] = AssetBalance("USD", 50000.0, 0.0, 50000.0)
    service.balances["XBT"] = AssetBalance("XBT", 1.0, 0.0, 1.0)

    service.rest_client.get_private.return_value = {
        "ZUSD": "49960.0",
        "XXBT": "0.9992",
    }
    service._reconcile()
    assert service.drift_flag is False
    assert service.get_drift_status().aggregate_valued_drift_base == pytest.approx(80.0)

    service.rest_client.get_private.return_value = {
        "ZUSD": "49940.0",
        "XXBT": "0.9988",
    }
    service._reconcile()
    assert service.drift_flag is True
    status = service.get_drift_status()
    assert status.aggregate_valued_drift_base == pytest.approx(120.0)
    assert status.effective_tolerance_base == pytest.approx(100.0)
    assert {m.asset for m in status.mismatched_assets} == {"USD", "XBT"}


def test_reconciliation_uses_absolute_magnitudes_for_offsetting_mismatches(service):
    service.config.reconciliation_tolerance = 1.0
    service.config.reconciliation_relative_tolerance_pct = 0.10
    service.balances["USD"] = AssetBalance("USD", 50000.0, 0.0, 50000.0)
    service.balances["XBT"] = AssetBalance("XBT", 1.0, 0.0, 1.0)

    service.rest_client.get_private.return_value = {
        "ZUSD": "50060.0",
        "XXBT": "0.9988",
    }
    service._reconcile()

    status = service.get_drift_status()
    assert service.drift_flag is True
    assert status.aggregate_valued_drift_base == pytest.approx(120.0)
    assert status.effective_tolerance_base == pytest.approx(100.0)


def test_reconciliation_absolute_tolerance_remains_floor_for_small_equity(service):
    service.config.reconciliation_tolerance = 1.0
    service.config.reconciliation_relative_tolerance_pct = 0.10
    service.balances["USD"] = AssetBalance("USD", 100.0, 0.0, 100.0)

    service.rest_client.get_private.return_value = {"ZUSD": "99.5"}
    service._reconcile()
    assert service.drift_flag is False
    assert service.get_drift_status().effective_tolerance_base == pytest.approx(1.0)

    service.rest_client.get_private.return_value = {"ZUSD": "98.9"}
    service._reconcile()
    assert service.drift_flag is True


def test_reconciliation_unvalued_nonzero_mismatch_always_flags(service):
    service.config.reconciliation_tolerance = 1_000_000.0
    service.config.reconciliation_relative_tolerance_pct = 100.0
    service.balances["FOO"] = AssetBalance("FOO", 2.0, 0.0, 2.0)

    service.rest_client.get_private.return_value = {}
    service._reconcile()

    assert service.drift_flag is True
    mismatch = service.get_drift_status().mismatched_assets[0]
    assert mismatch.asset == "FOO"
    assert mismatch.mismatch_reason == "unvalued_quantity_mismatch"


def test_internal_position_balance_mismatch_uses_absolute_tolerance(service):
    service.config.reconciliation_tolerance = 1.0
    service.config.reconciliation_relative_tolerance_pct = 0.10
    service.balances["USD"] = AssetBalance("USD", 500000.0, 0.0, 500000.0)
    service.balances["XBT"] = AssetBalance("XBT", 0.99, 0.0, 0.99)
    service.positions["XBTUSD"] = SpotPosition(
        "XBTUSD",
        "XBT",
        "USD",
        1.0,
        50000.0,
        0.0,
        0.0,
    )
    service.rest_client.get_private.return_value = {
        "ZUSD": "500000.0",
        "XXBT": "0.99",
    }

    service._reconcile()

    status = service.get_drift_status()
    assert service.drift_flag is True
    mismatch = next(
        m
        for m in status.mismatched_assets
        if m.mismatch_reason == "internal_position_balance_mismatch"
    )
    assert mismatch.difference_base == pytest.approx(500.0)
    assert mismatch.effective_tolerance_base == pytest.approx(1.0)


def test_get_equity(service):
    # Setup balances
    from krakked.portfolio.models import AssetBalance

    service.balances = {
        "USD": AssetBalance("USD", 10000, 0, 10000),
        "XBT": AssetBalance("XBT", 1.0, 0, 1.0),
    }
    # Mock prices: BTC=50k
    service.market_data.get_latest_price.side_effect = lambda p: (
        50000.0 if "XBT" in p else 1.0
    )

    equity = service.get_equity()
    # 10k USD + 1 BTC(50k) = 60k
    assert equity.equity_base == 60000.0
    assert equity.cash_base == 10000.0


def test_equity_uses_fallback_price(service):
    pos = SpotPosition("XBTUSD", "XBT", "USD", 1.0, 100.0, 0, 0)
    service.positions["XBTUSD"] = pos
    service.balances = {}

    service.market_data.get_latest_price.side_effect = None
    service.market_data.get_latest_price.return_value = None

    # Mock get_ohlc which is the new public fallback
    mock_bar = MagicMock()
    mock_bar.close = 120.0
    service.market_data.get_ohlc = MagicMock(return_value=[mock_bar])

    equity = service.get_equity()

    pos = service.positions["XBTUSD"]
    assert pos.current_value_base == pytest.approx(120.0)
    assert equity.unrealized_pnl_base_total == pytest.approx(20.0)
    assert equity.drift_flag is True


def test_paper_bootstrap_seeds_synthetic_starting_cash(mock_market_data, tmp_path):
    config = AppConfig(
        region=RegionProfile("US", RegionCapabilities(False, False, False)),
        universe=UniverseConfig([], [], 0),
        market_data=MarketDataConfig({}, {}, [], []),
        portfolio=PortfolioConfig(),
        execution=ExecutionConfig(mode="paper", validate_only=False),
        session=SessionConfig(mode="paper", profile_name="rob"),
    )
    service = PortfolioService(config, mock_market_data, str(tmp_path / "paper.db"))
    service.rest_client = MagicMock()

    service._bootstrap_from_store()

    assert service.balances["USD"].total == pytest.approx(10000.0)
    snapshot = service.store.get_latest_balance_snapshot()
    assert snapshot is not None
    assert snapshot.balances["USD"].total == pytest.approx(10000.0)


def test_paper_bootstrap_preserves_local_paper_state_across_restart(
    mock_market_data, tmp_path
):
    config = AppConfig(
        region=RegionProfile("US", RegionCapabilities(False, False, False)),
        universe=UniverseConfig([], [], 0),
        market_data=MarketDataConfig({}, {}, [], []),
        portfolio=PortfolioConfig(),
        execution=ExecutionConfig(mode="paper", validate_only=False),
        session=SessionConfig(mode="paper", profile_name="rob"),
    )
    db_path = tmp_path / "paper.db"

    first = PortfolioService(config, mock_market_data, str(db_path))
    first.rest_client = MagicMock()
    first._bootstrap_from_store()
    first.ingest_simulated_trades(
        [
            {
                "id": "paper-trade-1",
                "ordertxid": "order-1",
                "pair": "XBTUSD",
                "time": 1000,
                "type": "buy",
                "ordertype": "limit",
                "price": 50000.0,
                "cost": 5000.0,
                "fee": 0.0,
                "vol": 0.1,
                "margin": 0.0,
                "misc": "",
            }
        ]
    )

    second = PortfolioService(config, mock_market_data, str(db_path))
    second.rest_client = MagicMock()
    second._bootstrap_from_store()

    assert second.balances["USD"].total == pytest.approx(5000.0)
    assert second.balances["XBT"].total == pytest.approx(0.1)
    assert second.positions["XBTUSD"].base_size == pytest.approx(0.1)


def test_paper_bootstrap_resets_legacy_exchange_reference_snapshot(
    mock_market_data, tmp_path
):
    config = AppConfig(
        region=RegionProfile("US", RegionCapabilities(False, False, False)),
        universe=UniverseConfig([], [], 0),
        market_data=MarketDataConfig({}, {}, [], []),
        portfolio=PortfolioConfig(),
        execution=ExecutionConfig(mode="paper", validate_only=False),
        session=SessionConfig(mode="paper", profile_name="rob"),
    )
    service = PortfolioService(config, mock_market_data, str(tmp_path / "paper.db"))
    service.rest_client = MagicMock()
    service.store.save_balance_snapshot(
        BalanceSnapshot(
            id=None,
            time=1000.0,
            last_ledger_id="",
            balances={
                "USD": AssetBalance("USD", 14.52, 0.0, 14.52),
                "XBT": AssetBalance("XBT", 0.0001, 0.0, 0.0001),
            },
        )
    )

    service._bootstrap_from_store()

    assert set(service.balances) == {"USD"}
    assert service.balances["USD"].total == pytest.approx(10000.0)


def test_paper_sync_keeps_local_wallet_and_refreshes_exchange_reference(
    mock_market_data, tmp_path
):
    config = AppConfig(
        region=RegionProfile("US", RegionCapabilities(False, False, False)),
        universe=UniverseConfig([], [], 0),
        market_data=MarketDataConfig({}, {}, [], []),
        portfolio=PortfolioConfig(),
        execution=ExecutionConfig(mode="paper", validate_only=False),
        session=SessionConfig(mode="paper", profile_name="rob"),
    )
    service = PortfolioService(config, mock_market_data, str(tmp_path / "paper.db"))
    service.rest_client = MagicMock()
    service.rest_client.get_private.return_value = {"ZUSD": "14.52", "XXBT": "0.0001"}

    service._bootstrap_from_store()
    result = service.sync()

    assert result == {"new_trades": 0, "new_cash_flows": 0}
    assert service.balances["USD"].total == pytest.approx(10000.0)
    reference = service.get_exchange_reference_summary()
    assert reference is not None
    assert reference["cash_usd"] == pytest.approx(14.52)


def test_equity_sets_drift_when_no_price(service):
    pos = SpotPosition("XBTUSD", "XBT", "USD", 1.0, 100.0, 0, 0)
    service.positions["XBTUSD"] = pos
    service.balances = {}

    service.market_data.get_latest_price.side_effect = None
    service.market_data.get_latest_price.return_value = None
    # We no longer access private methods. Just ensure get_ohlc returns empty
    service.market_data.get_ohlc = MagicMock(return_value=[])

    equity = service.get_equity()

    pos = service.positions["XBTUSD"]
    assert pos.current_value_base == pytest.approx(100.0)
    assert equity.unrealized_pnl_base_total == pytest.approx(0.0)
    assert equity.drift_flag is True
