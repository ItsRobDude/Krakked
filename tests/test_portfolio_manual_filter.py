from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from kraken_bot.config import PortfolioConfig
from kraken_bot.portfolio.portfolio import Portfolio
from kraken_bot.portfolio.store import MAX_ML_TRAINING_EXAMPLES, PortfolioStore


class InMemoryStore(PortfolioStore):
    def save_trades(self, trades):
        self.trades = getattr(self, "trades", []) + trades

    def get_trades(
        self, pair=None, limit=None, since=None, until=None, ascending=False
    ):
        return getattr(self, "trades", [])

    def save_cash_flows(self, records):
        pass

    def get_cash_flows(
        self, asset=None, limit=None, since=None, until=None, ascending=False
    ):
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

    def save_order(self, order):
        self.orders = getattr(self, "orders", []) + [order]

    def update_order_status(
        self,
        local_id,
        status,
        kraken_order_id=None,
        cumulative_base_filled=None,
        avg_fill_price=None,
        last_error=None,
        raw_response=None,
        event_message=None,
    ):
        pass

    def save_execution_result(self, result):
        self.execution_results = getattr(self, "execution_results", []) + [result]

    def get_execution_plans(self, plan_id=None, since=None, limit=None):
        return []

    def get_execution_plan(self, plan_id):
        return None

    def get_order_by_reference(self, kraken_order_id=None, userref=None):
        return None

    def get_open_orders(self, plan_id=None, strategy_id=None):
        return getattr(self, "orders", [])

    def get_execution_results(self, limit: int = 10):
        return getattr(self, "execution_results", [])[:limit]

    def save_ledger_entry(self, entry):
        ledgers = getattr(self, "ledger_entries", [])
        ledgers.append(entry)
        self.ledger_entries = ledgers

    def get_ledger_entries(self, after_id=None, limit=None, since=None):
        entries = getattr(self, "ledger_entries", [])
        # Naive implementation: assume sorted insertion or don't care about order for basic tests
        if since:
            entries = [e for e in entries if e.time >= since]
        if after_id:
            # Find index
            try:
                idx = next(i for i, e in enumerate(entries) if e.id == after_id)
                entries = entries[idx + 1 :]
            except StopIteration:
                pass  # or empty?
        if limit:
            entries = entries[:limit]
        return entries

    def get_all_ledger_entries(self):
        return getattr(self, "ledger_entries", [])

    def get_latest_ledger_entry(self):
        entries = getattr(self, "ledger_entries", [])
        return entries[-1] if entries else None

    def save_balance_snapshot(self, snapshot):
        self.balance_snapshots = getattr(self, "balance_snapshots", []) + [snapshot]

    def get_latest_balance_snapshot(self):
        snaps = getattr(self, "balance_snapshots", [])
        return snaps[-1] if snaps else None

    def record_ml_example(
        self,
        strategy_id: str,
        model_key: str,
        *,
        created_at,
        source_mode: str,
        label_type: str,
        features,
        label,
        sample_weight: float = 1.0,
    ) -> None:
        examples = getattr(self, "ml_examples", {})
        window = examples.setdefault((strategy_id, model_key), [])
        window.append((list(features), float(label)))
        self.ml_examples = examples

    def load_ml_training_window(
        self,
        strategy_id: str,
        model_key: str,
        *,
        max_examples: int = MAX_ML_TRAINING_EXAMPLES,
        return_weights: bool = False,
    ) -> (
        tuple[list[list[float]], list[float]]
        | tuple[list[list[float]], list[float], list[float]]
    ):
        examples = self.ml_examples.get((strategy_id, model_key), [])
        # keep only the newest max_examples
        window = examples[-max_examples:]
        X = [features for features, _ in window]
        y = [label for _, label in window]
        if return_weights:
            weights = [1.0 for _ in window]
            return X, y, weights
        return X, y

    def save_ml_model(
        self,
        strategy_id: str,
        model_key: str,
        *,
        label_type: str,
        framework: str,
        model: object,
        version: int = 1,
    ) -> None:
        models = getattr(self, "ml_models", {})
        models[(strategy_id, model_key)] = {
            "label_type": label_type,
            "framework": framework,
            "model": model,
            "version": version,
        }
        self.ml_models = models

    def load_ml_model(self, strategy_id: str, model_key: str):
        return (
            getattr(self, "ml_models", {})
            .get((strategy_id, model_key), {})
            .get("model")
        )


@pytest.fixture
def market_data_mock():
    md = MagicMock()
    md.get_pair_metadata.return_value = SimpleNamespace(
        canonical="XBTUSD", base="XBT", quote="USD"
    )
    md.get_latest_price.side_effect = lambda pair: 110.0 if str(pair) == "XBTUSD" else None

    def _norm(asset):
        asset = str(asset)
        return {"XXBT": "XBT", "XBT": "XBT", "ZUSD": "USD", "USD": "USD"}.get(asset, asset)

    md.normalize_asset.side_effect = _norm
    md.get_valuation_pair.side_effect = lambda asset: "XBTUSD" if _norm(asset) == "XBT" else None

    return md


@pytest.fixture
def portfolio(market_data_mock):
    config = PortfolioConfig(base_currency="USD", track_manual_trades=False)
    return Portfolio(config, market_data_mock, InMemoryStore())


@pytest.fixture
def portfolio_with_manual_default(market_data_mock):
    config = PortfolioConfig(base_currency="USD", track_manual_trades=True)
    return Portfolio(
        config,
        market_data_mock,
        InMemoryStore(),
        strategy_tags={"trend_core": "trend_core"},
        userref_to_strategy={"42": "trend_core"},
    )


@pytest.fixture
def portfolio_with_strategy_tags(market_data_mock):
    config = PortfolioConfig(base_currency="USD", track_manual_trades=True)
    return Portfolio(
        config,
        market_data_mock,
        InMemoryStore(),
        strategy_tags={"trend_core": "trend_core"},
        userref_to_strategy={"trend_core": "trend_core"},
    )


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

    assert len(portfolio.realized_pnl_history) == 2
    # First record is buy (fee), second is sell
    rec = portfolio.realized_pnl_history[1]
    assert rec.strategy_tag is None
    assert rec.raw_userref == "123"
    assert rec.comment == "note"

    manual_filtered = portfolio.equity_view()
    assert pytest.approx(manual_filtered.realized_pnl_base_total, rel=1e-6) == 0.0

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

    assert portfolio.realized_pnl_history[0].strategy_tag is None

    filtered_view = portfolio.equity_view()
    assert filtered_view.realized_pnl_base_total == 0

    manual_view = portfolio.equity_view(include_manual=True)
    assert pytest.approx(manual_view.realized_pnl_base_total, rel=1e-6) == 10.0


def test_realized_pnl_by_strategy_groups_manual_and_tagged(
    portfolio_with_manual_default,
):
    tagged_buy = {
        "id": "T1",
        "pair": "XBTUSD",
        "time": 1,
        "type": "buy",
        "price": "100",
        "cost": "100",
        "fee": "0",
        "vol": "1",
        "userref": 42,
    }
    tagged_sell = {
        "id": "T2",
        "pair": "XBTUSD",
        "time": 2,
        "type": "sell",
        "price": "120",
        "cost": "120",
        "fee": "0",
        "vol": "1",
        "userref": 42,
    }
    manual_buy = {
        "id": "T3",
        "pair": "XBTUSD",
        "time": 3,
        "type": "buy",
        "price": "50",
        "cost": "50",
        "fee": "0",
        "vol": "1",
    }
    manual_sell = {
        "id": "T4",
        "pair": "XBTUSD",
        "time": 4,
        "type": "sell",
        "price": "70",
        "cost": "70",
        "fee": "0",
        "vol": "1",
    }

    portfolio_with_manual_default.ingest_trades(
        [tagged_buy, tagged_sell, manual_buy, manual_sell], persist=False
    )

    grouped = portfolio_with_manual_default.get_realized_pnl_by_strategy()

    assert pytest.approx(grouped["trend_core"], rel=1e-6) == 20.0
    assert pytest.approx(grouped["manual"], rel=1e-6) == 20.0


def test_realized_pnl_by_strategy_respects_manual_flag(market_data_mock):
    manual_buy = {
        "id": "T1",
        "pair": "XBTUSD",
        "time": 1,
        "type": "buy",
        "price": "50",
        "cost": "50",
        "fee": "0",
        "vol": "1",
    }
    manual_sell = {
        "id": "T2",
        "pair": "XBTUSD",
        "time": 2,
        "type": "sell",
        "price": "70",
        "cost": "70",
        "fee": "0",
        "vol": "1",
    }
    strategy_buy = {
        "id": "T3",
        "pair": "XBTUSD",
        "time": 3,
        "type": "buy",
        "price": "100",
        "cost": "100",
        "fee": "0",
        "vol": "1",
        "userref": 99,
    }
    strategy_sell = {
        "id": "T4",
        "pair": "XBTUSD",
        "time": 4,
        "type": "sell",
        "price": "110",
        "cost": "110",
        "fee": "0",
        "vol": "1",
        "userref": 99,
    }

    strategy_portfolio = Portfolio(
        PortfolioConfig(base_currency="USD", track_manual_trades=False),
        market_data_mock,
        InMemoryStore(),
        strategy_tags={"trend_core": "trend_core"},
        userref_to_strategy={"99": "trend_core"},
    )

    strategy_portfolio.ingest_trades(
        [manual_buy, manual_sell, strategy_buy, strategy_sell], persist=False
    )

    default_grouped = strategy_portfolio.get_realized_pnl_by_strategy()
    assert "manual" not in default_grouped
    assert pytest.approx(default_grouped["trend_core"], rel=1e-6) == 10.0

    grouped_with_manual = strategy_portfolio.get_realized_pnl_by_strategy(
        include_manual=True
    )
    assert pytest.approx(grouped_with_manual["manual"], rel=1e-6) == 20.0
    assert pytest.approx(grouped_with_manual["trend_core"], rel=1e-6) == 10.0


def test_userref_mapping_sets_strategy_tag(portfolio_with_strategy_tags):
    buy = {
        "id": "T1",
        "pair": "XBTUSD",
        "time": 1,
        "type": "buy",
        "price": "100",
        "cost": "100",
        "fee": "0",
        "vol": "1",
        "userref": "trend_core:1h",
    }
    sell = {
        "id": "T2",
        "pair": "XBTUSD",
        "time": 2,
        "type": "sell",
        "price": "110",
        "cost": "110",
        "fee": "0",
        "vol": "1",
        "userref": "trend_core:1h",
    }

    portfolio_with_strategy_tags.ingest_trades([buy, sell], persist=False)

    assert (
        portfolio_with_strategy_tags.realized_pnl_history[-1].strategy_tag
        == "trend_core"
    )

    pnl_by_strategy = portfolio_with_strategy_tags.get_realized_pnl_by_strategy(
        include_manual=True
    )
    assert "trend_core" in pnl_by_strategy
