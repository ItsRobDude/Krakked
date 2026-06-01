from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, tzinfo
from typing import Any, List, Optional, Sequence

FEATURE_TIME_TZ: tzinfo = UTC
ML_FEATURE_SCHEMA_VERSION = "ohlc_v5"
ML_FEATURE_CLIPPING_VERSION = "clip_v1"
ML_FEATURE_PROFILE_ALL = "all"
ML_FEATURE_NAMES = (
    "pct_change",
    "trend_diff",
    "volatility",
    "return_atr_1",
    "return_atr_3",
    "range_atr",
    "upper_wick_atr",
    "return_zscore",
    "volatility_ratio",
    "volume_change",
    "volume_log_ratio",
    "hour_sin",
    "hour_cos",
    "weekday_sin",
    "weekday_cos",
)
ML_FEATURE_PROFILES: dict[str, tuple[str, ...]] = {
    ML_FEATURE_PROFILE_ALL: ML_FEATURE_NAMES,
    "drop_weakest": tuple(
        name
        for name in ML_FEATURE_NAMES
        if name
        not in {"pct_change", "return_atr_3", "volatility_ratio"}
    ),
    "volume_change_only": tuple(
        name for name in ML_FEATURE_NAMES if name != "volume_log_ratio"
    ),
    "volume_log_ratio_only": tuple(
        name for name in ML_FEATURE_NAMES if name != "volume_change"
    ),
    "drop_time": tuple(
        name
        for name in ML_FEATURE_NAMES
        if name not in {"hour_sin", "hour_cos", "weekday_sin", "weekday_cos"}
    ),
}
ML_FEATURE_CLIP_RANGES: dict[str, tuple[float, float]] = {
    "pct_change": (-0.15, 0.15),
    "return_atr_1": (-5.0, 5.0),
    "return_atr_3": (-5.0, 5.0),
    "range_atr": (0.0, 5.0),
    "upper_wick_atr": (0.0, 5.0),
    "return_zscore": (-3.0, 3.0),
    "volatility_ratio": (0.0, 10.0),
    "volume_change": (-5.0, 5.0),
    "volume_log_ratio": (-5.0, 5.0),
}


@dataclass(frozen=True)
class MLFeatureVector:
    values: List[float]
    names: List[str]
    schema_version: str
    profile: str = ML_FEATURE_PROFILE_ALL
    clipping_version: str = ML_FEATURE_CLIPPING_VERSION
    clipping: dict[str, dict[str, float | bool]] = field(default_factory=dict)

    def to_metadata(self) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            name: value for name, value in zip(self.names, self.values)
        }
        metadata["feature_schema_version"] = self.schema_version
        metadata["feature_profile"] = self.profile
        metadata["feature_names"] = list(self.names)
        excluded = [name for name in ML_FEATURE_NAMES if name not in set(self.names)]
        if excluded:
            metadata["feature_profile_excluded_features"] = excluded
        metadata["feature_clipping_version"] = self.clipping_version
        if self.clipping:
            metadata["feature_clipping"] = {
                name: dict(details) for name, details in self.clipping.items()
            }
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


def normalize_feature_profile(value: object = None) -> str:
    if value is None or value == "":
        return ML_FEATURE_PROFILE_ALL
    profile = str(value)
    if profile not in ML_FEATURE_PROFILES:
        choices = ", ".join(sorted(ML_FEATURE_PROFILES))
        raise ValueError(f"Unknown ML feature profile {profile!r}; expected one of: {choices}")
    return profile


def feature_names_for_profile(value: object = None) -> tuple[str, ...]:
    return ML_FEATURE_PROFILES[normalize_feature_profile(value)]


def feature_model_key_suffix(value: object = None) -> str:
    profile = normalize_feature_profile(value)
    suffix = f"features_{ML_FEATURE_SCHEMA_VERSION}"
    if profile == ML_FEATURE_PROFILE_ALL:
        return suffix
    return f"{suffix}_profile_{profile}"


def _clip_feature(
    name: str,
    raw_value: float,
) -> tuple[float, Optional[dict[str, float | bool]]]:
    clip_range = ML_FEATURE_CLIP_RANGES.get(name)
    if clip_range is None:
        return raw_value, None
    cap_min, cap_max = clip_range
    clipped_value = min(max(raw_value, cap_min), cap_max)
    return clipped_value, {
        "cap_min": cap_min,
        "cap_max": cap_max,
        "raw_value": raw_value,
        "clipped_value": clipped_value,
        "was_clipped": clipped_value != raw_value,
    }


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
    feature_profile: object = None,
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
    latest_upper_wick = max(latest_high - max(latest_open, last_close), 0.0)

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

    raw_values = {
        "pct_change": pct_change,
        "trend_diff": trend_diff,
        "volatility": volatility,
        "return_atr_1": _safe_ratio(pct_change, atr_pct),
        "return_atr_3": _safe_ratio(_window_return(closes, 3), atr_pct),
        "range_atr": _safe_ratio(latest_range, atr),
        "upper_wick_atr": _safe_ratio(latest_upper_wick, atr),
        "return_zscore": return_zscore,
        "volatility_ratio": volatility_ratio,
        "volume_change": volume_change,
        "volume_log_ratio": volume_log_ratio,
        "hour_sin": hour_sin,
        "hour_cos": hour_cos,
        "weekday_sin": weekday_sin,
        "weekday_cos": weekday_cos,
    }
    profile = normalize_feature_profile(feature_profile)
    feature_names = feature_names_for_profile(profile)
    values: list[float] = []
    clipping: dict[str, dict[str, float | bool]] = {}
    for name in feature_names:
        clipped_value, clipping_metadata = _clip_feature(name, raw_values[name])
        values.append(clipped_value)
        if clipping_metadata is not None:
            clipping[name] = clipping_metadata

    return MLFeatureVector(
        values=values,
        names=list(feature_names),
        schema_version=ML_FEATURE_SCHEMA_VERSION,
        profile=profile,
        clipping=clipping,
    )


def compute_feature_vector_from_closes(
    closes: Sequence[float],
    short_window: int,
    long_window: int,
    lookback_bars: int,
    feature_profile: object = None,
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
        feature_profile=feature_profile,
    )


def compute_feature_vector_from_ohlc(
    ohlc_window: Sequence[object],
    short_window: int,
    long_window: int,
    lookback_bars: int,
    feature_profile: object = None,
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
        feature_profile=feature_profile,
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
    "ML_FEATURE_CLIPPING_VERSION",
    "ML_FEATURE_CLIP_RANGES",
    "ML_FEATURE_PROFILE_ALL",
    "ML_FEATURE_PROFILES",
    "FEATURE_TIME_TZ",
    "MLFeatureVector",
    "compute_feature_vector_from_closes",
    "compute_feature_vector_from_ohlc",
    "compute_features_from_window",
    "feature_model_key_suffix",
    "feature_names_for_profile",
    "normalize_feature_profile",
]
