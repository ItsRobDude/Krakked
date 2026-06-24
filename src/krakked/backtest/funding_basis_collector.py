"""Forward collection for funding/basis point-in-time research."""

from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Any, Mapping, Sequence

from krakked.backtest.funding_basis_feasibility import (
    DEFAULT_FUNDING_BASIS_PAIRS,
    DEFAULT_FUNDING_BASIS_TIMEFRAME,
    _as_list,
    _as_utc,
    _clean_pairs,
    _float_or_none,
    _parse_timestamp,
    _timeframe_seconds,
)
from krakked.market_data.futures_public import KrakenFuturesPublicClient
from krakked.market_data.futures_symbols import instrument_candidates, select_candidate

DEFAULT_FUNDING_BASIS_COLLECTION_DB = "data/research/funding_basis_observations.db"
DEFAULT_FUNDING_BASIS_LOOKBACK_HOURS = 48.0
RECOMMENDED_COLLECTION_INTERVAL_MINUTES = 15
MINIMUM_USEFUL_COLLECTION_INTERVAL_MINUTES = 60


@dataclass(frozen=True)
class FundingBasisCollectionResult:
    generated_at: datetime
    summary: dict[str, Any]
    pairs: list[dict[str, Any]]
    publish_lag: dict[str, Any]
    prediction_accuracy: dict[str, Any]
    storage: dict[str, Any]
    sources: dict[str, Any]

    def to_report_dict(self) -> dict[str, Any]:
        return {
            "report_version": 1,
            "report_type": "funding_basis_collection",
            "generated_at": self.generated_at.isoformat(),
            "summary": copy.deepcopy(self.summary),
            "pairs": copy.deepcopy(self.pairs),
            "publish_lag": copy.deepcopy(self.publish_lag),
            "prediction_accuracy": copy.deepcopy(self.prediction_accuracy),
            "storage": copy.deepcopy(self.storage),
            "sources": copy.deepcopy(self.sources),
        }


def run_funding_basis_collection(
    *,
    pairs: Sequence[str] | None = None,
    db_path: str | Path = DEFAULT_FUNDING_BASIS_COLLECTION_DB,
    lookback_hours: float = DEFAULT_FUNDING_BASIS_LOOKBACK_HOURS,
    timeframe: str = DEFAULT_FUNDING_BASIS_TIMEFRAME,
    client: KrakenFuturesPublicClient | None = None,
    clock: Callable[[], datetime] | None = None,
) -> FundingBasisCollectionResult:
    selected_pairs = _clean_pairs(pairs or DEFAULT_FUNDING_BASIS_PAIRS)
    if lookback_hours <= 0:
        raise ValueError("--lookback-hours must be positive")
    interval_seconds = _timeframe_seconds(timeframe)
    now_fn = clock or (lambda: datetime.now(UTC))
    started_at = _as_utc(now_fn())
    tail_start = started_at - timedelta(hours=float(lookback_hours))
    resolved_db_path = Path(db_path).expanduser().resolve()
    futures_client = client or KrakenFuturesPublicClient()
    batch_id = uuid.uuid4().hex

    resolved_db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(resolved_db_path.as_posix()) as conn:
        _ensure_schema(conn)
        _insert_batch(
            conn,
            batch_id=batch_id,
            started_at=started_at,
            pairs=selected_pairs,
            timeframe=timeframe,
            lookback_hours=float(lookback_hours),
        )

        instruments_payload, instruments_fetch_id = _fetch_and_store(
            conn,
            batch_id=batch_id,
            fetched_at=_as_utc(now_fn()),
            endpoint="/derivatives/api/v3/instruments",
            params={},
            fetch=lambda: futures_client.get_instruments(),
        )
        tickers_payload, tickers_fetch_id = _fetch_and_store(
            conn,
            batch_id=batch_id,
            fetched_at=_as_utc(now_fn()),
            endpoint="/derivatives/api/v3/tickers",
            params={},
            fetch=lambda: futures_client.get_tickers(),
        )
        instruments = _as_list(instruments_payload.get("instruments"))
        tickers = _as_list(tickers_payload.get("tickers"))
        tickers_by_symbol = {
            str(ticker.get("symbol") or "").upper(): dict(ticker)
            for ticker in tickers
            if isinstance(ticker, Mapping)
        }

        pair_reports: list[dict[str, Any]] = []
        for pair in selected_pairs:
            pair_reports.append(
                _collect_pair(
                    conn,
                    client=futures_client,
                    batch_id=batch_id,
                    pair=pair,
                    instruments=instruments,
                    tickers_by_symbol=tickers_by_symbol,
                    tickers_fetch_id=tickers_fetch_id,
                    fetched_at=_as_utc(now_fn()),
                    tail_start=tail_start,
                    tail_end=started_at,
                    timeframe=timeframe,
                    interval_seconds=interval_seconds,
                )
            )

        completed_at = _as_utc(now_fn())
        conn.execute(
            "UPDATE fetch_batches SET completed_at = ? WHERE batch_id = ?",
            (completed_at.isoformat(), batch_id),
        )
        conn.commit()

        publish_lag = _publish_lag_report(conn)
        prediction_accuracy = _prediction_accuracy_report(conn)

    status = _collection_status(pair_reports, publish_lag, prediction_accuracy)
    summary = {
        "research_only": True,
        "runtime_config_changed": False,
        "status": status,
        "pairs": selected_pairs,
        "selected_pair_count": len(pair_reports),
        "pair_count_with_selected_symbol": sum(
            1 for report in pair_reports if report.get("selected_symbol")
        ),
        "timeframe": timeframe,
        "lookback_hours": float(lookback_hours),
        "batch_id": batch_id,
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "publish_lag_status": publish_lag["status"],
        "prediction_accuracy_status": prediction_accuracy["status"],
        "recommended_collection_interval_minutes": RECOMMENDED_COLLECTION_INTERVAL_MINUTES,
        "minimum_useful_collection_interval_minutes": MINIMUM_USEFUL_COLLECTION_INTERVAL_MINUTES,
    }
    storage = {
        "db_path": str(resolved_db_path),
        "append_only": True,
        "portfolio_schema_changed": False,
        "batch_id": batch_id,
    }
    sources = {
        "kraken_futures_public_only": True,
        "raw_fetch_ids": {
            "instruments": instruments_fetch_id,
            "tickers": tickers_fetch_id,
        },
        "endpoints": [
            "/derivatives/api/v3/instruments",
            "/derivatives/api/v3/tickers",
            "/derivatives/api/v3/historical-funding-rates",
            "/api/charts/v1/{tick_type}/{symbol}/{interval}",
        ],
        "historical_funding_endpoint_bounded": False,
        "historical_funding_filtering": "local_tail_filter",
    }
    return FundingBasisCollectionResult(
        generated_at=completed_at,
        summary=summary,
        pairs=pair_reports,
        publish_lag=publish_lag,
        prediction_accuracy=prediction_accuracy,
        storage=storage,
        sources=sources,
    )


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS fetch_batches (
            batch_id TEXT PRIMARY KEY,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            pairs_json TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            lookback_hours REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS raw_public_fetches (
            raw_fetch_id TEXT PRIMARY KEY,
            batch_id TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            endpoint TEXT NOT NULL,
            params_hash TEXT NOT NULL,
            params_json TEXT NOT NULL,
            server_time TEXT,
            payload_hash TEXT NOT NULL,
            payload_json TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS funding_observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id TEXT NOT NULL,
            raw_fetch_id TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            pair TEXT NOT NULL,
            symbol TEXT NOT NULL,
            contract_family TEXT,
            period_ts TEXT NOT NULL,
            funding_rate REAL,
            relative_funding_rate REAL
        );

        CREATE TABLE IF NOT EXISTS prediction_observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id TEXT NOT NULL,
            raw_fetch_id TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            pair TEXT NOT NULL,
            symbol TEXT NOT NULL,
            contract_family TEXT,
            predicted_period_ts TEXT NOT NULL,
            funding_rate_prediction REAL,
            current_funding_rate REAL,
            mark_price REAL,
            index_price REAL
        );

        CREATE TABLE IF NOT EXISTS basis_observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id TEXT NOT NULL,
            raw_fetch_id TEXT,
            fetched_at TEXT NOT NULL,
            pair TEXT NOT NULL,
            symbol TEXT NOT NULL,
            contract_family TEXT,
            candle_ts TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            mark_close REAL,
            spot_close REAL,
            trade_close REAL,
            mark_spot_basis_pct REAL
        );

        CREATE INDEX IF NOT EXISTS idx_funding_symbol_period_fetch
            ON funding_observations(symbol, period_ts, fetched_at);
        CREATE INDEX IF NOT EXISTS idx_prediction_symbol_period_fetch
            ON prediction_observations(symbol, predicted_period_ts, fetched_at);
        CREATE INDEX IF NOT EXISTS idx_basis_symbol_candle_fetch
            ON basis_observations(symbol, candle_ts, fetched_at);
        CREATE INDEX IF NOT EXISTS idx_raw_endpoint_fetch
            ON raw_public_fetches(endpoint, fetched_at);
        """
    )


def _insert_batch(
    conn: sqlite3.Connection,
    *,
    batch_id: str,
    started_at: datetime,
    pairs: Sequence[str],
    timeframe: str,
    lookback_hours: float,
) -> None:
    conn.execute(
        """
        INSERT INTO fetch_batches (
            batch_id, started_at, completed_at, pairs_json, timeframe, lookback_hours
        ) VALUES (?, ?, NULL, ?, ?, ?)
        """,
        (
            batch_id,
            started_at.isoformat(),
            json.dumps(list(pairs), sort_keys=True),
            timeframe,
            lookback_hours,
        ),
    )


def _fetch_and_store(
    conn: sqlite3.Connection,
    *,
    batch_id: str,
    fetched_at: datetime,
    endpoint: str,
    params: Mapping[str, Any],
    fetch: Callable[[], dict[str, Any]],
) -> tuple[dict[str, Any], str]:
    payload = fetch()
    payload_json = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), default=str
    )
    params_json = json.dumps(dict(params), sort_keys=True, separators=(",", ":"))
    params_hash = _sha256(params_json)
    payload_hash = _sha256(payload_json)
    raw_fetch_id = _sha256(
        json.dumps(
            {
                "batch_id": batch_id,
                "fetched_at": fetched_at.isoformat(),
                "endpoint": endpoint,
                "params_hash": params_hash,
                "payload_hash": payload_hash,
            },
            sort_keys=True,
        )
    )
    conn.execute(
        """
        INSERT INTO raw_public_fetches (
            raw_fetch_id, batch_id, fetched_at, endpoint, params_hash, params_json,
            server_time, payload_hash, payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            raw_fetch_id,
            batch_id,
            fetched_at.isoformat(),
            endpoint,
            params_hash,
            params_json,
            _jsonable(payload.get("serverTime")),
            payload_hash,
            json.dumps(payload, sort_keys=True, default=str),
        ),
    )
    return payload, raw_fetch_id


def _collect_pair(
    conn: sqlite3.Connection,
    *,
    client: KrakenFuturesPublicClient,
    batch_id: str,
    pair: str,
    instruments: Sequence[Mapping[str, Any]],
    tickers_by_symbol: Mapping[str, Mapping[str, Any]],
    tickers_fetch_id: str,
    fetched_at: datetime,
    tail_start: datetime,
    tail_end: datetime,
    timeframe: str,
    interval_seconds: int,
) -> dict[str, Any]:
    candidates = instrument_candidates(pair, instruments, tickers_by_symbol)
    selected = select_candidate(candidates)
    if selected is None:
        return {
            "pair": pair,
            "selected_symbol": None,
            "candidate_symbols": [candidate["symbol"] for candidate in candidates],
            "status": "no_public_perpetual_symbol",
            "funding_observation_count": 0,
            "prediction_observation_count": 0,
            "basis_observation_count": 0,
        }

    symbol = str(selected["symbol"])
    family = str(selected.get("contract_family") or "")
    raw_ticker = selected.get("ticker")
    ticker: Mapping[str, Any] = (
        dict(raw_ticker) if isinstance(raw_ticker, Mapping) else {}
    )
    predicted_period_ts = _next_hour_boundary(fetched_at)
    prediction_count = _insert_prediction(
        conn,
        batch_id=batch_id,
        raw_fetch_id=tickers_fetch_id,
        fetched_at=fetched_at,
        pair=pair,
        symbol=symbol,
        contract_family=family,
        predicted_period_ts=predicted_period_ts,
        ticker=ticker,
    )

    funding_payload, funding_fetch_id = _fetch_and_store(
        conn,
        batch_id=batch_id,
        fetched_at=fetched_at,
        endpoint="/derivatives/api/v3/historical-funding-rates",
        params={"symbol": symbol},
        fetch=lambda: client.get_historical_funding_rates(symbol),
    )
    funding_rows = _funding_rows(funding_payload)
    funding_count = _insert_funding_rows(
        conn,
        batch_id=batch_id,
        raw_fetch_id=funding_fetch_id,
        fetched_at=fetched_at,
        pair=pair,
        symbol=symbol,
        contract_family=family,
        rows=[
            row
            for row in funding_rows
            if tail_start <= _as_utc(row["timestamp"]) <= tail_end
        ],
    )

    candle_rows_by_type: dict[str, list[dict[str, Any]]] = {}
    candle_fetch_ids: dict[str, str] = {}
    for tick_type in ("mark", "spot", "trade"):

        def fetch_candles(tick_type: str = tick_type) -> dict[str, Any]:
            return client.get_candles(
                tick_type=tick_type,
                symbol=symbol,
                interval=timeframe,
                start=int(tail_start.timestamp()),
                end=int(tail_end.timestamp()),
            )

        payload, raw_fetch_id = _fetch_and_store(
            conn,
            batch_id=batch_id,
            fetched_at=fetched_at,
            endpoint=f"/api/charts/v1/{tick_type}/{symbol}/{timeframe}",
            params={
                "from": int(tail_start.timestamp()),
                "to": int(tail_end.timestamp()),
                "count": 5000,
            },
            fetch=fetch_candles,
        )
        candle_rows_by_type[tick_type] = _candle_rows(payload)
        candle_fetch_ids[tick_type] = raw_fetch_id

    basis_count = _insert_basis_rows(
        conn,
        batch_id=batch_id,
        raw_fetch_id=candle_fetch_ids.get("mark"),
        fetched_at=fetched_at,
        pair=pair,
        symbol=symbol,
        contract_family=family,
        timeframe=timeframe,
        interval_seconds=interval_seconds,
        rows_by_type=candle_rows_by_type,
    )
    return {
        "pair": pair,
        "selected_symbol": symbol,
        "candidate_symbols": [candidate["symbol"] for candidate in candidates],
        "selected_contract_family": family,
        "status": "collected",
        "funding_observation_count": funding_count,
        "prediction_observation_count": prediction_count,
        "basis_observation_count": basis_count,
        "predicted_period_ts": predicted_period_ts.isoformat(),
    }


def _insert_prediction(
    conn: sqlite3.Connection,
    *,
    batch_id: str,
    raw_fetch_id: str,
    fetched_at: datetime,
    pair: str,
    symbol: str,
    contract_family: str,
    predicted_period_ts: datetime,
    ticker: Mapping[str, Any],
) -> int:
    conn.execute(
        """
        INSERT INTO prediction_observations (
            batch_id, raw_fetch_id, fetched_at, pair, symbol, contract_family,
            predicted_period_ts, funding_rate_prediction, current_funding_rate,
            mark_price, index_price
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            batch_id,
            raw_fetch_id,
            fetched_at.isoformat(),
            pair,
            symbol,
            contract_family,
            predicted_period_ts.isoformat(),
            _float_or_none(ticker.get("fundingRatePrediction")),
            _float_or_none(ticker.get("fundingRate")),
            _float_or_none(ticker.get("markPrice")),
            _float_or_none(ticker.get("indexPrice")),
        ),
    )
    return 1


def _insert_funding_rows(
    conn: sqlite3.Connection,
    *,
    batch_id: str,
    raw_fetch_id: str,
    fetched_at: datetime,
    pair: str,
    symbol: str,
    contract_family: str,
    rows: Sequence[Mapping[str, Any]],
) -> int:
    inserted = 0
    for row in rows:
        timestamp = row.get("timestamp")
        if not isinstance(timestamp, datetime):
            continue
        conn.execute(
            """
            INSERT INTO funding_observations (
                batch_id, raw_fetch_id, fetched_at, pair, symbol, contract_family,
                period_ts, funding_rate, relative_funding_rate
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                batch_id,
                raw_fetch_id,
                fetched_at.isoformat(),
                pair,
                symbol,
                contract_family,
                _as_utc(timestamp).isoformat(),
                _float_or_none(row.get("funding_rate")),
                _float_or_none(row.get("relative_funding_rate")),
            ),
        )
        inserted += 1
    return inserted


def _insert_basis_rows(
    conn: sqlite3.Connection,
    *,
    batch_id: str,
    raw_fetch_id: str | None,
    fetched_at: datetime,
    pair: str,
    symbol: str,
    contract_family: str,
    timeframe: str,
    interval_seconds: int,
    rows_by_type: Mapping[str, Sequence[Mapping[str, Any]]],
) -> int:
    del interval_seconds
    values_by_type: dict[str, dict[datetime, float | None]] = {}
    all_timestamps: set[datetime] = set()
    for tick_type, rows in rows_by_type.items():
        values: dict[datetime, float | None] = {}
        for row in rows:
            timestamp = row.get("timestamp")
            if not isinstance(timestamp, datetime):
                continue
            ts = _as_utc(timestamp)
            values[ts] = _float_or_none(row.get("close"))
            all_timestamps.add(ts)
        values_by_type[tick_type] = values

    inserted = 0
    for ts in sorted(all_timestamps):
        mark = values_by_type.get("mark", {}).get(ts)
        spot = values_by_type.get("spot", {}).get(ts)
        trade = values_by_type.get("trade", {}).get(ts)
        basis = ((mark - spot) / spot) * 100.0 if mark is not None and spot else None
        conn.execute(
            """
            INSERT INTO basis_observations (
                batch_id, raw_fetch_id, fetched_at, pair, symbol, contract_family,
                candle_ts, timeframe, mark_close, spot_close, trade_close,
                mark_spot_basis_pct
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                batch_id,
                raw_fetch_id,
                fetched_at.isoformat(),
                pair,
                symbol,
                contract_family,
                ts.isoformat(),
                timeframe,
                mark,
                spot,
                trade,
                basis,
            ),
        )
        inserted += 1
    return inserted


def _publish_lag_report(conn: sqlite3.Connection) -> dict[str, Any]:
    period_rows = conn.execute(
        """
        SELECT symbol, period_ts, MIN(fetched_at) AS first_observed_at, COUNT(*) AS observations
        FROM funding_observations
        GROUP BY symbol, period_ts
        ORDER BY symbol, period_ts
        """
    ).fetchall()
    fetch_rows = conn.execute(
        """
        SELECT fetched_at, params_json
        FROM raw_public_fetches
        WHERE endpoint = '/derivatives/api/v3/historical-funding-rates'
        ORDER BY fetched_at
        """
    ).fetchall()
    fetches_by_symbol: dict[str, list[datetime]] = {}
    for fetched_at_text, params_json in fetch_rows:
        params = json.loads(str(params_json or "{}"))
        symbol = str(params.get("symbol") or "")
        if not symbol:
            continue
        fetches_by_symbol.setdefault(symbol, []).append(
            _parse_stored_dt(fetched_at_text)
        )

    lag_seconds: list[float] = []
    unbounded_periods = 0
    pre_boundary_observed = 0
    for symbol, period_ts_text, first_observed_text, _observations in period_rows:
        period_ts = _parse_stored_dt(period_ts_text)
        first_observed = _parse_stored_dt(first_observed_text)
        prior_fetches = [
            fetched_at
            for fetched_at in fetches_by_symbol.get(str(symbol), [])
            if fetched_at < period_ts
        ]
        if first_observed < period_ts:
            pre_boundary_observed += 1
            continue
        if not prior_fetches:
            unbounded_periods += 1
            continue
        lag_seconds.append((first_observed - period_ts).total_seconds())

    status = (
        "publish_lag_provable" if lag_seconds else "collecting_insufficient_history"
    )
    return {
        "status": status,
        "observed_period_count": len(period_rows),
        "provable_period_count": len(lag_seconds),
        "unbounded_initial_backfill_period_count": unbounded_periods,
        "pre_boundary_observed_period_count": pre_boundary_observed,
        "lag_seconds": _distribution(lag_seconds),
        "note": (
            "Lag is counted only when the collector had at least one funding fetch "
            "before the funding period boundary and later observed the realized row."
        ),
    }


def _prediction_accuracy_report(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute(
        """
        WITH latest_prediction AS (
            SELECT p.symbol, p.predicted_period_ts, MAX(p.fetched_at) AS fetched_at
            FROM prediction_observations p
            WHERE p.funding_rate_prediction IS NOT NULL
              AND p.fetched_at < p.predicted_period_ts
            GROUP BY p.symbol, p.predicted_period_ts
        ),
        realized AS (
            SELECT symbol, period_ts, AVG(funding_rate) AS realized_funding_rate
            FROM funding_observations
            WHERE funding_rate IS NOT NULL
            GROUP BY symbol, period_ts
        )
        SELECT p.symbol, p.predicted_period_ts, p.fetched_at,
               p.funding_rate_prediction, r.realized_funding_rate
        FROM latest_prediction lp
        JOIN prediction_observations p
          ON p.symbol = lp.symbol
         AND p.predicted_period_ts = lp.predicted_period_ts
         AND p.fetched_at = lp.fetched_at
        JOIN realized r
          ON r.symbol = p.symbol
         AND r.period_ts = p.predicted_period_ts
        ORDER BY p.symbol, p.predicted_period_ts
        """
    ).fetchall()
    errors: list[float] = []
    sign_matches = 0
    for _symbol, _period_ts, _fetched_at, prediction, realized in rows:
        predicted_value = float(prediction)
        realized_value = float(realized)
        errors.append(predicted_value - realized_value)
        if _sign(predicted_value) == _sign(realized_value):
            sign_matches += 1
    abs_errors = [abs(value) for value in errors]
    sample_count = len(errors)
    status = (
        "prediction_signal_viable_for_experiment"
        if sample_count >= 24
        else "collecting_insufficient_history"
    )
    return {
        "status": status,
        "sample_count": sample_count,
        "mean_absolute_error": _mean(abs_errors),
        "bias": _mean(errors),
        "sign_agreement": (sign_matches / sample_count) if sample_count else None,
        "note": (
            "Prediction accuracy uses the latest prediction observed before the "
            "predicted funding period boundary."
        ),
    }


def _collection_status(
    pair_reports: Sequence[Mapping[str, Any]],
    publish_lag: Mapping[str, Any],
    prediction_accuracy: Mapping[str, Any],
) -> str:
    if not pair_reports or any(
        not report.get("selected_symbol") for report in pair_reports
    ):
        return "collector_unhealthy"
    if publish_lag.get("status") == "publish_lag_provable":
        return "publish_lag_provable"
    if prediction_accuracy.get("status") == "prediction_signal_viable_for_experiment":
        return "prediction_signal_viable_for_experiment"
    return "collecting_insufficient_history"


def _funding_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in _as_list(payload.get("rates")):
        timestamp = _parse_timestamp(item.get("timestamp"))
        if timestamp is None:
            continue
        rows.append(
            {
                "timestamp": timestamp,
                "funding_rate": _float_or_none(item.get("fundingRate")),
                "relative_funding_rate": _float_or_none(
                    item.get("relativeFundingRate")
                ),
            }
        )
    return rows


def _candle_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in _as_list(payload.get("candles")):
        timestamp = _parse_timestamp(item.get("time"))
        close = _float_or_none(item.get("close"))
        if timestamp is None:
            continue
        rows.append({"timestamp": timestamp, "close": close})
    return rows


def _next_hour_boundary(value: datetime) -> datetime:
    current = _as_utc(value).replace(minute=0, second=0, microsecond=0)
    if current == _as_utc(value):
        return current + timedelta(hours=1)
    return current + timedelta(hours=1)


def _parse_stored_dt(value: Any) -> datetime:
    parsed = _parse_timestamp(value)
    if parsed is None:
        raise ValueError(f"Invalid stored timestamp: {value!r}")
    return parsed


def _distribution(values: Sequence[float]) -> dict[str, float | int | None]:
    if not values:
        return {
            "count": 0,
            "min": None,
            "median": None,
            "p95": None,
            "max": None,
        }
    sorted_values = sorted(float(value) for value in values)
    p95_index = min(len(sorted_values) - 1, int((len(sorted_values) - 1) * 0.95))
    return {
        "count": len(sorted_values),
        "min": sorted_values[0],
        "median": median(sorted_values),
        "p95": sorted_values[p95_index],
        "max": sorted_values[-1],
    }


def _mean(values: Sequence[float]) -> float | None:
    return (sum(values) / len(values)) if values else None


def _sign(value: float) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _jsonable(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _as_utc(value).isoformat()
    return str(value)


__all__ = [
    "DEFAULT_FUNDING_BASIS_COLLECTION_DB",
    "FundingBasisCollectionResult",
    "run_funding_basis_collection",
]
