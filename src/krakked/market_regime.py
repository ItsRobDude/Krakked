"""Runtime-safe market-regime classification helpers."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import UTC, datetime
from statistics import mean, pstdev
from typing import Any, Mapping, Protocol, Sequence

from krakked.market_data.models import OHLCBar
from krakked.market_data.ohlc_fetcher import TIMEFRAME_MAP

DEFAULT_MARKET_REGIME_TIMEFRAME = "4h"


class MarketDataOHLCReader(Protocol):
    def get_ohlc(self, pair: str, timeframe: str, lookback: int) -> list[OHLCBar]: ...


@dataclass(frozen=True)
class MarketRegimeOverlayParams:
    timeframe: str = DEFAULT_MARKET_REGIME_TIMEFRAME
    benchmark_pair: str = "BTC/USD"
    momentum_lookback_bars: int = 42
    basket_momentum_lookback_bars: int = 42
    volatility_lookback_bars: int = 42
    drawdown_lookback_bars: int = 42
    neutral_allocation_multiplier: float = 0.5
    risk_off_allocation_multiplier: float = 0.0
    neutral_benchmark_momentum_bps: float = 150.0
    neutral_basket_momentum_bps: float = 100.0
    risk_off_benchmark_momentum_bps: float = 0.0
    risk_off_basket_momentum_bps: float = 0.0
    neutral_benchmark_drawdown_pct: float = 4.0
    risk_off_benchmark_drawdown_pct: float = 8.0
    neutral_volatility_pct: float = 2.5
    risk_off_volatility_pct: float = 4.0

    def __post_init__(self) -> None:
        if self.timeframe not in TIMEFRAME_MAP:
            raise ValueError(f"Unsupported market regime timeframe: {self.timeframe}")
        for field_name in (
            "momentum_lookback_bars",
            "basket_momentum_lookback_bars",
            "volatility_lookback_bars",
            "drawdown_lookback_bars",
        ):
            if int(getattr(self, field_name)) < 2:
                raise ValueError(f"{field_name} must be at least 2")
        for field_name in (
            "neutral_allocation_multiplier",
            "risk_off_allocation_multiplier",
        ):
            value = float(getattr(self, field_name))
            if value < 0.0 or value > 1.0:
                raise ValueError(f"{field_name} must be between 0.0 and 1.0")
        if self.risk_off_allocation_multiplier > self.neutral_allocation_multiplier:
            raise ValueError(
                "risk_off_allocation_multiplier cannot exceed "
                "neutral_allocation_multiplier"
            )


@dataclass
class MarketRegimeSnapshot:
    timestamp: int
    regime: str
    allocation_multiplier: float
    reason_codes: list[str]
    features: dict[str, Any]

    @property
    def time(self) -> str:
        return datetime.fromtimestamp(int(self.timestamp), tz=UTC).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": int(self.timestamp),
            "time": self.time,
            "regime": self.regime,
            "allocation_multiplier": self.allocation_multiplier,
            "reason_codes": list(self.reason_codes),
            "features": copy.deepcopy(self.features),
        }


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _clean_pairs(values: Sequence[str]) -> list[str]:
    pairs: list[str] = []
    seen: set[str] = set()
    for value in values:
        pair = str(value).strip()
        if not pair or pair in seen:
            continue
        pairs.append(pair)
        seen.add(pair)
    return pairs


def _default_pairs(config: Any) -> list[str]:
    pairs = _clean_pairs(list(getattr(config.universe, "include_pairs", []) or []))
    return pairs or ["BTC/USD", "ETH/USD", "SOL/USD", "ADA/USD"]


def _sort_bars(bars: Sequence[OHLCBar]) -> list[OHLCBar]:
    return sorted(list(bars), key=lambda bar: int(bar.timestamp))


def _bars_at_or_before(
    bars: Sequence[OHLCBar], ts: int, lookback: int
) -> list[OHLCBar]:
    filtered = [bar for bar in bars if int(bar.timestamp) <= int(ts)]
    return filtered[-lookback:] if lookback > 0 else []


def _feature_payload(bars: Sequence[OHLCBar], ts: int, lookback: int) -> dict[str, Any]:
    window = _bars_at_or_before(bars, ts, lookback)
    if len(window) < lookback:
        return {
            "available": False,
            "bar_count": len(window),
            "required_bars": lookback,
        }

    closes = [float(bar.close) for bar in window]
    if not closes or closes[0] <= 0.0 or closes[-1] <= 0.0:
        return {
            "available": False,
            "bar_count": len(window),
            "required_bars": lookback,
        }

    returns: list[float] = []
    for previous, current in zip(closes, closes[1:]):
        if previous <= 0.0:
            continue
        returns.append((current - previous) / previous)

    peak = max(closes)
    drawdown_pct = ((peak - closes[-1]) / peak) * 100.0 if peak > 0.0 else 0.0
    return {
        "available": True,
        "bar_count": len(window),
        "required_bars": lookback,
        "first_close": closes[0],
        "last_close": closes[-1],
        "momentum_bps": ((closes[-1] - closes[0]) / closes[0]) * 10_000.0,
        "drawdown_pct": drawdown_pct,
        "volatility_pct": (pstdev(returns) * 100.0) if len(returns) >= 2 else 0.0,
    }


def _aggregate_basket_features(
    bars_by_pair: Mapping[str, Sequence[OHLCBar]],
    *,
    ts: int,
    lookback: int,
) -> dict[str, Any]:
    pair_features = {
        pair: _feature_payload(bars, ts, lookback)
        for pair, bars in bars_by_pair.items()
    }
    available = [
        payload for payload in pair_features.values() if bool(payload.get("available"))
    ]
    if not available:
        return {
            "available": False,
            "available_pair_count": 0,
            "pair_count": len(pair_features),
            "pairs": copy.deepcopy(pair_features),
        }

    return {
        "available": True,
        "available_pair_count": len(available),
        "pair_count": len(pair_features),
        "momentum_bps": mean(float(item["momentum_bps"]) for item in available),
        "drawdown_pct": mean(float(item["drawdown_pct"]) for item in available),
        "volatility_pct": mean(float(item["volatility_pct"]) for item in available),
        "pairs": copy.deepcopy(pair_features),
    }


def classify_market_regime_snapshot(
    bars_by_pair: Mapping[str, Sequence[OHLCBar]],
    *,
    timestamp: int,
    params: MarketRegimeOverlayParams,
) -> MarketRegimeSnapshot:
    benchmark_bars = _sort_bars(bars_by_pair.get(params.benchmark_pair, []))
    benchmark_lookback = max(
        params.momentum_lookback_bars,
        params.volatility_lookback_bars,
        params.drawdown_lookback_bars,
    )
    benchmark = _feature_payload(benchmark_bars, timestamp, benchmark_lookback)
    basket = _aggregate_basket_features(
        {pair: _sort_bars(bars) for pair, bars in bars_by_pair.items()},
        ts=timestamp,
        lookback=params.basket_momentum_lookback_bars,
    )
    features = {
        "benchmark_pair": params.benchmark_pair,
        "benchmark": benchmark,
        "basket": basket,
    }

    if not benchmark.get("available") or not basket.get("available"):
        return MarketRegimeSnapshot(
            timestamp=int(timestamp),
            regime="neutral",
            allocation_multiplier=params.neutral_allocation_multiplier,
            reason_codes=["insufficient_data"],
            features=features,
        )

    benchmark_momentum = float(benchmark["momentum_bps"])
    basket_momentum = float(basket["momentum_bps"])
    benchmark_drawdown = float(benchmark["drawdown_pct"])
    benchmark_volatility = float(benchmark["volatility_pct"])

    risk_off_reasons: list[str] = []
    if (
        benchmark_momentum < params.risk_off_benchmark_momentum_bps
        and basket_momentum < params.risk_off_basket_momentum_bps
    ):
        risk_off_reasons.extend(["btc_momentum_negative", "basket_momentum_negative"])
    if benchmark_drawdown >= params.risk_off_benchmark_drawdown_pct:
        risk_off_reasons.append("btc_drawdown_exceeded")
    if benchmark_volatility >= params.risk_off_volatility_pct:
        risk_off_reasons.append("volatility_spike")

    if risk_off_reasons:
        return MarketRegimeSnapshot(
            timestamp=int(timestamp),
            regime="risk_off",
            allocation_multiplier=params.risk_off_allocation_multiplier,
            reason_codes=_unique_reasons(risk_off_reasons),
            features=features,
        )

    neutral_reasons: list[str] = []
    if benchmark_momentum < params.neutral_benchmark_momentum_bps:
        neutral_reasons.append("btc_momentum_soft")
    if basket_momentum < params.neutral_basket_momentum_bps:
        neutral_reasons.append("basket_momentum_soft")
    if benchmark_drawdown >= params.neutral_benchmark_drawdown_pct:
        neutral_reasons.append("btc_drawdown_elevated")
    if benchmark_volatility >= params.neutral_volatility_pct:
        neutral_reasons.append("volatility_elevated")

    if neutral_reasons:
        return MarketRegimeSnapshot(
            timestamp=int(timestamp),
            regime="neutral",
            allocation_multiplier=params.neutral_allocation_multiplier,
            reason_codes=_unique_reasons(neutral_reasons),
            features=features,
        )

    return MarketRegimeSnapshot(
        timestamp=int(timestamp),
        regime="risk_on",
        allocation_multiplier=1.0,
        reason_codes=["risk_on_conditions_met"],
        features=features,
    )


def _unique_reasons(reasons: Sequence[str]) -> list[str]:
    unique: list[str] = []
    for reason in reasons:
        if reason not in unique:
            unique.append(reason)
    return unique


def classify_market_regime_from_market_data(
    market_data: MarketDataOHLCReader,
    *,
    pairs: Sequence[str],
    params: MarketRegimeOverlayParams,
    timestamp: int,
) -> MarketRegimeSnapshot:
    selected_pairs = _clean_pairs(list(pairs))
    if params.benchmark_pair not in selected_pairs:
        selected_pairs.insert(0, params.benchmark_pair)
    lookback = max(
        params.momentum_lookback_bars,
        params.basket_momentum_lookback_bars,
        params.volatility_lookback_bars,
        params.drawdown_lookback_bars,
    )
    bars_by_pair = {
        pair: market_data.get_ohlc(pair, params.timeframe, lookback=lookback)
        for pair in selected_pairs
    }
    return classify_market_regime_snapshot(
        bars_by_pair,
        timestamp=timestamp,
        params=params,
    )
