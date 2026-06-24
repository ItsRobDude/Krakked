"""Funding/basis feasibility reporting for defensive risk research."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import UTC, datetime
from statistics import median
from typing import Any, Mapping, Sequence

from krakked.backtest.evidence_windows import (
    EVIDENCE_WINDOW_SET_TUPLES,
    parse_evidence_datetime,
)
from krakked.market_data.futures_public import KrakenFuturesPublicClient
from krakked.market_data.futures_symbols import instrument_candidates, select_candidate

DEFAULT_FUNDING_BASIS_PAIRS: tuple[str, ...] = (
    "BTC/USD",
    "ETH/USD",
    "SOL/USD",
    "ADA/USD",
)
DEFAULT_FUNDING_BASIS_TIMEFRAME = "4h"
FUNDING_POINT_IN_TIME_STATUS = "unknown_publish_lag"
FUNDING_POINT_IN_TIME_NOTE = (
    "Historical funding rates expose realized period timestamps, but this probe "
    "does not prove the first-observable publish time. Treat realized funding as "
    "forward-collection-only until a later PR proves or records publish timing."
)
BASIS_POINT_IN_TIME_STATUS = "completed_candle_safe"
BASIS_POINT_IN_TIME_NOTE = (
    "Historical mark/spot candles can be used only after the candle is complete; "
    "a later experiment must shift decisions to the next bar."
)

_TIMEFRAME_SECONDS: Mapping[str, int] = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}


@dataclass(frozen=True)
class FundingBasisFeasibilityResult:
    generated_at: datetime
    summary: dict[str, Any]
    pairs: list[dict[str, Any]]
    windows: list[dict[str, Any]]
    sources: dict[str, Any]

    def to_report_dict(self) -> dict[str, Any]:
        return {
            "report_version": 1,
            "report_type": "funding_basis_feasibility",
            "generated_at": self.generated_at.isoformat(),
            "summary": copy.deepcopy(self.summary),
            "pairs": copy.deepcopy(self.pairs),
            "windows": copy.deepcopy(self.windows),
            "sources": copy.deepcopy(self.sources),
        }


def run_funding_basis_feasibility(
    *,
    pairs: Sequence[str] | None = None,
    start: datetime,
    end: datetime,
    window_sets: Mapping[str, Sequence[tuple[str, str, str]]] | None = None,
    timeframe: str = DEFAULT_FUNDING_BASIS_TIMEFRAME,
    client: KrakenFuturesPublicClient | None = None,
    raw_cache_dir: str | None = None,
) -> FundingBasisFeasibilityResult:
    selected_pairs = _clean_pairs(pairs or DEFAULT_FUNDING_BASIS_PAIRS)
    selected_window_sets = window_sets or {
        "regime_diverse_4h": EVIDENCE_WINDOW_SET_TUPLES["regime_diverse_4h"]
    }
    start = _as_utc(start)
    end = _as_utc(end)
    if end <= start:
        raise ValueError("--end must be after --start")
    interval_seconds = _timeframe_seconds(timeframe)

    futures_client = client or KrakenFuturesPublicClient(raw_cache_dir=raw_cache_dir)
    instruments_payload = futures_client.get_instruments()
    tickers_payload = futures_client.get_tickers()
    instruments = _as_list(instruments_payload.get("instruments"))
    tickers = _as_list(tickers_payload.get("tickers"))
    tickers_by_symbol = {
        str(ticker.get("symbol") or "").upper(): dict(ticker)
        for ticker in tickers
        if isinstance(ticker, Mapping)
    }
    windows = _window_rows(selected_window_sets)

    pair_reports: list[dict[str, Any]] = []
    for pair in selected_pairs:
        pair_reports.append(
            _pair_report(
                pair=pair,
                instruments=instruments,
                tickers_by_symbol=tickers_by_symbol,
                client=futures_client,
                start=start,
                end=end,
                timeframe=timeframe,
                interval_seconds=interval_seconds,
                windows=windows,
            )
        )

    aggregate_windows = _aggregate_windows(windows, pair_reports)
    verdict = _verdict(pair_reports, aggregate_windows)
    summary = {
        "research_only": True,
        "runtime_config_changed": False,
        "pairs": selected_pairs,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "window_sets": list(selected_window_sets),
        "timeframe": timeframe,
        "verdict": verdict["verdict"],
        "verdict_reason": verdict["reason"],
        "next_step": verdict["next_step"],
        "selected_pair_count": len(pair_reports),
        "pair_count_with_selected_symbol": sum(
            1 for report in pair_reports if report.get("selected_symbol")
        ),
        "all_pairs_have_selected_symbol": all(
            bool(report.get("selected_symbol")) for report in pair_reports
        ),
        "all_required_coverage_complete": all(
            bool(report.get("required_coverage_complete")) for report in pair_reports
        ),
        "all_required_point_in_time_safe": all(
            bool(report.get("required_point_in_time_safe")) for report in pair_reports
        ),
        "window_count": len(aggregate_windows),
        "covered_window_count": sum(
            1 for window in aggregate_windows if window["coverage_status"] == "complete"
        ),
        "point_in_time_gate": {
            "realized_funding_status": FUNDING_POINT_IN_TIME_STATUS,
            "basis_status": BASIS_POINT_IN_TIME_STATUS,
            "complete_coverage_unknown_publish_timing_blocks_history": True,
        },
        "independent_episode_note": (
            "Episode counts are conservative stress-run proxies; row counts are not "
            "independent observations."
        ),
    }
    sources = {
        "kraken_futures_public_only": True,
        "endpoints": [
            "/derivatives/api/v3/instruments",
            "/derivatives/api/v3/tickers",
            "/derivatives/api/v3/historical-funding-rates",
            "/api/charts/v1/{tick_type}/{symbol}/{interval}",
        ],
        "instruments_server_time": _jsonable(instruments_payload.get("serverTime")),
        "tickers_server_time": _jsonable(tickers_payload.get("serverTime")),
        "raw_cache_used": raw_cache_dir is not None,
    }
    return FundingBasisFeasibilityResult(
        generated_at=datetime.now(UTC),
        summary=summary,
        pairs=pair_reports,
        windows=aggregate_windows,
        sources=sources,
    )


def _pair_report(
    *,
    pair: str,
    instruments: Sequence[Mapping[str, Any]],
    tickers_by_symbol: Mapping[str, Mapping[str, Any]],
    client: KrakenFuturesPublicClient,
    start: datetime,
    end: datetime,
    timeframe: str,
    interval_seconds: int,
    windows: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    candidates = instrument_candidates(pair, instruments, tickers_by_symbol)
    selected = select_candidate(candidates)
    if selected is None:
        return {
            "pair": pair,
            "selected_symbol": None,
            "candidate_symbols": [candidate["symbol"] for candidate in candidates],
            "candidate_count": len(candidates),
            "required_coverage_complete": False,
            "required_point_in_time_safe": False,
            "verdict": "not_viable_from_kraken_alone",
            "reason": "no_tradeable_perpetual_symbol_found",
            "funding": None,
            "basis": None,
            "coverage_by_window": [],
        }

    symbol = str(selected["symbol"])
    funding_payload = client.get_historical_funding_rates(symbol)
    funding_rows = _funding_rows(funding_payload)
    funding_cadence = _observed_cadence_seconds(
        [row["timestamp"] for row in funding_rows]
    )
    funding_coverage = _series_coverage(
        name="realized_funding",
        timestamps=[row["timestamp"] for row in funding_rows],
        start=start,
        end=end,
        expected_interval_seconds=funding_cadence,
    )
    candle_reports: dict[str, dict[str, Any]] = {}
    for tick_type in ("mark", "spot", "trade"):
        candles_payload = client.get_candles(
            tick_type=tick_type,
            symbol=symbol,
            interval=timeframe,
            start=int(start.timestamp()),
            end=int(end.timestamp()),
        )
        candle_rows = _candle_rows(candles_payload)
        candle_reports[tick_type] = {
            "coverage": _series_coverage(
                name=f"{tick_type}_candles",
                timestamps=[row["timestamp"] for row in candle_rows],
                start=start,
                end=end,
                expected_interval_seconds=interval_seconds,
            ),
            "row_count": len(candle_rows),
            "rows": candle_rows,
        }

    mark_spot_basis = _basis_values(
        candle_reports["mark"]["rows"], candle_reports["spot"]["rows"]
    )
    funding_values = [
        float(row["relative_funding_rate"])
        for row in funding_rows
        if row.get("relative_funding_rate") is not None
    ]
    coverage_by_window = []
    for window in windows:
        funding_window = _series_coverage(
            name="realized_funding",
            timestamps=[row["timestamp"] for row in funding_rows],
            start=window["start_dt"],
            end=window["end_dt"],
            expected_interval_seconds=funding_cadence,
        )
        mark_window = _series_coverage(
            name="mark_candles",
            timestamps=[row["timestamp"] for row in candle_reports["mark"]["rows"]],
            start=window["start_dt"],
            end=window["end_dt"],
            expected_interval_seconds=interval_seconds,
        )
        spot_window = _series_coverage(
            name="spot_candles",
            timestamps=[row["timestamp"] for row in candle_reports["spot"]["rows"]],
            start=window["start_dt"],
            end=window["end_dt"],
            expected_interval_seconds=interval_seconds,
        )
        complete = all(
            item["coverage_status"] == "complete"
            for item in (funding_window, mark_window, spot_window)
        )
        coverage_by_window.append(
            {
                "window_set": window["window_set"],
                "window_id": window["window_id"],
                "start": window["start"],
                "end": window["end"],
                "coverage_status": "complete" if complete else "partial",
                "realized_funding": funding_window,
                "mark_candles": mark_window,
                "spot_candles": spot_window,
            }
        )

    funding = {
        "coverage": funding_coverage,
        "row_count": len(funding_rows),
        "observed_cadence_seconds": funding_cadence,
        "first_timestamp": funding_coverage["first_timestamp"],
        "last_timestamp": funding_coverage["last_timestamp"],
        "timestamp_semantics": {
            "series": "realized_funding",
            "timestamp_field": "timestamp",
            "value_fields": ["fundingRate", "relativeFundingRate"],
            "point_in_time_status": FUNDING_POINT_IN_TIME_STATUS,
            "note": FUNDING_POINT_IN_TIME_NOTE,
        },
        "current_or_predicted_ticker_fields": {
            "fundingRate": selected.get("ticker", {}).get("fundingRate"),
            "fundingRatePrediction": selected.get("ticker", {}).get(
                "fundingRatePrediction"
            ),
            "historical_backtestable": False,
        },
        "stress_episode_proxy": _stress_episode_proxy(funding_values),
    }
    basis = {
        "mark_vs_spot": {
            "available": bool(mark_spot_basis),
            "coverage": _combine_coverages(
                [
                    candle_reports["mark"]["coverage"],
                    candle_reports["spot"]["coverage"],
                ]
            ),
            "point_in_time_status": BASIS_POINT_IN_TIME_STATUS,
            "note": BASIS_POINT_IN_TIME_NOTE,
            "cross_feed_caveat": (
                "Kraken Futures mark and spot chart candles are joined by candle "
                "timestamp; later experiments must trade next-bar only."
            ),
            "stress_episode_proxy": _stress_episode_proxy(mark_spot_basis),
        },
        "mark_vs_index": {
            "available": False,
            "historical_backtestable": False,
            "reason": (
                "No historical index/reference candle endpoint is used by this thin "
                "probe; ticker indexPrice is current metadata only."
            ),
            "current_ticker_index_price": selected.get("ticker", {}).get("indexPrice"),
        },
        "candles": {
            tick_type: {
                "row_count": report["row_count"],
                "coverage": report["coverage"],
            }
            for tick_type, report in candle_reports.items()
        },
    }
    required_coverage_complete = (
        funding_coverage["coverage_status"] == "complete"
        and basis["mark_vs_spot"]["coverage"]["coverage_status"] == "complete"
        and all(
            window["coverage_status"] == "complete" for window in coverage_by_window
        )
    )
    required_point_in_time_safe = (
        funding["timestamp_semantics"]["point_in_time_status"] == "proven"
        and basis["mark_vs_spot"]["point_in_time_status"] == BASIS_POINT_IN_TIME_STATUS
    )
    return {
        "pair": pair,
        "selected_symbol": symbol,
        "candidate_symbols": [candidate["symbol"] for candidate in candidates],
        "candidate_count": len(candidates),
        "selected_contract_family": selected["contract_family"],
        "selected_contract_type": selected.get("type"),
        "base": selected.get("base"),
        "quote": selected.get("quote"),
        "tradeable": selected.get("tradeable"),
        "suspended": selected.get("suspended"),
        "funding_fields_available": bool(
            selected.get("fundingRate") is not None
            or selected.get("fundingRatePrediction") is not None
        ),
        "candidates": candidates,
        "funding": funding,
        "basis": basis,
        "coverage_by_window": coverage_by_window,
        "required_coverage_complete": required_coverage_complete,
        "required_point_in_time_safe": required_point_in_time_safe,
        "verdict": (
            "historical_backtestable"
            if required_coverage_complete and required_point_in_time_safe
            else "forward_collection_only"
        ),
        "reason": (
            "point_in_time_ready"
            if required_coverage_complete and required_point_in_time_safe
            else (
                "coverage_complete_but_realized_funding_publish_timing_unknown"
                if required_coverage_complete
                else "current_symbol_available_but_historical_coverage_incomplete"
            )
        ),
    }


def _series_coverage(
    *,
    name: str,
    timestamps: Sequence[datetime],
    start: datetime,
    end: datetime,
    expected_interval_seconds: int | None,
) -> dict[str, Any]:
    rows = sorted(_as_utc(timestamp) for timestamp in timestamps)
    in_window = [timestamp for timestamp in rows if start <= timestamp < end]
    first = in_window[0] if in_window else None
    last = in_window[-1] if in_window else None
    expected = _expected_count(start, end, expected_interval_seconds)
    gaps = _gap_rows(in_window, expected_interval_seconds)
    coverage_note = None
    if not in_window:
        status = "missing"
    elif expected_interval_seconds is None:
        status = "partial"
        coverage_note = "expected_interval_unknown"
    elif expected is not None and len(in_window) < expected:
        status = "partial"
    elif gaps:
        status = "partial"
    else:
        status = "complete"
    return {
        "series": name,
        "coverage_status": status,
        "row_count": len(in_window),
        "expected_row_count": expected,
        "first_timestamp": first.isoformat() if first else None,
        "last_timestamp": last.isoformat() if last else None,
        "expected_interval_seconds": expected_interval_seconds,
        "gap_count": len(gaps),
        "gaps": gaps[:10],
        "coverage_note": coverage_note,
    }


def _combine_coverages(coverages: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    statuses = [str(coverage.get("coverage_status")) for coverage in coverages]
    if all(status == "complete" for status in statuses):
        status = "complete"
    elif any(status != "missing" for status in statuses):
        status = "partial"
    else:
        status = "missing"
    return {
        "coverage_status": status,
        "component_statuses": {
            str(coverage.get("series")): coverage.get("coverage_status")
            for coverage in coverages
        },
    }


def _aggregate_windows(
    windows: Sequence[dict[str, Any]], pair_reports: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for window in windows:
        pair_statuses: dict[str, str] = {}
        for report in pair_reports:
            match = next(
                (
                    row
                    for row in (report.get("coverage_by_window") or [])
                    if row.get("window_set") == window["window_set"]
                    and row.get("window_id") == window["window_id"]
                ),
                None,
            )
            pair_statuses[str(report.get("pair"))] = (
                str(match.get("coverage_status")) if match else "missing"
            )
        complete = bool(pair_statuses) and all(
            status == "complete" for status in pair_statuses.values()
        )
        rows.append(
            {
                "window_set": window["window_set"],
                "window_id": window["window_id"],
                "start": window["start"],
                "end": window["end"],
                "coverage_status": "complete" if complete else "partial",
                "pair_statuses": pair_statuses,
            }
        )
    return rows


def _verdict(
    pair_reports: Sequence[Mapping[str, Any]],
    aggregate_windows: Sequence[Mapping[str, Any]],
) -> dict[str, str]:
    if not pair_reports or any(
        not report.get("selected_symbol") for report in pair_reports
    ):
        return {
            "verdict": "not_viable_from_kraken_alone",
            "reason": "one_or_more_pairs_have_no_public_perpetual_symbol",
            "next_step": "stop_kraken_only_lane_or_evaluate_another_provider",
        }
    coverage_complete = all(
        bool(report.get("required_coverage_complete")) for report in pair_reports
    ) and all(
        window.get("coverage_status") == "complete" for window in aggregate_windows
    )
    if not coverage_complete:
        return {
            "verdict": "forward_collection_only",
            "reason": "current_public_symbols_available_but_historical_coverage_incomplete",
            "next_step": "build_forward_collector_or_seek_gap_fill_before_backtest",
        }
    point_in_time_safe = all(
        bool(report.get("required_point_in_time_safe")) for report in pair_reports
    )
    if not point_in_time_safe:
        return {
            "verdict": "forward_collection_only",
            "reason": "complete_coverage_but_publish_timing_is_not_proven",
            "next_step": "build_forward_collector_or_prove_publish_timing_before_backtest",
        }
    return {
        "verdict": "historical_backtestable",
        "reason": "coverage_and_point_in_time_semantics_are_ready",
        "next_step": "build_raw_storage_backfill_then_baseline_controlled_defensive_experiment",
    }


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


def _basis_values(
    mark_rows: Sequence[Mapping[str, Any]],
    spot_rows: Sequence[Mapping[str, Any]],
) -> list[float]:
    spot_by_ts = {
        _as_utc(row["timestamp"]): _float_or_none(row.get("close"))
        for row in spot_rows
        if row.get("timestamp") is not None
    }
    values: list[float] = []
    for row in mark_rows:
        timestamp = row.get("timestamp")
        if timestamp is None:
            continue
        mark = _float_or_none(row.get("close"))
        spot = spot_by_ts.get(_as_utc(timestamp))
        if mark is None or spot is None or spot == 0:
            continue
        values.append(((mark - spot) / spot) * 100.0)
    return values


def _stress_episode_proxy(values: Sequence[float]) -> dict[str, Any]:
    clean = [abs(float(value)) for value in values if value is not None]
    if len(clean) < 10:
        return {
            "sample_count": len(clean),
            "threshold_abs": None,
            "episode_count": 0,
            "note": "insufficient_samples_for_episode_proxy",
        }
    sorted_values = sorted(clean)
    index = min(len(sorted_values) - 1, int(len(sorted_values) * 0.9))
    threshold = sorted_values[index]
    in_episode = False
    episodes = 0
    for value in clean:
        stressed = value >= threshold and threshold > 0
        if stressed and not in_episode:
            episodes += 1
            in_episode = True
        elif not stressed:
            in_episode = False
    return {
        "sample_count": len(clean),
        "threshold_abs": threshold,
        "episode_count": episodes,
        "note": "contiguous runs above the 90th percentile absolute value",
    }


def _window_rows(
    window_sets: Mapping[str, Sequence[tuple[str, str, str]]]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for window_set, windows in window_sets.items():
        for window_id, start, end in windows:
            start_dt = parse_evidence_datetime(start)
            end_dt = parse_evidence_datetime(end)
            rows.append(
                {
                    "window_set": window_set,
                    "window_id": window_id,
                    "start": start_dt.isoformat(),
                    "end": end_dt.isoformat(),
                    "start_dt": start_dt,
                    "end_dt": end_dt,
                }
            )
    return rows


def _observed_cadence_seconds(timestamps: Sequence[datetime]) -> int | None:
    rows = sorted(_as_utc(timestamp) for timestamp in timestamps)
    if len(rows) < 2:
        return None
    diffs = [
        int((after - before).total_seconds())
        for before, after in zip(rows, rows[1:])
        if after > before
    ]
    if not diffs:
        return None
    return int(median(diffs))


def _expected_count(
    start: datetime, end: datetime, expected_interval_seconds: int | None
) -> int | None:
    if not expected_interval_seconds or expected_interval_seconds <= 0:
        return None
    duration = int((end - start).total_seconds())
    if duration <= 0:
        return 0
    return duration // expected_interval_seconds


def _gap_rows(
    timestamps: Sequence[datetime], expected_interval_seconds: int | None
) -> list[dict[str, Any]]:
    if not expected_interval_seconds or len(timestamps) < 2:
        return []
    rows: list[dict[str, Any]] = []
    max_expected_gap = expected_interval_seconds * 1.5
    for before, after in zip(timestamps, timestamps[1:]):
        gap = int((after - before).total_seconds())
        if gap > max_expected_gap:
            rows.append(
                {
                    "from": before.isoformat(),
                    "to": after.isoformat(),
                    "gap_seconds": gap,
                }
            )
    return rows


def _timeframe_seconds(timeframe: str) -> int:
    key = str(timeframe).strip().lower()
    if key not in _TIMEFRAME_SECONDS:
        raise ValueError(
            f"Unsupported futures candle timeframe {timeframe!r}; supported: "
            + ", ".join(sorted(_TIMEFRAME_SECONDS))
        )
    return _TIMEFRAME_SECONDS[key]


def _clean_pairs(pairs: Sequence[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for pair in pairs:
        value = str(pair or "").strip().upper()
        if not value or value in seen:
            continue
        seen.add(value)
        cleaned.append(value)
    if not cleaned:
        raise ValueError("At least one pair is required")
    return cleaned


def _as_list(value: Any) -> list[Mapping[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, Mapping)]
    return []


def _parse_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _as_utc(value)
    if isinstance(value, (int, float)):
        numeric = float(value)
        if numeric > 10_000_000_000:
            numeric /= 1000.0
        return datetime.fromtimestamp(numeric, tz=UTC)
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return _as_utc(parsed)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return _as_utc(value).isoformat()
    return value
