from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence

ML_FEATURE_SCHEMA_VERSION = "ohlc_v1"
ML_FEATURE_NAMES = ("pct_change", "trend_diff", "volatility")


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


def compute_feature_vector_from_closes(
    closes: Sequence[float], short_window: int, long_window: int, lookback_bars: int
) -> Optional[MLFeatureVector]:
    """
    Compute ML features from a list of close prices.

    Features:
    1. pct_change: Return of the last bar (relative to T-2).
    2. trend_diff: (Short MA - Long MA) / Long MA.
    3. volatility: Coefficient of Variation (std/mean) over lookback window.

    Args:
        closes: List of close prices. Must include at least 3 bars.
                closes[-1] is the latest close (T-1).
        short_window: Period for short MA.
        long_window: Period for long MA.
        lookback_bars: Period for volatility calc.

    Returns:
        Feature vector or None if insufficient data.
    """
    if not closes or len(closes) < 3:
        return None

    last_close, prev_close = closes[-1], closes[-2]
    if prev_close <= 0:
        return None

    pct_change = (last_close - prev_close) / prev_close

    short_len = min(short_window, len(closes))
    long_len = min(long_window, len(closes))
    short_ma = sum(closes[-short_len:]) / short_len if short_len > 0 else 0.0
    long_ma = sum(closes[-long_len:]) / long_len if long_len > 0 else 0.0
    trend_diff = ((short_ma - long_ma) / long_ma) if long_ma > 0 else 0.0

    window = closes[-lookback_bars:]
    mean_close = sum(window) / len(window)
    volatility = 0.0
    if mean_close > 0 and len(window) > 1:
        variance = sum((c - mean_close) ** 2 for c in window) / len(window)
        volatility = math.sqrt(variance) / mean_close

    return MLFeatureVector(
        values=[pct_change, trend_diff, volatility],
        names=list(ML_FEATURE_NAMES),
        schema_version=ML_FEATURE_SCHEMA_VERSION,
    )


def compute_feature_vector_from_ohlc(
    ohlc_window: Sequence[object],
    short_window: int,
    long_window: int,
    lookback_bars: int,
) -> Optional[MLFeatureVector]:
    closes = [float(getattr(bar, "close")) for bar in ohlc_window]
    return compute_feature_vector_from_closes(
        closes,
        short_window,
        long_window,
        lookback_bars,
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
