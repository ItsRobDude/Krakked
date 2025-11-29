from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from kraken_bot.portfolio.models import AssetExposure, EquityView, SpotPosition


@pytest.fixture
def portfolio_context(client: TestClient):
    return client.app.state.context


def test_portfolio_summary_enveloped(client, portfolio_context):
    portfolio_context.portfolio.get_equity.return_value = EquityView(
        equity_base=1000.0,
        cash_base=500.0,
        realized_pnl_base_total=50.0,
        unrealized_pnl_base_total=25.0,
        drift_flag=True,
    )
    portfolio_context.portfolio.get_latest_snapshot.return_value = SimpleNamespace(timestamp=123456)

    response = client.get("/api/portfolio/summary")

    assert response.status_code == 200
    payload = response.json()
    assert payload["error"] is None
    assert payload["data"]["equity_usd"] == 1000.0
    assert payload["data"]["last_snapshot_ts"] == 123456


def test_positions_shape_matches_payload(client, portfolio_context):
    portfolio_context.market_data.get_latest_price.return_value = 2.5
    portfolio_context.portfolio.get_positions.return_value = [
        SpotPosition(
            pair="BTC/USD",
            base_asset="BTC",
            quote_asset="USD",
            base_size=0.5,
            avg_entry_price=10000.0,
            realized_pnl_base=0.0,
            fees_paid_base=0.0,
            strategy_tag="alpha",
        )
    ]

    response = client.get("/api/portfolio/positions")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data[0]["pair"] == "BTC/USD"
    assert data[0]["value_usd"] == pytest.approx(1.25)
    assert data[0]["unrealized_pnl_usd"] == pytest.approx(-4999.375)


def test_exposure_breakdown_enveloped(client, portfolio_context):
    portfolio_context.portfolio.get_asset_exposure.return_value = [
        AssetExposure(asset="BTC", amount=1.0, value_base=100.0, percentage_of_equity=0.1)
    ]
    portfolio_context.strategy_engine.get_risk_status.return_value = SimpleNamespace(
        per_strategy_exposure_pct={"alpha": 5.0}
    )

    response = client.get("/api/portfolio/exposure")

    assert response.status_code == 200
    payload = response.json()
    assert payload["error"] is None
    assert payload["data"]["by_asset"][0] == {
        "asset": "BTC",
        "value_usd": 100.0,
        "pct_of_equity": 0.1,
    }
    assert payload["data"]["by_strategy"] == [
        {"strategy_id": "alpha", "value_usd": None, "pct_of_equity": 5.0}
    ]


def test_trades_filter_and_envelope(client, portfolio_context):
    trade_history = [
        {"id": "t1", "strategy_tag": "s1"},
        {"id": "t2", "strategy_tag": "other"},
    ]
    portfolio_context.portfolio.get_trade_history.return_value = trade_history

    response = client.get("/api/portfolio/trades?strategy_id=s1&limit=1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["error"] is None
    assert payload["data"] == [{"id": "t1", "strategy_tag": "s1"}]
    portfolio_context.portfolio.get_trade_history.assert_called_once_with(
        pair=None, limit=1, since=None, until=None, ascending=False
    )


@pytest.mark.parametrize("ui_read_only", [False])
def test_create_snapshot_updates_mock(client, portfolio_context):
    portfolio_context.portfolio.create_snapshot.return_value = SimpleNamespace(
        timestamp=10,
        equity_base=1.0,
        cash_base=0.5,
        realized_pnl_base_total=0.1,
        unrealized_pnl_base_total=0.2,
    )

    response = client.post("/api/portfolio/snapshot")

    assert response.status_code == 200
    payload = response.json()
    portfolio_context.portfolio.create_snapshot.assert_called_once()
    assert payload["error"] is None
    assert payload["data"]["timestamp"] == 10


@pytest.mark.parametrize("ui_read_only", [True])
def test_create_snapshot_blocked_in_read_only(client, portfolio_context):
    response = client.post("/api/portfolio/snapshot")

    assert response.status_code == 200
    assert response.json() == {"data": None, "error": "UI is in read-only mode"}
    portfolio_context.portfolio.create_snapshot.assert_not_called()
