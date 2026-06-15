"""Display-only market risk signal helpers."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Sequence

from krakked.market_data.models import OHLCBar
from krakked.market_data.ohlc_fetcher import TIMEFRAME_MAP

RISK_SIGNAL_SOURCE = "riskmetrics_ewma"
DEFAULT_RISK_SIGNAL_PAIR = "BTC/USD"
DEFAULT_RISK_SIGNAL_TIMEFRAME = "4h"


@dataclass(frozen=True)
class EWMARiskSignalParams:
    benchmark_pair: str = DEFAULT_RISK_SIGNAL_PAIR
    timeframe: str = DEFAULT_RISK_SIGNAL_TIMEFRAME
    horizon_bars: int = 6
    ewma_lambda: float = 0.94
    min_bars: int = 84
    lookback_bars: int = 720
    epsilon_variance: float = 1e-12
    stale_after_seconds: float | None = None

    def __post_init__(self) -> None:
        if int(self.horizon_bars) < 1:
            raise ValueError("horizon_bars must be at least 1")
        if int(self.min_bars) < 2:
            raise ValueError("min_bars must be at least 2")
        if int(self.lookback_bars) < int(self.min_bars):
            raise ValueError("lookback_bars must be at least min_bars")
        if not 0.0 < float(self.ewma_lambda) < 1.0:
            raise ValueError("ewma_lambda must be between 0 and 1")
        if float(self.epsilon_variance) <= 0.0:
            raise ValueError("epsilon_variance must be greater than 0")


def build_ewma_risk_signal(
    bars: Sequence[OHLCBar],
    *,
    params: EWMARiskSignalParams | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Build a display-only EWMA market-risk payload from cached OHLC bars."""

    config = params or EWMARiskSignalParams()
    now = _to_utc(generated_at or datetime.now(timezone.utc))
    sorted_bars = sorted(list(bars), key=lambda bar: int(bar.timestamp))
    base_payload = _base_payload(config, generated_at=now, bars_used=len(sorted_bars))

    if len(sorted_bars) < int(config.min_bars):
        return {
            **base_payload,
            "status": "insufficient_data",
            "notes": [
                f"Need at least {int(config.min_bars)} {config.timeframe} bars for the EWMA risk signal.",
                _display_only_note(),
            ],
        }

    ewma_per_bar = ewma_per_bar_variances(
        sorted_bars,
        ewma_lambda=float(config.ewma_lambda),
        epsilon=float(config.epsilon_variance),
    )
    horizon_variances = [
        max(float(value) * int(config.horizon_bars), float(config.epsilon_variance))
        for value in ewma_per_bar[1:]
    ]
    horizon_vols_pct = [math.sqrt(value) * 100.0 for value in horizon_variances]

    latest_bar = sorted_bars[-1]
    latest_bar_time = datetime.fromtimestamp(int(latest_bar.timestamp), tz=timezone.utc)
    latest_per_bar_variance = max(
        float(ewma_per_bar[-1]),
        float(config.epsilon_variance),
    )
    latest_horizon_variance = max(
        latest_per_bar_variance * int(config.horizon_bars),
        float(config.epsilon_variance),
    )
    latest_horizon_vol_pct = math.sqrt(latest_horizon_variance) * 100.0
    latest_bar_age_seconds = max((now - latest_bar_time).total_seconds(), 0.0)
    stale_after_seconds = _stale_after_seconds(config)
    is_stale = latest_bar_age_seconds > stale_after_seconds
    percentile = _rank_percentile(horizon_vols_pct, latest_horizon_vol_pct)
    risk_level = _risk_level(percentile)
    notes = [_display_only_note()]
    if is_stale:
        notes.insert(
            0,
            f"Latest {config.timeframe} bar is stale for display use.",
        )

    return {
        **base_payload,
        "available": not is_stale,
        "status": "stale_data" if is_stale else "ready",
        "latest_bar_time": latest_bar_time,
        "latest_bar_age_seconds": latest_bar_age_seconds,
        "ewma_per_bar_variance": latest_per_bar_variance,
        "ewma_per_bar_volatility_pct": math.sqrt(latest_per_bar_variance) * 100.0,
        "ewma_horizon_variance": latest_horizon_variance,
        "ewma_horizon_volatility_pct": latest_horizon_vol_pct,
        "volatility_percentile": percentile,
        "risk_level": risk_level,
        "thresholds": {
            "elevated_percentile": 75.0,
            "stressed_percentile": 90.0,
            "elevated_horizon_volatility_pct": _percentile(horizon_vols_pct, 75.0),
            "stressed_horizon_volatility_pct": _percentile(horizon_vols_pct, 90.0),
        },
        "notes": notes,
    }


def ewma_per_bar_variances(
    bars: Sequence[OHLCBar],
    *,
    ewma_lambda: float,
    epsilon: float,
) -> list[float]:
    if not bars:
        return []
    values = [float(epsilon)] * len(bars)
    current = float(epsilon)
    for index in range(1, len(bars)):
        squared_return = squared_log_return(bars[index - 1], bars[index])
        if index == 1:
            current = max(squared_return, float(epsilon))
        else:
            current = (
                float(ewma_lambda) * current
                + (1.0 - float(ewma_lambda)) * squared_return
            )
            current = max(current, float(epsilon))
        values[index] = current
    return values


def squared_log_return(previous: OHLCBar, current: OHLCBar) -> float:
    if previous.close <= 0.0 or current.close <= 0.0:
        return 0.0
    return math.log(float(current.close) / float(previous.close)) ** 2


def _base_payload(
    params: EWMARiskSignalParams,
    *,
    generated_at: datetime,
    bars_used: int,
) -> dict[str, Any]:
    return {
        "available": False,
        "status": "insufficient_data",
        "source": RISK_SIGNAL_SOURCE,
        "benchmark_pair": params.benchmark_pair,
        "timeframe": params.timeframe,
        "generated_at": generated_at,
        "latest_bar_time": None,
        "latest_bar_age_seconds": None,
        "bars_used": int(bars_used),
        "lookback_bars": int(params.lookback_bars),
        "min_bars": int(params.min_bars),
        "horizon_bars": int(params.horizon_bars),
        "ewma_lambda": float(params.ewma_lambda),
        "ewma_per_bar_variance": None,
        "ewma_per_bar_volatility_pct": None,
        "ewma_horizon_variance": None,
        "ewma_horizon_volatility_pct": None,
        "volatility_percentile": None,
        "risk_level": None,
        "thresholds": {},
        "display_only": True,
        "trading_effect": False,
        "runtime_wiring_approved": False,
        "notes": [],
    }


def _display_only_note() -> str:
    return "Display-only context; does not alter strategy selection, sizing, or order flow."


def _risk_level(percentile: float | None) -> str | None:
    if percentile is None:
        return None
    if percentile >= 90.0:
        return "stressed"
    if percentile >= 75.0:
        return "elevated"
    return "normal"


def _rank_percentile(values: Sequence[float], current: float) -> float | None:
    cleaned = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not cleaned:
        return None
    count = sum(1 for value in cleaned if value <= float(current))
    return 100.0 * count / len(cleaned)


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    cleaned = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not cleaned:
        return None
    if len(cleaned) == 1:
        return cleaned[0]
    rank = (float(percentile) / 100.0) * (len(cleaned) - 1)
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return cleaned[int(rank)]
    weight = rank - lower
    return cleaned[lower] * (1.0 - weight) + cleaned[upper] * weight


def _stale_after_seconds(params: EWMARiskSignalParams) -> float:
    if params.stale_after_seconds is not None:
        return float(params.stale_after_seconds)
    timeframe_seconds = _timeframe_seconds(params.timeframe)
    return max(timeframe_seconds * 3.0, 12.0 * 60.0 * 60.0)


def _timeframe_seconds(timeframe: str) -> float:
    if timeframe in TIMEFRAME_MAP:
        return float(TIMEFRAME_MAP[timeframe] * 60)
    if timeframe.endswith("m"):
        return float(int(timeframe[:-1]) * 60)
    if timeframe.endswith("h"):
        return float(int(timeframe[:-1]) * 3600)
    if timeframe.endswith("d"):
        return float(int(timeframe[:-1]) * 86400)
    raise ValueError(f"Unsupported timeframe: {timeframe}")


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
