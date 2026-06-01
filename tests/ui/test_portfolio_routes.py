import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from starlette.testclient import TestClient

from krakked.portfolio.models import AssetExposure, EquityView, SpotPosition
from krakked.ui import route_runtime


@pytest.fixture
def portfolio_context(client: TestClient):
    return client.context  # type: ignore[attr-defined]


def test_portfolio_summary_enveloped(client, portfolio_context):
    portfolio_context.portfolio.get_cached_equity.return_value = EquityView(
        equity_base=1000.0,
        cash_base=500.0,
        realized_pnl_base_total=50.0,
        unrealized_pnl_base_total=25.0,
        drift_flag=True,
    )
    portfolio_context.portfolio.get_cached_last_snapshot_ts.return_value = 123456

    response = client.get("/api/portfolio/summary")

    assert response.status_code == 200
    payload = response.json()
    assert payload["error"] is None
    assert payload["data"]["equity_usd"] == 1000.0
    assert payload["data"]["last_snapshot_ts"] == 123456
    assert payload["data"]["portfolio_baseline"] == "ledger_history"


def test_portfolio_summary_reports_exchange_balance_baseline(client, portfolio_context):
    portfolio_context.portfolio.get_cached_equity.return_value = EquityView(
        equity_base=42.0,
        cash_base=10.0,
        realized_pnl_base_total=0.0,
        unrealized_pnl_base_total=1.5,
        drift_flag=False,
    )
    portfolio_context.portfolio.get_cached_last_snapshot_ts.return_value = 987654
    portfolio_context.portfolio.baseline_source = "exchange_balances"

    response = client.get("/api/portfolio/summary")

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["portfolio_baseline"] == "exchange_balances"
    assert payload["cash_usd"] == 10.0


def test_portfolio_summary_includes_exchange_reference_details(client, portfolio_context):
    portfolio_context.portfolio.get_cached_equity.return_value = EquityView(
        equity_base=10000.0,
        cash_base=10000.0,
        realized_pnl_base_total=0.0,
        unrealized_pnl_base_total=0.0,
        drift_flag=False,
    )
    portfolio_context.portfolio.get_cached_last_snapshot_ts.return_value = 111111
    portfolio_context.portfolio.baseline_source = "paper_wallet"
    portfolio_context.portfolio.get_exchange_reference_summary.return_value = {
        "equity_usd": 14.52,
        "cash_usd": 14.52,
        "checked_at": "2026-04-20T20:53:00Z",
    }

    response = client.get("/api/portfolio/summary")

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["portfolio_baseline"] == "paper_wallet"
    assert payload["exchange_reference_equity_usd"] == 14.52
    assert payload["exchange_reference_cash_usd"] == 14.52
    assert payload["exchange_reference_checked_at"] == "2026-04-20T20:53:00Z"


def test_portfolio_summary_times_out_fast(client, portfolio_context, monkeypatch):
    monkeypatch.setattr(route_runtime, "DEFAULT_UI_ROUTE_TIMEOUT_SECONDS", 0.01)

    def _slow_equity():
        time.sleep(0.05)
        return EquityView(
            equity_base=100.0,
            cash_base=25.0,
            realized_pnl_base_total=0.0,
            unrealized_pnl_base_total=0.0,
            drift_flag=False,
        )

    portfolio_context.portfolio.get_cached_equity.side_effect = _slow_equity

    started = time.perf_counter()
    response = client.get("/api/portfolio/summary")
    elapsed = time.perf_counter() - started

    assert response.status_code == 200
    payload = response.json()
    assert payload["data"] is None
    assert payload["error"] == "Portfolio summary timed out."
    assert elapsed < 0.04


def test_positions_shape_matches_payload(client, portfolio_context):
    price = 2.5
    position = SpotPosition(
        pair="BTC/USD",
        base_asset="BTC",
        quote_asset="USD",
        base_size=0.5,
        avg_entry_price=10000.0,
        realized_pnl_base=0.0,
        fees_paid_base=0.0,
        strategy_tag="alpha",
    )

    portfolio_context.market_data.get_latest_price.return_value = price
    # Mock metadata for dust check
    mock_meta = MagicMock()
    mock_meta.min_order_size = 0.0001
    mock_meta.volume_decimals = 8
    portfolio_context.market_data.get_pair_metadata.return_value = mock_meta

    position.current_value_base = price * position.base_size
    position.unrealized_pnl_base = (
        position.current_value_base - (position.base_size * position.avg_entry_price)
    )
    portfolio_context.portfolio.get_cached_positions.return_value = [position]

    response = client.get("/api/portfolio/positions")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data[0]["pair"] == "BTC/USD"
    assert data[0]["value_usd"] == pytest.approx(1.25)
    # Phase 3 PnL formula: (current_price - avg_entry_price) * base_size
    expected_unrealized = (price - position.avg_entry_price) * position.base_size
    assert data[0]["unrealized_pnl_usd"] == pytest.approx(expected_unrealized)
    assert data[0]["is_dust"] is False


def test_exposure_breakdown_enveloped(client, portfolio_context):
    portfolio_context.portfolio.get_cached_asset_exposure.return_value = [
        AssetExposure(
            asset="BTC", amount=1.0, value_base=100.0, percentage_of_equity=0.1
        )
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
