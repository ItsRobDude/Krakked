from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from kraken_bot.config import PortfolioConfig
from kraken_bot.portfolio.models import AssetBalance
from kraken_bot.portfolio.portfolio import Portfolio
from kraken_bot.portfolio.store import PortfolioStore


class InMemoryStore(PortfolioStore):
    def save_trades(self, trades):
        pass

    def get_trades(self, pair=None, limit=None, since=None, until=None, ascending=False):
        return []

    def save_cash_flows(self, records):
        pass

    def get_cash_flows(self, asset=None, limit=None, since=None, until=None, ascending=False):
        return []

    def save_snapshot(self, snapshot):
        self.snapshots = getattr(self, "snapshots", []) + [snapshot]

    def get_snapshots(self, since=None, limit=None):
        return getattr(self, "snapshots", [])

    def prune_snapshots(self, older_than_ts: int):
        pass

    def add_decision(self, record):
        pass

    def get_decisions(self, plan_id=None, since=None, limit=None, strategy_name=None):
        return []

    def save_execution_plan(self, plan):
        pass

    def get_execution_plans(self, plan_id=None, since=None, limit=None):
        return []

    def get_execution_plan(self, plan_id):
        return None


@pytest.fixture
def market_data_mock():
    md = MagicMock()
    md.get_pair_metadata.return_value = SimpleNamespace(canonical="XBTUSD", base="XBT", quote="USD")
    return md


def test_equity_respects_asset_filters(market_data_mock):
    config = PortfolioConfig(base_currency="USD", include_assets=["USD"], exclude_assets=["XBT"])
    market_data_mock.get_latest_price.side_effect = lambda pair: 20000.0 if pair == "XBTUSD" else 1.0
    portfolio = Portfolio(config, market_data_mock, InMemoryStore())

    portfolio.balances = {
        "USD": AssetBalance("USD", 100.0, 0.0, 100.0),
        "XBT": AssetBalance("XBT", 1.0, 0.0, 1.0),
    }

    equity = portfolio.equity_view()
    assert equity.equity_base == 100.0

    exposures = portfolio.get_asset_exposure()
    assert [exp.asset for exp in exposures] == ["USD"]

    snapshot = portfolio.snapshot(now=1, persist=False, enforce_retention=False)
    assert [val.asset for val in snapshot.asset_valuations] == ["USD"]


def test_unvalued_assets_flagged_with_source_pair(market_data_mock):
    config = PortfolioConfig(base_currency="USD")
    market_data_mock.get_latest_price.side_effect = lambda pair: None
    portfolio = Portfolio(config, market_data_mock, InMemoryStore())

    portfolio.balances = {"EUR": AssetBalance("EUR", 50.0, 0.0, 50.0)}

    equity = portfolio.equity_view()
    assert "EUR" in equity.unvalued_assets

    exposures = portfolio.get_asset_exposure()
    assert exposures[0].valuation_status == "unvalued"
    assert exposures[0].source_pair == "EURUSD"

    snapshot = portfolio.snapshot(now=2, persist=False, enforce_retention=False)
    assert snapshot.asset_valuations[0].valuation_status == "unvalued"
    assert snapshot.asset_valuations[0].source_pair == "EURUSD"
