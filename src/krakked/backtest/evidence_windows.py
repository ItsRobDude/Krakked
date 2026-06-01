"""Shared evidence-window definitions and market-context helpers."""

from __future__ import annotations

import copy
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from statistics import mean, pstdev
from typing import Any, Iterable, Mapping, Sequence

from krakked.config import AppConfig
from krakked.market_regime import (
    MarketRegimeOverlayParams,
    _clean_pairs,
    classify_market_regime_snapshot,
)

from .runner import BacktestMarketData


@dataclass(frozen=True)
class EvidenceWindow:
    window_set: str
    window_id: str
    start: str
    end: str
    label: str = ""

    def as_tuple(self) -> tuple[str, str, str]:
        return self.window_id, self.start, self.end


RECENT_20D_WINDOWS: tuple[EvidenceWindow, ...] = (
    EvidenceWindow(
        "recent_20d",
        "20260321-20260410",
        "2026-03-21T00:00:00Z",
        "2026-04-10T00:00:00Z",
    ),
    EvidenceWindow(
        "recent_20d",
        "20260410-20260430",
        "2026-04-10T00:00:00Z",
        "2026-04-30T00:00:00Z",
    ),
    EvidenceWindow(
        "recent_20d",
        "20260430-20260520",
        "2026-04-30T00:00:00Z",
        "2026-05-20T00:00:00Z",
    ),
    EvidenceWindow(
        "recent_20d",
        "20260505-20260525",
        "2026-05-05T00:00:00Z",
        "2026-05-25T00:00:00Z",
    ),
    EvidenceWindow(
        "recent_20d",
        "20260510-20260530",
        "2026-05-10T00:00:00Z",
        "2026-05-30T00:00:00Z",
        label="current_rolling",
    ),
)

LONG_4H_WINDOWS: tuple[EvidenceWindow, ...] = (
    EvidenceWindow(
        "long_4h",
        "20251221-20260120",
        "2025-12-21T00:00:00Z",
        "2026-01-20T00:00:00Z",
    ),
    EvidenceWindow(
        "long_4h",
        "20260120-20260219",
        "2026-01-20T00:00:00Z",
        "2026-02-19T00:00:00Z",
    ),
    EvidenceWindow(
        "long_4h",
        "20260219-20260321",
        "2026-02-19T00:00:00Z",
        "2026-03-21T00:00:00Z",
    ),
    EvidenceWindow(
        "long_4h",
        "20260321-20260420",
        "2026-03-21T00:00:00Z",
        "2026-04-20T00:00:00Z",
    ),
    EvidenceWindow(
        "long_4h",
        "20260420-20260520",
        "2026-04-20T00:00:00Z",
        "2026-05-20T00:00:00Z",
    ),
    EvidenceWindow(
        "long_4h",
        "20260430-20260530",
        "2026-04-30T00:00:00Z",
        "2026-05-30T00:00:00Z",
        label="current_rolling",
    ),
)

# NOTE: This is a *candidate* set whose regime mix is not guaranteed by the name.
# It is the five `long_4h` windows plus the current rolling window (the whole
# cached Dec 2025 -> May 2026 span). The actual per-window regime is computed at
# report time via `_market_bucket` from benchmark/basket returns, and
# `summarize_regime_coverage` enforces real up/down/chop coverage so a report
# cannot claim diversity from this name alone. (As of the current cached OHLC it
# does resolve to a genuine mix -- ~2 uptrend / 1 downtrend / 2 chop windows by
# benchmark return -- but never assume that; always read the computed buckets.)
REGIME_DIVERSE_4H_WINDOWS: tuple[EvidenceWindow, ...] = (
    LONG_4H_WINDOWS[0],
    LONG_4H_WINDOWS[1],
    LONG_4H_WINDOWS[2],
    LONG_4H_WINDOWS[3],
    LONG_4H_WINDOWS[4],
    RECENT_20D_WINDOWS[-1],
)

# Buckets that must all be present among the evaluable (non-current) windows for
# an evidence set to count as genuinely regime-diverse.
REQUIRED_REGIME_BUCKETS: tuple[str, ...] = (
    "uptrend",
    "downtrend",
    "chop_or_transition",
)
# Buckets that do not count toward regime coverage: the current rolling window is
# in-flight, and insufficient-data windows carry no regime signal.
NON_EVALUABLE_REGIME_BUCKETS: frozenset[str] = frozenset(
    {"current_rolling", "insufficient_data"}
)


def summarize_regime_coverage(
    buckets: Iterable[Any],
) -> tuple[dict[str, int], bool]:
    """Count evidence buckets and decide whether regime coverage is sufficient.

    Coverage is sufficient only when the evaluable windows (excluding the current
    rolling window and insufficient-data windows) include every bucket in
    ``REQUIRED_REGIME_BUCKETS``. This lets a report fail honestly with
    ``insufficient_regime_coverage`` instead of trusting a window-set name like
    ``regime_diverse_4h`` that may not actually span multiple regimes.
    """
    counts = Counter(str(bucket) for bucket in buckets if bucket)
    evaluable = {
        bucket for bucket in counts if bucket not in NON_EVALUABLE_REGIME_BUCKETS
    }
    sufficient = all(bucket in evaluable for bucket in REQUIRED_REGIME_BUCKETS)
    return dict(sorted(counts.items())), sufficient


EVIDENCE_WINDOWS: Mapping[str, tuple[EvidenceWindow, ...]] = {
    "recent_20d": RECENT_20D_WINDOWS,
    "long_4h": LONG_4H_WINDOWS,
    "regime_diverse_4h": REGIME_DIVERSE_4H_WINDOWS,
}

EVIDENCE_WINDOW_SET_TUPLES: dict[str, list[tuple[str, str, str]]] = {
    window_set: [window.as_tuple() for window in windows]
    for window_set, windows in EVIDENCE_WINDOWS.items()
}


def evidence_window_tuples(
    window_sets: Sequence[str] | None = None,
) -> dict[str, list[tuple[str, str, str]]]:
    requested = list(window_sets or EVIDENCE_WINDOWS)
    return {
        window_set: list(EVIDENCE_WINDOW_SET_TUPLES[window_set])
        for window_set in requested
    }


def parse_evidence_datetime(value: str) -> datetime:
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def build_evidence_window_context(
    config: AppConfig,
    *,
    window_sets: Mapping[str, Sequence[tuple[str, str, str]]],
    pairs: Sequence[str] | None = None,
    timeframe: str = "4h",
    regime_params: MarketRegimeOverlayParams | None = None,
) -> dict[str, Any]:
    params = regime_params or MarketRegimeOverlayParams(timeframe=timeframe)
    selected_pairs = _clean_pairs(
        list(pairs if pairs is not None else config.universe.include_pairs)
    )
    if not selected_pairs:
        selected_pairs = ["BTC/USD", "ETH/USD", "SOL/USD", "ADA/USD"]
    if params.benchmark_pair not in selected_pairs:
        selected_pairs.insert(0, params.benchmark_pair)

    windows: list[dict[str, Any]] = []
    for window_set, rows in window_sets.items():
        for window_id, start_text, end_text in rows:
            windows.append(
                _single_window_context(
                    config,
                    window_set=window_set,
                    window_id=window_id,
                    start=parse_evidence_datetime(start_text),
                    end=parse_evidence_datetime(end_text),
                    pairs=selected_pairs,
                    timeframe=timeframe,
                    regime_params=params,
                )
            )

    bucket_counts = Counter(str(window["evidence_bucket"]) for window in windows)
    return {
        "timeframe": timeframe,
        "benchmark_pair": params.benchmark_pair,
        "pairs": selected_pairs,
        "windows": windows,
        "evidence_bucket_counts": dict(sorted(bucket_counts.items())),
    }


def context_by_window_key(
    payload: Mapping[str, Any] | None,
) -> dict[tuple[str, str], Mapping[str, Any]]:
    if not payload:
        return {}
    windows = payload.get("windows") if isinstance(payload, Mapping) else None
    if not isinstance(windows, Sequence):
        return {}
    result: dict[tuple[str, str], Mapping[str, Any]] = {}
    for window in windows:
        if not isinstance(window, Mapping):
            continue
        window_set = window.get("window_set")
        window_id = window.get("window_id")
        if window_set and window_id:
            result[(str(window_set), str(window_id))] = window
    return result


def _single_window_context(
    config: AppConfig,
    *,
    window_set: str,
    window_id: str,
    start: datetime,
    end: datetime,
    pairs: Sequence[str],
    timeframe: str,
    regime_params: MarketRegimeOverlayParams,
) -> dict[str, Any]:
    market_data = BacktestMarketData(config, pairs, [timeframe], start, end)
    try:
        preflight = market_data.get_preflight()
        timestamps = list(market_data.iter_timestamps())
        market_data.set_time(end)
        bars_by_pair = {
            pair: market_data.get_ohlc(pair, timeframe, lookback=1_000_000)
            for pair in pairs
        }
        pair_returns = {
            pair: _pair_return_pct(market_data, pair, timeframe, timestamps)
            for pair in pairs
        }
        usable_returns = [
            float(value) for value in pair_returns.values() if value is not None
        ]
        benchmark_return = pair_returns.get(regime_params.benchmark_pair)
        basket_return = mean(usable_returns) if usable_returns else None
        benchmark_curve = _pair_equity_curve(
            market_data,
            regime_params.benchmark_pair,
            timeframe,
            timestamps,
        )
        basket_curve = _basket_equity_curve(market_data, pairs, timeframe, timestamps)
        snapshots = [
            classify_market_regime_snapshot(
                bars_by_pair,
                timestamp=timestamp,
                params=regime_params,
            )
            for timestamp in timestamps
        ]
        state_counts = Counter(snapshot.regime for snapshot in snapshots)
        reason_counts: Counter[str] = Counter()
        for snapshot in snapshots:
            reason_counts.update(snapshot.reason_codes)
        market_bucket = _market_bucket(
            benchmark_return=benchmark_return,
            basket_return=basket_return,
            benchmark_max_drawdown_pct=_max_drawdown_pct(benchmark_curve),
        )
        evidence_bucket = (
            "current_rolling"
            if window_id in {"20260510-20260530", "20260430-20260530"}
            else market_bucket
        )
        return {
            "window_set": window_set,
            "window_id": window_id,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "timeframe": timeframe,
            "benchmark_pair": regime_params.benchmark_pair,
            "strict_data_ready": not (
                preflight.missing_series or preflight.partial_series
            ),
            "missing_series": list(preflight.missing_series),
            "partial_series": list(preflight.partial_series),
            "bar_count": len(timestamps),
            "benchmark_return_pct": benchmark_return,
            "basket_return_pct": basket_return,
            "benchmark_max_drawdown_pct": _max_drawdown_pct(benchmark_curve),
            "basket_max_drawdown_pct": _max_drawdown_pct(basket_curve),
            "basket_volatility_pct": _volatility_pct(basket_curve),
            "state_counts": dict(sorted(state_counts.items())),
            "reason_counts": dict(reason_counts.most_common()),
            "market_bucket": market_bucket,
            "evidence_bucket": evidence_bucket,
            "pair_returns_pct": pair_returns,
        }
    finally:
        shutdown = getattr(market_data, "shutdown", None)
        if callable(shutdown):
            shutdown()


def _pair_return_pct(
    market_data: BacktestMarketData,
    pair: str,
    timeframe: str,
    timestamps: Sequence[int],
) -> float | None:
    if not timestamps:
        return None
    start_bar = market_data.get_bar_at_or_after(pair, timeframe, timestamps[0])
    end_bar = market_data.get_bar_at_or_before(pair, timeframe, timestamps[-1])
    if (
        start_bar is None
        or end_bar is None
        or float(start_bar.close) <= 0.0
        or float(end_bar.close) <= 0.0
    ):
        return None
    return ((float(end_bar.close) - float(start_bar.close)) / float(start_bar.close)) * 100.0


def _pair_equity_curve(
    market_data: BacktestMarketData,
    pair: str,
    timeframe: str,
    timestamps: Sequence[int],
) -> list[float]:
    if not timestamps:
        return []
    start_bar = market_data.get_bar_at_or_after(pair, timeframe, timestamps[0])
    if start_bar is None or float(start_bar.close) <= 0.0:
        return []
    start_price = float(start_bar.close)
    curve: list[float] = []
    for timestamp in timestamps:
        bar = market_data.get_bar_at_or_before(pair, timeframe, timestamp)
        if bar is None or float(bar.close) <= 0.0:
            return []
        curve.append(float(bar.close) / start_price)
    return curve


def _basket_equity_curve(
    market_data: BacktestMarketData,
    pairs: Sequence[str],
    timeframe: str,
    timestamps: Sequence[int],
) -> list[float]:
    curves = [
        curve
        for pair in pairs
        if (curve := _pair_equity_curve(market_data, pair, timeframe, timestamps))
    ]
    if not curves:
        return []
    min_length = min(len(curve) for curve in curves)
    return [
        mean(curve[index] for curve in curves)
        for index in range(min_length)
    ]


def _max_drawdown_pct(equity_curve: Sequence[float]) -> float:
    peak = 0.0
    max_drawdown = 0.0
    for equity in equity_curve:
        value = float(equity)
        if value <= 0.0:
            continue
        peak = max(peak, value)
        if peak <= 0.0:
            continue
        drawdown = ((peak - value) / peak) * 100.0
        max_drawdown = max(max_drawdown, drawdown)
    return max_drawdown


def _volatility_pct(equity_curve: Sequence[float]) -> float:
    returns: list[float] = []
    for previous, current in zip(equity_curve, equity_curve[1:]):
        if previous > 0.0:
            returns.append((float(current) - float(previous)) / float(previous))
    return pstdev(returns) * 100.0 if len(returns) >= 2 else 0.0


def _market_bucket(
    *,
    benchmark_return: float | None,
    basket_return: float | None,
    benchmark_max_drawdown_pct: float,
) -> str:
    if benchmark_return is None or basket_return is None:
        return "insufficient_data"
    if benchmark_return >= 2.0 and basket_return >= 1.0:
        return "uptrend"
    if (
        benchmark_return <= -2.0
        and basket_return <= -1.0
    ) or benchmark_max_drawdown_pct >= 8.0:
        return "downtrend"
    return "chop_or_transition"


def clone_window_context(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    return copy.deepcopy(dict(payload or {}))
