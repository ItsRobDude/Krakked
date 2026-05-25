from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence

ML_FEATURE_SCHEMA_VERSION = "ohlc_v2"
ML_FEATURE_NAMES = (
    "pct_change",
    "trend_diff",
    "volatility",
    "return_3",
    "return_6",
    "close_vs_short_ma",
    "close_vs_long_ma",
    "range_pct",
    "body_pct",
    "upper_wick_pct",
    "lower_wick_pct",
    "volume_zscore",
)


@dataclass(frozen=True)
class MLFeatureVector:
    values: List[float]
    names: List[str]
    schema_version: str

    def to_metadata(self) -> dict[str, float | str]:
        metadata: dict[str, float | str] = {
            name: value for name, value in zip(self.names, self.values)
        }
        metadata["feature_schema_version"] = self.schema_version
        return metadata


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _std(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = _mean(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    return math.sqrt(variance)


def _window_return(values: Sequence[float], periods: int) -> float:
    if len(values) <= periods:
        return 0.0
    base = values[-(periods + 1)]
    if base <= 0:
        return 0.0
    return (values[-1] - base) / base


def _safe_ratio(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator > 0 else 0.0


def _feature_vector(
    *,
    closes: Sequence[float],
    short_window: int,
    long_window: int,
    lookback_bars: int,
    latest_open: float,
    latest_high: float,
    latest_low: float,
    latest_volume: float,
    volumes: Sequence[float],
) -> Optional[MLFeatureVector]:
    if not closes or len(closes) < 3:
        return None

    last_close, prev_close = closes[-1], closes[-2]
    if prev_close <= 0:
        return None

    pct_change = (last_close - prev_close) / prev_close

    short_len = min(short_window, len(closes))
    long_len = min(long_window, len(closes))
    short_ma = _mean(closes[-short_len:]) if short_len > 0 else 0.0
    long_ma = _mean(closes[-long_len:]) if long_len > 0 else 0.0
    trend_diff = ((short_ma - long_ma) / long_ma) if long_ma > 0 else 0.0

    window = closes[-lookback_bars:]
    mean_close = _mean(window)
    volatility = 0.0
    if mean_close > 0 and len(window) > 1:
        volatility = _std(window) / mean_close

    range_pct = _safe_ratio(max(latest_high - latest_low, 0.0), last_close)
    body_pct = _safe_ratio(abs(last_close - latest_open), last_close)
    upper_wick_pct = _safe_ratio(
        max(latest_high - max(latest_open, last_close), 0.0),
        last_close,
    )
    lower_wick_pct = _safe_ratio(
        max(min(latest_open, last_close) - latest_low, 0.0),
        last_close,
    )

    volume_window = list(volumes[-lookback_bars:])
    volume_zscore = 0.0
    if len(volume_window) > 1:
        volume_std = _std(volume_window)
        if volume_std > 0:
            volume_zscore = (latest_volume - _mean(volume_window)) / volume_std

    return MLFeatureVector(
        values=[
            pct_change,
            trend_diff,
            volatility,
            _window_return(closes, 3),
            _window_return(closes, 6),
            _safe_ratio(last_close - short_ma, short_ma),
            _safe_ratio(last_close - long_ma, long_ma),
            range_pct,
            body_pct,
            upper_wick_pct,
            lower_wick_pct,
            volume_zscore,
        ],
        names=list(ML_FEATURE_NAMES),
        schema_version=ML_FEATURE_SCHEMA_VERSION,
    )


def compute_feature_vector_from_closes(
    closes: Sequence[float], short_window: int, long_window: int, lookback_bars: int
) -> Optional[MLFeatureVector]:
    """
    Compute shared ML features from close prices.

    Close-only callers get the same schema as OHLC callers; fields that need
    full candle or volume data are set to zero.
    """
    if not closes:
        return None
    return _feature_vector(
        closes=closes,
        short_window=short_window,
        long_window=long_window,
        lookback_bars=lookback_bars,
        latest_open=float(closes[-1]),
        latest_high=float(closes[-1]),
        latest_low=float(closes[-1]),
        latest_volume=0.0,
        volumes=[0.0 for _ in closes],
    )


def compute_feature_vector_from_ohlc(
    ohlc_window: Sequence[object],
    short_window: int,
    long_window: int,
    lookback_bars: int,
) -> Optional[MLFeatureVector]:
    closes = [float(getattr(bar, "close")) for bar in ohlc_window]
    if not closes:
        return None
    latest = ohlc_window[-1]
    return _feature_vector(
        closes=closes,
        short_window=short_window,
        long_window=long_window,
        lookback_bars=lookback_bars,
        latest_open=float(getattr(latest, "open", closes[-1])),
        latest_high=float(getattr(latest, "high", closes[-1])),
        latest_low=float(getattr(latest, "low", closes[-1])),
        latest_volume=float(getattr(latest, "volume", 0.0)),
        volumes=[float(getattr(bar, "volume", 0.0)) for bar in ohlc_window],
    )


def compute_features_from_window(
    closes: List[float], short_window: int, long_window: int, lookback_bars: int
) -> Optional[List[float]]:
    vector = compute_feature_vector_from_closes(
        closes,
        short_window,
        long_window,
        lookback_bars,
    )
    return list(vector.values) if vector is not None else None


__all__ = [
    "ML_FEATURE_NAMES",
    "ML_FEATURE_SCHEMA_VERSION",
    "MLFeatureVector",
    "compute_feature_vector_from_closes",
    "compute_feature_vector_from_ohlc",
    "compute_features_from_window",
]
