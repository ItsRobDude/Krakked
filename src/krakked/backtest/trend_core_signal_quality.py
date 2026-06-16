"""Research-only forward-return diagnostics for the trend_core signal."""

from __future__ import annotations

import copy
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from statistics import mean, median
from typing import Any, Mapping, Sequence

from krakked.config import AppConfig, StrategyConfig
from krakked.market_data.ohlc_fetcher import TIMEFRAME_MAP
from krakked.market_regime import _as_utc, _clean_pairs, _default_pairs
from krakked.portfolio.models import SpotPosition
from krakked.strategy.base import StrategyContext
from krakked.strategy.regime import infer_regime
from krakked.strategy.strategies.demo_strategy import TrendFollowingStrategy
from krakked.utils.strings import unique_strings as _unique_strings

from .market_regime_overlay import _preflight_to_dict
from .runner import BacktestMarketData, backtest_strict_data_details

REPORT_TYPE_TREND_CORE_SIGNAL_QUALITY = "trend_core_signal_quality"
REPORT_VERSION = 1
DEFAULT_FORWARD_HORIZON_BARS = (1, 3, 6)


@dataclass
class TrendCoreSignalQualityResult:
    generated_at: datetime
    summary: dict[str, Any]
    overall: dict[str, Any]
    by_timeframe: list[dict[str, Any]]
    by_pair: list[dict[str, Any]]
    by_trend_strength_quartile: list[dict[str, Any]]
    by_confidence_quartile: list[dict[str, Any]]
    strongest_vs_weakest: dict[str, Any]
    signals_sample: list[dict[str, Any]]
    preflight: dict[str, Any] | None

    def to_report_dict(self) -> dict[str, Any]:
        return {
            "report_version": REPORT_VERSION,
            "report_type": REPORT_TYPE_TREND_CORE_SIGNAL_QUALITY,
            "generated_at": self.generated_at.astimezone(UTC).isoformat(),
            "summary": copy.deepcopy(self.summary),
            "overall": copy.deepcopy(self.overall),
            "by_timeframe": copy.deepcopy(self.by_timeframe),
            "by_pair": copy.deepcopy(self.by_pair),
            "by_trend_strength_quartile": copy.deepcopy(
                self.by_trend_strength_quartile
            ),
            "by_confidence_quartile": copy.deepcopy(self.by_confidence_quartile),
            "strongest_vs_weakest": copy.deepcopy(self.strongest_vs_weakest),
            "signals_sample": copy.deepcopy(self.signals_sample),
            "preflight": copy.deepcopy(self.preflight),
        }


class _SignalQualityPortfolio:
    def __init__(self, config: AppConfig) -> None:
        self.app_config = config

    def get_positions(self) -> list[SpotPosition]:
        return []


def run_trend_core_signal_quality(
    config: AppConfig,
    *,
    start: datetime,
    end: datetime,
    pairs: Sequence[str] | None = None,
    timeframes: Sequence[str] | None = None,
    forward_horizon_bars: Sequence[int] | None = None,
    fee_bps: float = 25.0,
    fresh_bars_only: bool = False,
    strict_data: bool = False,
    warmup_days: float = 30.0,
    max_signal_rows: int = 50,
) -> TrendCoreSignalQualityResult:
    """Collect trend_core entry signals and score their forward returns."""

    start = _as_utc(start)
    end = _as_utc(end)
    if end <= start:
        raise ValueError("end must be after start")
    if float(fee_bps) < 0.0:
        raise ValueError("fee_bps must be greater than or equal to 0")
    if float(warmup_days) < 0.0:
        raise ValueError("warmup_days must be greater than or equal to 0")

    strategy_config = _trend_core_config(config)
    selected_pairs = _trend_core_pairs(config, strategy_config, pairs)
    selected_timeframes = _trend_core_timeframes(strategy_config, timeframes)
    horizons = _validate_horizons(forward_horizon_bars)

    regime_timeframe = str(
        (strategy_config.params or {}).get("regime_timeframe") or "1d"
    )
    load_timeframes = _unique_strings([*selected_timeframes, regime_timeframe, "1h"])
    warmup_start = start - timedelta(days=float(warmup_days)) if warmup_days else None

    market_data = BacktestMarketData(
        config,
        pairs=selected_pairs,
        timeframes=load_timeframes,
        start=start,
        end=end,
        warmup_start=warmup_start,
        warmup_timeframes=load_timeframes,
        warmup_days=float(warmup_days),
    )
    try:
        preflight = market_data.get_preflight()
        strict_details = backtest_strict_data_details(preflight)
        if strict_data and strict_details:
            raise ValueError(
                "trend_core signal-quality failed in strict mode: "
                + "; ".join(strict_details)
            )

        signals = _collect_trend_core_signals(
            market_data,
            config=config,
            strategy_config=strategy_config,
            start=start,
            end=end,
            pairs=selected_pairs,
            timeframes=selected_timeframes,
            horizons=horizons,
            fresh_bars_only=bool(fresh_bars_only),
        )
        return build_trend_core_signal_quality_report(
            signals,
            start=start,
            end=end,
            pairs=selected_pairs,
            timeframes=selected_timeframes,
            horizons=horizons,
            fee_bps=float(fee_bps),
            fresh_bars_only=bool(fresh_bars_only),
            strict_data=bool(strict_data),
            warmup_days=float(warmup_days),
            max_signal_rows=max_signal_rows,
            preflight=_preflight_to_dict(preflight),
        )
    finally:
        shutdown = getattr(market_data, "shutdown", None)
        if callable(shutdown):
            shutdown()


def build_trend_core_signal_quality_report(
    signals: Sequence[Mapping[str, Any]],
    *,
    start: datetime,
    end: datetime,
    pairs: Sequence[str],
    timeframes: Sequence[str],
    horizons: Sequence[int],
    fee_bps: float,
    fresh_bars_only: bool,
    strict_data: bool,
    warmup_days: float,
    max_signal_rows: int = 50,
    preflight: dict[str, Any] | None = None,
) -> TrendCoreSignalQualityResult:
    start = _as_utc(start)
    end = _as_utc(end)
    rows = [copy.deepcopy(dict(signal)) for signal in signals]
    horizon_values = _validate_horizons(horizons)
    round_trip_fee_hurdle_pct = (float(fee_bps) * 2.0) / 100.0
    primary_horizon = max(horizon_values)

    overall = _stats_payload(rows, horizon_values, round_trip_fee_hurdle_pct)
    by_timeframe = _grouped_stats(
        rows,
        group_key="timeframe",
        horizons=horizon_values,
        round_trip_fee_hurdle_pct=round_trip_fee_hurdle_pct,
    )
    by_pair = _grouped_stats(
        rows,
        group_key="pair",
        horizons=horizon_values,
        round_trip_fee_hurdle_pct=round_trip_fee_hurdle_pct,
    )
    by_strength = _quartile_stats(
        rows,
        metric_key="trend_strength_bps",
        group_key="trend_strength_quartile",
        horizons=horizon_values,
        round_trip_fee_hurdle_pct=round_trip_fee_hurdle_pct,
    )
    by_confidence = _quartile_stats(
        rows,
        metric_key="confidence",
        group_key="confidence_quartile",
        horizons=horizon_values,
        round_trip_fee_hurdle_pct=round_trip_fee_hurdle_pct,
    )
    strongest_vs_weakest = _strongest_vs_weakest(
        by_strength,
        primary_horizon=primary_horizon,
    )
    assessment = _assess_signal_quality(
        rows,
        overall=overall,
        strongest_vs_weakest=strongest_vs_weakest,
        primary_horizon=primary_horizon,
        round_trip_fee_hurdle_pct=round_trip_fee_hurdle_pct,
    )

    summary = {
        "research_only": True,
        "runtime_config_changed": False,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "pairs": list(pairs),
        "timeframes": list(timeframes),
        "forward_horizon_bars": list(horizon_values),
        "primary_horizon_bars": primary_horizon,
        "fee_bps": float(fee_bps),
        "round_trip_fee_hurdle_pct": round_trip_fee_hurdle_pct,
        "fresh_bars_only": bool(fresh_bars_only),
        "strict_data": bool(strict_data),
        "warmup_days": float(warmup_days),
        "total_signals": len(rows),
        "status": assessment["status"],
        "status_note": assessment["status_note"],
        "promotion_ready": assessment["promotion_ready"],
        "gate_reasons": assessment["gate_reasons"],
    }

    return TrendCoreSignalQualityResult(
        generated_at=datetime.now(UTC),
        summary=summary,
        overall=overall,
        by_timeframe=by_timeframe,
        by_pair=by_pair,
        by_trend_strength_quartile=by_strength,
        by_confidence_quartile=by_confidence,
        strongest_vs_weakest=strongest_vs_weakest,
        signals_sample=[copy.deepcopy(row) for row in rows[: max(0, max_signal_rows)]],
        preflight=copy.deepcopy(preflight),
    )


def _collect_trend_core_signals(
    market_data: BacktestMarketData,
    *,
    config: AppConfig,
    strategy_config: StrategyConfig,
    start: datetime,
    end: datetime,
    pairs: Sequence[str],
    timeframes: Sequence[str],
    horizons: Sequence[int],
    fresh_bars_only: bool,
) -> list[dict[str, Any]]:
    params = copy.deepcopy(strategy_config.params or {})
    params["timeframes"] = list(timeframes)
    research_config = StrategyConfig(
        name=strategy_config.name,
        type=strategy_config.type,
        enabled=True,
        params=params,
    )
    strategy = TrendFollowingStrategy(research_config)
    portfolio = _SignalQualityPortfolio(config)
    signals: list[dict[str, Any]] = []

    for ts in market_data.iter_timestamps():
        if ts < int(start.timestamp()) or ts > int(end.timestamp()):
            continue
        now = datetime.fromtimestamp(int(ts), tz=UTC)
        market_data.set_time(now)
        regime = infer_regime(market_data, list(pairs))
        for timeframe in timeframes:
            if fresh_bars_only and not _has_fresh_bar_at(
                market_data, pairs=pairs, timeframe=timeframe, timestamp=ts
            ):
                continue
            context = StrategyContext(
                now=now,
                universe=list(pairs),
                market_data=market_data,
                portfolio=portfolio,  # type: ignore[arg-type]
                timeframe=timeframe,
                regime=regime,
            )
            intents = strategy.generate_intents(context)
            for intent in intents:
                if intent.side != "long" or intent.intent_type not in {
                    "enter",
                    "increase",
                }:
                    continue
                current_bar = market_data.get_bar_at_or_before(
                    intent.pair, timeframe, ts
                )
                if current_bar is None or float(current_bar.close) <= 0.0:
                    continue
                signals.append(
                    _signal_row(
                        market_data,
                        intent=intent,
                        timeframe=timeframe,
                        timestamp=ts,
                        current_bar=current_bar,
                        horizons=horizons,
                        regime=regime.regime_for(intent.pair),
                    )
                )
    return signals


def _signal_row(
    market_data: BacktestMarketData,
    *,
    intent: Any,
    timeframe: str,
    timestamp: int,
    current_bar: Any,
    horizons: Sequence[int],
    regime: Any,
) -> dict[str, Any]:
    frame_seconds = int(TIMEFRAME_MAP[timeframe]) * 60
    current_close = float(current_bar.close)
    current_bar_ts = int(current_bar.timestamp)
    forward_returns: dict[str, float | None] = {}
    for horizon in horizons:
        target_ts = current_bar_ts + (frame_seconds * int(horizon))
        future_bar = market_data.get_bar_at_or_after(intent.pair, timeframe, target_ts)
        if future_bar is None or current_close <= 0.0:
            forward_returns[str(int(horizon))] = None
            continue
        future_close = float(future_bar.close)
        forward_returns[str(int(horizon))] = (
            (future_close - current_close) / current_close
        ) * 100.0

    metadata = copy.deepcopy(getattr(intent, "metadata", {}) or {})
    return {
        "timestamp": int(timestamp),
        "time": datetime.fromtimestamp(int(timestamp), tz=UTC).isoformat(),
        "signal_bar_timestamp": current_bar_ts,
        "signal_bar_time": datetime.fromtimestamp(current_bar_ts, tz=UTC).isoformat(),
        "pair": str(intent.pair),
        "timeframe": timeframe,
        "intent_type": str(intent.intent_type),
        "confidence": float(getattr(intent, "confidence", 0.0) or 0.0),
        "trend_strength_bps": _optional_float(metadata.get("trend_strength_bps")),
        "regime": getattr(regime, "value", regime),
        "current_close": current_close,
        "forward_returns_pct": forward_returns,
        "metadata": metadata,
    }


def _trend_core_config(config: AppConfig) -> StrategyConfig:
    strategy_config = config.strategies.configs.get("trend_core")
    if strategy_config is None:
        raise ValueError("trend_core strategy config was not found")
    if strategy_config.type != "trend_following":
        raise ValueError("trend_core signal-quality expects trend_following config")
    return strategy_config


def _trend_core_pairs(
    config: AppConfig,
    strategy_config: StrategyConfig,
    pairs: Sequence[str] | None,
) -> list[str]:
    params = strategy_config.params or {}
    configured_pairs = params.get("pairs")
    if pairs:
        selected = list(pairs)
    elif isinstance(configured_pairs, list) and configured_pairs:
        selected = [str(pair) for pair in configured_pairs]
    else:
        selected = _default_pairs(config)
    cleaned = _clean_pairs(selected)
    if not cleaned:
        raise ValueError("At least one pair is required")
    return cleaned


def _trend_core_timeframes(
    strategy_config: StrategyConfig,
    timeframes: Sequence[str] | None,
) -> list[str]:
    params = strategy_config.params or {}
    configured_timeframes = params.get("timeframes")
    if timeframes:
        selected = [str(timeframe) for timeframe in timeframes]
    elif isinstance(configured_timeframes, list) and configured_timeframes:
        selected = [str(timeframe) for timeframe in configured_timeframes]
    else:
        selected = ["1h"]
    cleaned = _unique_strings(str(timeframe).strip() for timeframe in selected)
    if not cleaned:
        raise ValueError("At least one timeframe is required")
    unsupported = [timeframe for timeframe in cleaned if timeframe not in TIMEFRAME_MAP]
    if unsupported:
        raise ValueError("Unsupported trend_core timeframe: " + ", ".join(unsupported))
    return cleaned


def _validate_horizons(values: Sequence[int] | None) -> list[int]:
    raw_values = list(values or DEFAULT_FORWARD_HORIZON_BARS)
    horizons: list[int] = []
    for value in raw_values:
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("forward_horizon_bars must be integers") from exc
        if parsed < 1:
            raise ValueError("forward_horizon_bars values must be at least 1")
        if parsed not in horizons:
            horizons.append(parsed)
    return sorted(horizons)


def _has_fresh_bar_at(
    market_data: BacktestMarketData,
    *,
    pairs: Sequence[str],
    timeframe: str,
    timestamp: int,
) -> bool:
    for pair in pairs:
        bars = market_data.get_ohlc(pair, timeframe, lookback=1)
        if bars and int(bars[-1].timestamp) == int(timestamp):
            return True
    return False


def _stats_payload(
    rows: Sequence[Mapping[str, Any]],
    horizons: Sequence[int],
    round_trip_fee_hurdle_pct: float,
) -> dict[str, Any]:
    return {
        str(horizon): _horizon_stats(
            rows,
            horizon=horizon,
            round_trip_fee_hurdle_pct=round_trip_fee_hurdle_pct,
        )
        for horizon in horizons
    }


def _horizon_stats(
    rows: Sequence[Mapping[str, Any]],
    *,
    horizon: int,
    round_trip_fee_hurdle_pct: float,
) -> dict[str, Any]:
    returns = _forward_returns(rows, horizon)
    if not returns:
        return {
            "sample_count": 0,
            "mean_return_pct": None,
            "median_return_pct": None,
            "min_return_pct": None,
            "max_return_pct": None,
            "hit_rate": None,
            "fee_adjusted_hit_rate": None,
            "mean_after_fee_pct": None,
        }

    mean_return = mean(returns)
    return {
        "sample_count": len(returns),
        "mean_return_pct": mean_return,
        "median_return_pct": median(returns),
        "min_return_pct": min(returns),
        "max_return_pct": max(returns),
        "hit_rate": sum(1 for value in returns if value > 0.0) / len(returns),
        "fee_adjusted_hit_rate": sum(
            1 for value in returns if value >= round_trip_fee_hurdle_pct
        )
        / len(returns),
        "mean_after_fee_pct": mean_return - round_trip_fee_hurdle_pct,
    }


def _forward_returns(rows: Sequence[Mapping[str, Any]], horizon: int) -> list[float]:
    returns: list[float] = []
    key = str(int(horizon))
    for row in rows:
        payload = row.get("forward_returns_pct") or {}
        if not isinstance(payload, Mapping):
            continue
        value = payload.get(key)
        if value is None:
            continue
        returns.append(float(value))
    return returns


def _grouped_stats(
    rows: Sequence[Mapping[str, Any]],
    *,
    group_key: str,
    horizons: Sequence[int],
    round_trip_fee_hurdle_pct: float,
) -> list[dict[str, Any]]:
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row.get(group_key) or "unknown")].append(row)
    return [
        {
            group_key: key,
            "total_signals": len(group_rows),
            "forward_stats": _stats_payload(
                group_rows,
                horizons,
                round_trip_fee_hurdle_pct,
            ),
        }
        for key, group_rows in sorted(groups.items())
    ]


def _quartile_stats(
    rows: Sequence[Mapping[str, Any]],
    *,
    metric_key: str,
    group_key: str,
    horizons: Sequence[int],
    round_trip_fee_hurdle_pct: float,
) -> list[dict[str, Any]]:
    scored = [
        dict(row) for row in rows if _optional_float(row.get(metric_key)) is not None
    ]
    scored.sort(key=lambda row: float(row[metric_key]))
    if not scored:
        return []

    labels = ("q1_weakest", "q2", "q3", "q4_strongest")
    buckets: dict[str, list[Mapping[str, Any]]] = {label: [] for label in labels}
    total = len(scored)
    for index, row in enumerate(scored):
        bucket_index = min(3, int(index * 4 / total))
        buckets[labels[bucket_index]].append(row)

    output: list[dict[str, Any]] = []
    for label in labels:
        bucket_rows = buckets[label]
        if not bucket_rows:
            continue
        values = [float(row[metric_key]) for row in bucket_rows]
        output.append(
            {
                group_key: label,
                "metric": metric_key,
                "min_value": min(values),
                "max_value": max(values),
                "total_signals": len(bucket_rows),
                "forward_stats": _stats_payload(
                    bucket_rows,
                    horizons,
                    round_trip_fee_hurdle_pct,
                ),
            }
        )
    return output


def _strongest_vs_weakest(
    quartile_rows: Sequence[Mapping[str, Any]],
    *,
    primary_horizon: int,
) -> dict[str, Any]:
    by_bucket = {str(row.get("trend_strength_quartile")): row for row in quartile_rows}
    weakest = by_bucket.get("q1_weakest")
    strongest = by_bucket.get("q4_strongest")
    horizon_key = str(primary_horizon)
    weakest_mean = _nested_mean(weakest, horizon_key)
    strongest_mean = _nested_mean(strongest, horizon_key)
    return {
        "primary_horizon_bars": primary_horizon,
        "weakest_mean_return_pct": weakest_mean,
        "strongest_mean_return_pct": strongest_mean,
        "strongest_minus_weakest_mean_return_pct": (
            None
            if weakest_mean is None or strongest_mean is None
            else strongest_mean - weakest_mean
        ),
    }


def _nested_mean(row: Mapping[str, Any] | None, horizon_key: str) -> float | None:
    if row is None:
        return None
    stats = row.get("forward_stats") or {}
    if not isinstance(stats, Mapping):
        return None
    horizon_stats = stats.get(horizon_key) or {}
    if not isinstance(horizon_stats, Mapping):
        return None
    value = horizon_stats.get("mean_return_pct")
    return None if value is None else float(value)


def _assess_signal_quality(
    rows: Sequence[Mapping[str, Any]],
    *,
    overall: Mapping[str, Any],
    strongest_vs_weakest: Mapping[str, Any],
    primary_horizon: int,
    round_trip_fee_hurdle_pct: float,
) -> dict[str, Any]:
    reasons: list[str] = []
    horizon_stats = overall.get(str(primary_horizon)) or {}
    sample_count = int(horizon_stats.get("sample_count", 0) or 0)
    mean_return = horizon_stats.get("mean_return_pct")
    median_return = horizon_stats.get("median_return_pct")
    hit_rate = horizon_stats.get("hit_rate")
    strength_delta = strongest_vs_weakest.get("strongest_minus_weakest_mean_return_pct")

    if not rows:
        return {
            "status": "no_signals",
            "status_note": "No trend_core long entry/increase signals were produced.",
            "promotion_ready": False,
            "gate_reasons": ["no_signals"],
        }
    if sample_count < 30:
        reasons.append(
            f"primary horizon has only {sample_count} forward-return samples"
        )
    if mean_return is None or float(mean_return) <= round_trip_fee_hurdle_pct:
        reasons.append("mean forward return does not clear the round-trip fee hurdle")
    if median_return is None or float(median_return) <= 0.0:
        reasons.append("median forward return is not positive")
    if hit_rate is None or float(hit_rate) < 0.55:
        reasons.append("hit rate is below 55%")
    if strength_delta is None or float(strength_delta) <= 0.0:
        reasons.append("stronger trend-strength bucket does not outperform weakest")

    if reasons:
        return {
            "status": "edge_not_proven",
            "status_note": "; ".join(reasons),
            "promotion_ready": False,
            "gate_reasons": reasons,
        }

    return {
        "status": "candidate_signal",
        "status_note": "Primary horizon clears the initial signal-quality checks.",
        "promotion_ready": True,
        "gate_reasons": [],
    }


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
