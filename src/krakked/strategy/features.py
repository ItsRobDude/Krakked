from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo
from typing import List, Optional, Sequence

FEATURE_TIME_TZ: tzinfo = UTC
ML_FEATURE_SCHEMA_VERSION = "ohlc_v3"
ML_FEATURE_NAMES = (
    "pct_change",
    "trend_diff",
    "volatility",
    "return_atr_1",
    "return_atr_3",
    "range_atr",
    "body_atr",
    "upper_wick_atr",
    "lower_wick_atr",
    "return_zscore",
    "volatility_ratio",
    "volume_change",
    "volume_log_ratio",
    "hour_sin",
    "hour_cos",
    "weekday_sin",
    "weekday_cos",
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


def _safe_log_ratio(numerator: float, denominator: float) -> float:
    if numerator <= 0 or denominator <= 0:
        return 0.0
    return math.log(numerator / denominator)


def _returns(closes: Sequence[float]) -> list[float]:
    values: list[float] = []
    for previous, current in zip(closes, closes[1:]):
        values.append((current - previous) / previous if previous > 0 else 0.0)
    return values


def _true_ranges(
    *,
    closes: Sequence[float],
    highs: Sequence[float],
    lows: Sequence[float],
) -> list[float]:
    ranges: list[float] = []
    for index, (high, low) in enumerate(zip(highs, lows)):
        base_range = max(high - low, 0.0)
        if index == 0:
            ranges.append(base_range)
            continue
        previous_close = closes[index - 1]
        ranges.append(
            max(
                base_range,
                abs(high - previous_close),
                abs(low - previous_close),
            )
        )
    return ranges


def _cyclical(value: float, period: float) -> tuple[float, float]:
    angle = 2.0 * math.pi * value / period
    return math.sin(angle), math.cos(angle)


def _time_features(timestamp: int | None) -> tuple[float, float, float, float]:
    if timestamp is None:
        return 0.0, 0.0, 0.0, 0.0
    observed_at = datetime.fromtimestamp(int(timestamp), tz=FEATURE_TIME_TZ)
    hour_value = (
        observed_at.hour
        + observed_at.minute / 60.0
        + observed_at.second / 3600.0
    )
    hour_sin, hour_cos = _cyclical(hour_value, 24.0)
    weekday_sin, weekday_cos = _cyclical(float(observed_at.weekday()), 7.0)
    return hour_sin, hour_cos, weekday_sin, weekday_cos


def _feature_vector(
    *,
    closes: Sequence[float],
    opens: Sequence[float],
    highs: Sequence[float],
    lows: Sequence[float],
    short_window: int,
    long_window: int,
    lookback_bars: int,
    volumes: Sequence[float],
    timestamps: Sequence[int],
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

    latest_open = opens[-1] if opens else last_close
    latest_high = highs[-1] if highs else last_close
    latest_low = lows[-1] if lows else last_close
    latest_volume = volumes[-1] if volumes else 0.0
    latest_timestamp = timestamps[-1] if timestamps else None

    true_ranges = _true_ranges(closes=closes, highs=highs, lows=lows)
    atr_window = true_ranges[-lookback_bars:]
    atr = _mean(atr_window)
    atr_pct = _safe_ratio(atr, last_close)
    latest_range = max(latest_high - latest_low, 0.0)
    latest_body = abs(last_close - latest_open)
    latest_upper_wick = max(latest_high - max(latest_open, last_close), 0.0)
    latest_lower_wick = max(min(latest_open, last_close) - latest_low, 0.0)

    return_values = _returns(closes)
    return_window = return_values[-lookback_bars:]
    return_std = _std(return_window)
    return_zscore = (
        (pct_change - _mean(return_window)) / return_std
        if return_window and return_std > 0
        else 0.0
    )
    short_return_std = _std(return_values[-short_window:])
    long_return_std = _std(return_values[-long_window:])
    volatility_ratio = _safe_ratio(short_return_std, long_return_std)

    previous_volume = volumes[-2] if len(volumes) >= 2 else 0.0
    volume_window = list(volumes[-lookback_bars:])
    mean_volume = _mean(volume_window)
    volume_change = _safe_log_ratio(latest_volume, previous_volume)
    volume_log_ratio = _safe_log_ratio(latest_volume, mean_volume)

    hour_sin, hour_cos, weekday_sin, weekday_cos = _time_features(
        latest_timestamp
    )

    return MLFeatureVector(
        values=[
            pct_change,
            trend_diff,
            volatility,
            _safe_ratio(pct_change, atr_pct),
            _safe_ratio(_window_return(closes, 3), atr_pct),
            _safe_ratio(latest_range, atr),
            _safe_ratio(latest_body, atr),
            _safe_ratio(latest_upper_wick, atr),
            _safe_ratio(latest_lower_wick, atr),
            return_zscore,
            volatility_ratio,
            volume_change,
            volume_log_ratio,
            hour_sin,
            hour_cos,
            weekday_sin,
            weekday_cos,
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
        opens=closes,
        highs=closes,
        lows=closes,
        short_window=short_window,
        long_window=long_window,
        lookback_bars=lookback_bars,
        volumes=[0.0 for _ in closes],
        timestamps=[],
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
    opens = [float(getattr(bar, "open", getattr(bar, "close"))) for bar in ohlc_window]
    highs = [float(getattr(bar, "high", getattr(bar, "close"))) for bar in ohlc_window]
    lows = [float(getattr(bar, "low", getattr(bar, "close"))) for bar in ohlc_window]
    return _feature_vector(
        closes=closes,
        opens=opens,
        highs=highs,
        lows=lows,
        short_window=short_window,
        long_window=long_window,
        lookback_bars=lookback_bars,
        volumes=[float(getattr(bar, "volume", 0.0)) for bar in ohlc_window],
        timestamps=[int(getattr(bar, "timestamp", 0)) for bar in ohlc_window],
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
    "FEATURE_TIME_TZ",
    "MLFeatureVector",
    "compute_feature_vector_from_closes",
    "compute_feature_vector_from_ohlc",
    "compute_features_from_window",
]
