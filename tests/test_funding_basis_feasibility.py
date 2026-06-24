from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta
from typing import Any

from krakked.backtest.funding_basis_feasibility import (
    _verdict,
    run_funding_basis_feasibility,
)
from krakked.market_data.futures_public import KrakenFuturesPublicClient


class _FakeFuturesClient:
    def __init__(
        self,
        *,
        instruments: list[dict[str, Any]],
        tickers: list[dict[str, Any]],
        funding: dict[str, list[dict[str, Any]]],
        candles: dict[tuple[str, str], list[dict[str, Any]]],
    ) -> None:
        self.instruments = instruments
        self.tickers = tickers
        self.funding = funding
        self.candles = candles
        self.calls: list[tuple[str, Any]] = []

    def get_instruments(self) -> dict[str, Any]:
        self.calls.append(("instruments", None))
        return {
            "result": "success",
            "serverTime": "2026-06-24T00:00:00Z",
            "instruments": self.instruments,
        }

    def get_tickers(self) -> dict[str, Any]:
        self.calls.append(("tickers", None))
        return {
            "result": "success",
            "serverTime": "2026-06-24T00:00:00Z",
            "tickers": self.tickers,
        }

    def get_historical_funding_rates(self, symbol: str) -> dict[str, Any]:
        self.calls.append(("funding", symbol))
        return {"result": "success", "rates": self.funding.get(symbol, [])}

    def get_candles(
        self,
        *,
        tick_type: str,
        symbol: str,
        interval: str,
        start: int,
        end: int,
        count: int = 5000,
    ) -> dict[str, Any]:
        self.calls.append(("candles", (tick_type, symbol, interval, start, end, count)))
        return {
            "result": "success",
            "candles": self.candles.get((tick_type, symbol), []),
        }


def _iso_rows(
    start: datetime,
    end: datetime,
    *,
    step: timedelta,
    kind: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    current = start
    index = 0
    while current < end:
        if kind == "funding":
            rows.append(
                {
                    "timestamp": current.isoformat().replace("+00:00", "Z"),
                    "fundingRate": 0.01 * ((index % 3) - 1),
                    "relativeFundingRate": 0.0001 * ((index % 3) - 1),
                }
            )
        else:
            rows.append(
                {
                    "time": int(current.timestamp() * 1000),
                    "open": "100.0",
                    "high": "101.0",
                    "low": "99.0",
                    "close": str(100.0 + (index * 0.1)),
                }
            )
        current += step
        index += 1
    return rows


def _client_with_complete_btc() -> _FakeFuturesClient:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = datetime(2026, 1, 3, tzinfo=UTC)
    symbol = "PF_XBTUSD"
    return _FakeFuturesClient(
        instruments=[
            {
                "symbol": symbol,
                "type": "futures_linear",
                "base": "BTC",
                "quote": "USD",
                "pair": "BTC:USD",
                "tradeable": True,
            }
        ],
        tickers=[
            {
                "symbol": symbol,
                "pair": "XBT:USD",
                "tag": "perpetual",
                "markPrice": "100.0",
                "indexPrice": "99.9",
                "fundingRate": "0.01",
                "fundingRatePrediction": "0.02",
                "suspended": False,
            }
        ],
        funding={
            symbol: _iso_rows(start, end, step=timedelta(hours=1), kind="funding")
        },
        candles={
            ("mark", symbol): _iso_rows(
                start, end, step=timedelta(hours=4), kind="candle"
            ),
            ("spot", symbol): _iso_rows(
                start, end, step=timedelta(hours=4), kind="candle"
            ),
            ("trade", symbol): _iso_rows(
                start, end, step=timedelta(hours=4), kind="candle"
            ),
        },
    )


def test_complete_coverage_with_unknown_publish_timing_is_forward_only() -> None:
    client = _client_with_complete_btc()

    result = run_funding_basis_feasibility(
        pairs=["BTC/USD"],
        start=datetime(2026, 1, 1, tzinfo=UTC),
        end=datetime(2026, 1, 3, tzinfo=UTC),
        window_sets={"tiny": [("w1", "2026-01-01T00:00:00Z", "2026-01-03T00:00:00Z")]},
        client=client,  # type: ignore[arg-type]
    )

    payload = result.to_report_dict()
    summary = payload["summary"]
    pair = payload["pairs"][0]
    assert summary["verdict"] == "forward_collection_only"
    assert summary["all_required_coverage_complete"] is True
    assert summary["all_required_point_in_time_safe"] is False
    assert pair["selected_symbol"] == "PF_XBTUSD"
    assert pair["selected_contract_family"] == "PF"
    assert pair["funding"]["timestamp_semantics"]["point_in_time_status"] == (
        "unknown_publish_lag"
    )
    assert pair["basis"]["mark_vs_spot"]["coverage"]["coverage_status"] == "complete"
    assert pair["basis"]["mark_vs_index"]["historical_backtestable"] is False


def test_missing_futures_symbol_is_not_viable() -> None:
    client = _FakeFuturesClient(
        instruments=[],
        tickers=[],
        funding={},
        candles={},
    )

    result = run_funding_basis_feasibility(
        pairs=["DOGE/USD"],
        start=datetime(2026, 1, 1, tzinfo=UTC),
        end=datetime(2026, 1, 2, tzinfo=UTC),
        window_sets={"tiny": [("w1", "2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z")]},
        client=client,  # type: ignore[arg-type]
    )

    payload = result.to_report_dict()
    assert payload["summary"]["verdict"] == "not_viable_from_kraken_alone"
    assert payload["pairs"][0]["reason"] == "no_tradeable_perpetual_symbol_found"


def test_symbol_mapping_does_not_match_longer_base_prefix() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = datetime(2026, 1, 2, tzinfo=UTC)
    symbol = "PF_ETHUSD"
    client = _FakeFuturesClient(
        instruments=[
            {
                "symbol": "PF_ETHFIUSD",
                "type": "futures_linear",
                "base": "ETHFI",
                "quote": "USD",
                "pair": "ETHFI:USD",
                "tradeable": True,
            },
            {
                "symbol": symbol,
                "type": "futures_linear",
                "base": "ETH",
                "quote": "USD",
                "pair": "ETH:USD",
                "tradeable": True,
            },
        ],
        tickers=[
            {"symbol": "PF_ETHFIUSD", "suspended": False},
            {
                "symbol": symbol,
                "fundingRate": "0.01",
                "fundingRatePrediction": "0.02",
                "suspended": False,
            },
        ],
        funding={
            symbol: _iso_rows(start, end, step=timedelta(hours=1), kind="funding")
        },
        candles={
            ("mark", symbol): _iso_rows(
                start, end, step=timedelta(hours=4), kind="candle"
            ),
            ("spot", symbol): _iso_rows(
                start, end, step=timedelta(hours=4), kind="candle"
            ),
            ("trade", symbol): _iso_rows(
                start, end, step=timedelta(hours=4), kind="candle"
            ),
        },
    )

    result = run_funding_basis_feasibility(
        pairs=["ETH/USD"],
        start=start,
        end=end,
        window_sets={"tiny": [("w1", "2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z")]},
        client=client,  # type: ignore[arg-type]
    )

    assert result.to_report_dict()["pairs"][0]["selected_symbol"] == "PF_ETHUSD"


def test_chart_gap_blocks_historical_viability() -> None:
    client = _client_with_complete_btc()
    symbol = "PF_XBTUSD"
    client.candles[("spot", symbol)] = client.candles[("spot", symbol)][:-1]

    result = run_funding_basis_feasibility(
        pairs=["BTC/USD"],
        start=datetime(2026, 1, 1, tzinfo=UTC),
        end=datetime(2026, 1, 3, tzinfo=UTC),
        window_sets={"tiny": [("w1", "2026-01-01T00:00:00Z", "2026-01-03T00:00:00Z")]},
        client=client,  # type: ignore[arg-type]
    )

    payload = result.to_report_dict()
    assert payload["summary"]["verdict"] == "forward_collection_only"
    assert payload["summary"]["verdict_reason"] == (
        "current_public_symbols_available_but_historical_coverage_incomplete"
    )
    assert payload["pairs"][0]["required_coverage_complete"] is False
    assert payload["pairs"][0]["reason"] == (
        "current_symbol_available_but_historical_coverage_incomplete"
    )


def test_unknown_funding_cadence_blocks_historical_viability_but_allows_forward_collection() -> (
    None
):
    client = _client_with_complete_btc()
    symbol = "PF_XBTUSD"
    client.funding[symbol] = client.funding[symbol][:1]

    result = run_funding_basis_feasibility(
        pairs=["BTC/USD"],
        start=datetime(2026, 1, 1, tzinfo=UTC),
        end=datetime(2026, 1, 3, tzinfo=UTC),
        window_sets={"tiny": [("w1", "2026-01-01T00:00:00Z", "2026-01-03T00:00:00Z")]},
        client=client,  # type: ignore[arg-type]
    )

    payload = result.to_report_dict()
    pair = payload["pairs"][0]
    assert payload["summary"]["verdict"] == "forward_collection_only"
    assert pair["funding"]["coverage"]["coverage_status"] == "partial"
    assert pair["funding"]["coverage"]["coverage_note"] == "expected_interval_unknown"
    assert pair["required_coverage_complete"] is False


def test_reproducible_report_fields_are_deterministic_with_canned_inputs() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = datetime(2026, 1, 3, tzinfo=UTC)
    window_sets = {"tiny": [("w1", "2026-01-01T00:00:00Z", "2026-01-03T00:00:00Z")]}

    first = run_funding_basis_feasibility(
        pairs=["BTC/USD"],
        start=start,
        end=end,
        window_sets=window_sets,
        client=_client_with_complete_btc(),  # type: ignore[arg-type]
    ).to_report_dict()
    second = run_funding_basis_feasibility(
        pairs=["BTC/USD"],
        start=start,
        end=end,
        window_sets=window_sets,
        client=_client_with_complete_btc(),  # type: ignore[arg-type]
    ).to_report_dict()
    first.pop("generated_at")
    second.pop("generated_at")

    assert first == second


def test_futures_public_client_has_no_private_or_signing_surface() -> None:
    params = inspect.signature(KrakenFuturesPublicClient).parameters

    assert "api_key" not in params
    assert "api_secret" not in params
    assert not hasattr(KrakenFuturesPublicClient, "get_private")
    assert not hasattr(KrakenFuturesPublicClient, "add_order")


def test_verdict_branches_to_historical_when_coverage_and_timing_are_ready() -> None:
    verdict = _verdict(
        [
            {
                "selected_symbol": "PF_XBTUSD",
                "required_coverage_complete": True,
                "required_point_in_time_safe": True,
            }
        ],
        [{"coverage_status": "complete"}],
    )

    assert verdict["verdict"] == "historical_backtestable"
    assert verdict["next_step"].startswith("build_raw_storage_backfill")


def test_verdict_branches_to_forward_collection_when_symbol_exists_but_history_is_incomplete() -> (
    None
):
    verdict = _verdict(
        [
            {
                "selected_symbol": "PF_XBTUSD",
                "required_coverage_complete": False,
                "required_point_in_time_safe": False,
            }
        ],
        [{"coverage_status": "partial"}],
    )

    assert verdict["verdict"] == "forward_collection_only"
    assert verdict["reason"] == (
        "current_public_symbols_available_but_historical_coverage_incomplete"
    )
