from unittest.mock import Mock

from kraken_bot.portfolio.manager import PortfolioService


def _build_service(store, portfolio, api_client):
    service = PortfolioService.__new__(PortfolioService)
    service.store = store
    service.portfolio = portfolio
    service.rest_client = api_client
    service._bootstrapped = True
    service._last_sync_ok = True
    service._reconcile = Mock()
    return service


def test_sync_ingests_before_saving():
    store = Mock()
    portfolio = Mock()
    api_client = Mock()

    store.get_trades.return_value = []
    store.get_cash_flows.return_value = []
    portfolio._normalize_trade_payload.side_effect = lambda t: t

    api_client.get_private.side_effect = [
        {
            "trades": {
                "T1": {
                    "time": 1,
                    "pair": "BTC/USD",
                    "type": "buy",
                    "price": 10,
                    "cost": 10,
                    "fee": 0,
                    "vol": 1,
                }
            },
            "last": None,
        },
        {"trades": {}},
    ]
    api_client.get_ledgers.return_value = {"ledger": {}}

    service = _build_service(store, portfolio, api_client)

    result = service.sync()

    portfolio.ingest_trades.assert_called_once()
    store.save_trades.assert_called_once()
    assert result["new_trades"] == 1
    assert service.last_sync_ok is True


def test_sync_does_not_save_when_ingest_fails():
    store = Mock()
    portfolio = Mock()
    api_client = Mock()

    store.get_trades.return_value = []
    store.get_cash_flows.return_value = []
    portfolio._normalize_trade_payload.side_effect = lambda t: t
    portfolio.ingest_trades.side_effect = RuntimeError("boom")

    api_client.get_private.return_value = {
        "trades": {
            "T1": {
                "time": 1,
                "pair": "BTC/USD",
                "type": "buy",
                "price": 10,
                "cost": 10,
                "fee": 0,
                "vol": 1,
            }
        },
        "last": None,
    }
    api_client.get_ledgers.return_value = {"ledger": {}}

    service = _build_service(store, portfolio, api_client)

    service.sync()

    store.save_trades.assert_not_called()
    assert service.last_sync_ok is False


def test_sync_does_not_persist_cash_flows_on_failure():
    store = Mock()
    portfolio = Mock()
    api_client = Mock()

    store.get_trades.return_value = []
    store.get_cash_flows.return_value = []
    portfolio._normalize_trade_payload.side_effect = lambda t: t
    portfolio.ingest_cashflows.side_effect = RuntimeError("failed")

    api_client.get_private.return_value = {"trades": {}, "last": None}
    api_client.get_ledgers.return_value = {
        "ledger": {
            "L1": {
                "time": 2,
                "asset": "USD",
                "amount": 5,
                "type": "deposit",
            }
        }
    }

    service = _build_service(store, portfolio, api_client)

    service.sync()

    store.save_cash_flows.assert_not_called()
    assert service.last_sync_ok is False
