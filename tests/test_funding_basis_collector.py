from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from krakked.backtest.funding_basis_collector import run_funding_basis_collection


class _FakeFuturesClient:
    def __init__(self) -> None:
        self.funding_call_count = 0

    def get_instruments(self) -> dict[str, Any]:
        return {
            "result": "success",
            "serverTime": "2026-06-24T00:00:00Z",
            "instruments": [
                {
                    "symbol": "PF_XBTUSD",
                    "type": "futures_linear",
                    "base": "BTC",
                    "quote": "USD",
                    "pair": "BTC:USD",
                    "tradeable": True,
                }
            ],
        }

    def get_tickers(self) -> dict[str, Any]:
        return {
            "result": "success",
            "serverTime": "2026-06-24T00:00:00Z",
            "tickers": [
                {
                    "symbol": "PF_XBTUSD",
                    "pair": "XBT:USD",
                    "tag": "perpetual",
                    "markPrice": "101.0",
                    "indexPrice": "100.5",
                    "fundingRate": "0.010",
                    "fundingRatePrediction": "0.011",
                    "suspended": False,
                }
            ],
        }

    def get_historical_funding_rates(self, symbol: str) -> dict[str, Any]:
        self.funding_call_count += 1
        rates = [
            {
                "timestamp": "2026-06-24T00:00:00Z",
                "fundingRate": "0.005",
                "relativeFundingRate": "0.00005",
            }
        ]
        if self.funding_call_count >= 2:
            rates.append(
                {
                    "timestamp": "2026-06-24T01:00:00Z",
                    "fundingRate": "0.010",
                    "relativeFundingRate": "0.00010",
                }
            )
        return {"result": "success", "rates": rates, "symbol": symbol}

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
        del symbol, interval, start, end, count
        close = {"mark": "101.0", "spot": "100.0", "trade": "100.8"}[tick_type]
        return {
            "result": "success",
            "candles": [
                {
                    "time": int(datetime(2026, 6, 24, tzinfo=UTC).timestamp() * 1000),
                    "open": close,
                    "high": close,
                    "low": close,
                    "close": close,
                }
            ],
        }


def test_collector_preserves_repeated_observations_and_proves_publish_lag(
    tmp_path: Path,
) -> None:
    client = _FakeFuturesClient()
    db_path = tmp_path / "funding_basis.db"

    first_now = datetime(2026, 6, 24, 0, 50, tzinfo=UTC)
    first = run_funding_basis_collection(
        pairs=["BTC/USD"],
        db_path=db_path,
        lookback_hours=2,
        client=client,  # type: ignore[arg-type]
        clock=lambda: first_now,
    )

    second_now = datetime(2026, 6, 24, 1, 5, tzinfo=UTC)
    second = run_funding_basis_collection(
        pairs=["BTC/USD"],
        db_path=db_path,
        lookback_hours=2,
        client=client,  # type: ignore[arg-type]
        clock=lambda: second_now,
    )

    assert first.summary["status"] == "collecting_insufficient_history"
    assert second.publish_lag["status"] == "publish_lag_provable"
    assert second.publish_lag["lag_seconds"]["min"] == 300.0
    assert second.prediction_accuracy["sample_count"] == 1
    assert second.prediction_accuracy["mean_absolute_error"] == pytest.approx(0.001)

    with sqlite3.connect(db_path) as conn:
        repeated = conn.execute(
            """
            SELECT COUNT(DISTINCT fetched_at)
            FROM funding_observations
            WHERE symbol = 'PF_XBTUSD' AND period_ts = '2026-06-24T00:00:00+00:00'
            """
        ).fetchone()[0]
        raw_fetches = conn.execute(
            "SELECT COUNT(*) FROM raw_public_fetches"
        ).fetchone()[0]
        basis = conn.execute(
            "SELECT mark_spot_basis_pct FROM basis_observations LIMIT 1"
        ).fetchone()[0]

    assert repeated == 2
    assert raw_fetches == 12
    assert basis == 1.0


def test_collector_reports_missing_symbol_as_unhealthy(tmp_path: Path) -> None:
    class _NoSymbolClient(_FakeFuturesClient):
        def get_instruments(self) -> dict[str, Any]:
            return {"result": "success", "instruments": []}

        def get_tickers(self) -> dict[str, Any]:
            return {"result": "success", "tickers": []}

    result = run_funding_basis_collection(
        pairs=["DOGE/USD"],
        db_path=tmp_path / "funding_basis.db",
        client=_NoSymbolClient(),  # type: ignore[arg-type]
        clock=lambda: datetime(2026, 6, 24, 0, 0, tzinfo=UTC),
    )

    assert result.summary["status"] == "collector_unhealthy"
    assert result.pairs[0]["status"] == "no_public_perpetual_symbol"


def test_collector_rejects_non_positive_lookback(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="--lookback-hours must be positive"):
        run_funding_basis_collection(
            pairs=["BTC/USD"],
            db_path=tmp_path / "funding_basis.db",
            lookback_hours=0,
            client=_FakeFuturesClient(),  # type: ignore[arg-type]
            clock=lambda: datetime(2026, 6, 24, 0, 0, tzinfo=UTC),
        )
