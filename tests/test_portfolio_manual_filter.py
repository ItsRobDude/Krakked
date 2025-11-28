import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock

from kraken_bot.config import PortfolioConfig
from kraken_bot.portfolio.portfolio import Portfolio
from kraken_bot.portfolio.store import PortfolioStore


class InMemoryStore(PortfolioStore):
    def save_trades(self, trades):
        self.trades = getattr(self, "trades", []) + trades

    def get_trades(self, pair=None, limit=None, since=None, until=None, ascending=False):
        return getattr(self, "trades", [])

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
    md.get_latest_price.side_effect = lambda pair: 110.0 if pair == "XBTUSD" else None
    return md


@pytest.fixture
def portfolio(market_data_mock):
    config = PortfolioConfig(base_currency="USD", track_manual_trades=False)
    return Portfolio(config, market_data_mock, InMemoryStore())


def test_manual_trades_respect_toggle_in_equity_view(portfolio):
    manual_buy = {
        "id": "T1",
        "pair": "XBTUSD",
        "time": 1,
        "type": "buy",
        "price": "100",
        "cost": "100",
        "fee": "0",
        "vol": "1",
    }

    portfolio.ingest_trades([manual_buy], persist=False)

    default_view = portfolio.equity_view()
    assert default_view.unrealized_pnl_base_total == 0

    manual_included = portfolio.equity_view(include_manual=True)
    assert pytest.approx(manual_included.unrealized_pnl_base_total, rel=1e-6) == 10.0


def test_realized_pnl_tags_and_manual_filtering(portfolio):
    tagged_buy = {
        "id": "T1",
        "pair": "XBTUSD",
        "time": 1,
        "type": "buy",
        "price": "100",
        "cost": "100",
        "fee": "0",
        "vol": "1",
        "userref": 123,
        "comment": "note",
    }
    tagged_sell = {
        "id": "T2",
        "pair": "XBTUSD",
        "time": 2,
        "type": "sell",
        "price": "110",
        "cost": "110",
        "fee": "0",
        "vol": "1",
        "userref": 123,
        "comment": "note",
    }

    portfolio.ingest_trades([tagged_buy, tagged_sell], persist=False)

    assert len(portfolio.realized_pnl_history) == 1
    record = portfolio.realized_pnl_history[0]
    assert record.strategy_tag == "123"
    assert record.raw_userref == "123"
    assert record.comment == "note"

    manual_filtered = portfolio.equity_view()
    assert pytest.approx(manual_filtered.realized_pnl_base_total, rel=1e-6) == 10.0

    include_manual = portfolio.equity_view(include_manual=True)
    assert pytest.approx(include_manual.realized_pnl_base_total, rel=1e-6) == 10.0


def test_manual_realized_pnl_filtered_by_config(portfolio):
    manual_buy = {
        "id": "T1",
        "pair": "XBTUSD",
        "time": 1,
        "type": "buy",
        "price": "100",
        "cost": "100",
        "fee": "0",
        "vol": "1",
    }
    manual_sell = {
        "id": "T2",
        "pair": "XBTUSD",
        "time": 2,
        "type": "sell",
        "price": "110",
        "cost": "110",
        "fee": "0",
        "vol": "1",
    }

    portfolio.ingest_trades([manual_buy, manual_sell], persist=False)

    assert portfolio.realized_pnl_history[0].strategy_tag == "manual"

    filtered_view = portfolio.equity_view()
    assert filtered_view.realized_pnl_base_total == 0

    manual_view = portfolio.equity_view(include_manual=True)
    assert pytest.approx(manual_view.realized_pnl_base_total, rel=1e-6) == 10.0
