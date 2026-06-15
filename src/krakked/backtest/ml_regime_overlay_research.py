"""Research-only ML exposure-scale overlay for market-regime targets."""

from __future__ import annotations

import copy
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from statistics import mean
from typing import Any, Mapping, Sequence

from krakked.config import AppConfig
from krakked.market_regime import (
    MarketRegimeOverlayParams,
    MarketRegimeSnapshot,
    _as_utc,
    _clean_pairs,
    _default_pairs,
    classify_market_regime_snapshot,
)
from krakked.strategy.ml_models import (
    MLOnlineModelBundle,
    PassiveAggressiveClassifier,
    StandardScaler,
)

from .evidence_windows import (
    REQUIRED_REGIME_BUCKETS,
    build_evidence_window_context,
    context_by_window_key,
    parse_evidence_datetime,
    summarize_regime_coverage,
)
from .market_regime_exposure import (
    MarketRegimeExposureScenarioParams,
    _common_timeline,
    _execute_plan,
    _max_drawdown_pct,
    _portfolio_equity,
    _portfolio_exposure,
    _price_maps,
    _scenario_target_weights,
    _ScenarioPortfolio,
    _target_plan,
    evaluate_market_regime_exposure_scenarios,
)
from .market_regime_overlay import _preflight_to_dict, _sort_bars, _strict_data_message
from .runner import BacktestMarketData

REPORT_TYPE = "ml_regime_overlay_research"
REPORT_VERSION = 1
SCALE_VALUES = (0.25, 0.75, 1.0)
SCALE_CLASSES = (0, 1, 2)
SCALE_BY_CLASS = dict(zip(SCALE_CLASSES, SCALE_VALUES))
CLASS_BY_SCALE = {value: label for label, value in SCALE_BY_CLASS.items()}
MODEL_RANDOM_STATE = 42
FEATURE_NAMES = (
    "benchmark_momentum_bps",
    "benchmark_drawdown_pct",
    "benchmark_volatility_pct",
    "basket_momentum_bps",
    "basket_drawdown_pct",
    "basket_volatility_pct",
    "top_momentum_bps",
    "second_momentum_bps",
    "momentum_spread_bps",
    "selected_pair_count",
    "previous_scale",
)
REQUIRED_PASSING_REGIME_BUCKETS = (len(REQUIRED_REGIME_BUCKETS) // 2) + 1


@dataclass(frozen=True)
class MLRegimeOverlayResearchParams:
    allocation_pct: float = 20.0
    starting_cash_usd: float = 10_000.0
    fee_bps: float = 25.0
    rebalance_interval_bars: int = 6
    target_lookback_bars: int = 63
    max_target_pairs: int = 2
    min_training_examples: int = 20

    def __post_init__(self) -> None:
        if self.allocation_pct <= 0.0 or self.allocation_pct > 100.0:
            raise ValueError("allocation_pct must be greater than 0 and at most 100")
        if self.starting_cash_usd <= 0.0:
            raise ValueError("starting_cash_usd must be greater than 0")
        if self.fee_bps < 0.0:
            raise ValueError("fee_bps must be greater than or equal to 0")
        if int(self.rebalance_interval_bars) < 1:
            raise ValueError("rebalance_interval_bars must be at least 1")
        if int(self.target_lookback_bars) < 2:
            raise ValueError("target_lookback_bars must be at least 2")
        if int(self.max_target_pairs) < 1:
            raise ValueError("max_target_pairs must be at least 1")
        if int(self.min_training_examples) < 1:
            raise ValueError("min_training_examples must be at least 1")


@dataclass(frozen=True)
class _WindowSpec:
    window_set: str
    window_id: str
    start: datetime
    end: datetime


@dataclass
class MLRegimeOverlayResearchResult:
    generated_at: datetime
    summary: dict[str, Any]
    windows: list[dict[str, Any]]

    def to_report_dict(self) -> dict[str, Any]:
        return {
            "report_version": REPORT_VERSION,
            "report_type": REPORT_TYPE,
            "generated_at": self.generated_at.astimezone(UTC).isoformat(),
            "summary": copy.deepcopy(self.summary),
            "windows": copy.deepcopy(self.windows),
        }


def run_ml_regime_overlay_research(
    config: AppConfig,
    *,
    window_sets: Mapping[str, Sequence[tuple[str, str, str]]],
    pairs: Sequence[str] | None = None,
    timeframe: str = "4h",
    params: MLRegimeOverlayResearchParams | None = None,
    strict_data: bool = False,
) -> MLRegimeOverlayResearchResult:
    params = params or MLRegimeOverlayResearchParams()
    regime_params = _default_regime_params(timeframe)
    selected_pairs = _clean_pairs(list(pairs or _default_pairs(config)))
    if regime_params.benchmark_pair not in selected_pairs:
        selected_pairs.insert(0, regime_params.benchmark_pair)

    # Compute the per-window market context (benchmark/basket returns, drawdown,
    # and the resolved regime bucket) so the report self-evidences whether the
    # evaluated windows actually span multiple regimes instead of trusting the
    # window-set name. Re-uses the shared, tested evidence-window machinery.
    context_map = context_by_window_key(
        build_evidence_window_context(
            config,
            window_sets=window_sets,
            pairs=selected_pairs,
            timeframe=timeframe,
            regime_params=regime_params,
        )
    )

    training_examples: list[dict[str, Any]] = []
    window_reports: list[dict[str, Any]] = []
    for spec in _chronological_window_specs(window_sets):
        bars_by_pair, preflight = _load_window_bars(
            config,
            start=spec.start,
            end=spec.end,
            pairs=selected_pairs,
            timeframe=timeframe,
            strict_data=strict_data,
        )
        scenario_params = _scenario_params(params)
        controlled = evaluate_market_regime_exposure_scenarios(
            bars_by_pair,
            start=spec.start,
            end=spec.end,
            pairs=selected_pairs,
            regime_params=regime_params,
            scenario_params=scenario_params,
            scenarios=["trend_rank_proxy"],
            overlay_modes=["target_scale"],
            preflight=preflight,
        )
        baseline_run = next(
            run for run in controlled.runs if run["overlay_mode"] == "none"
        )
        handcoded_run = next(
            run for run in controlled.runs if run["overlay_mode"] == "target_scale"
        )
        eligible_training_examples = _examples_with_labels_before(
            training_examples,
            cutoff=spec.start,
        )
        model = _fit_model(eligible_training_examples, params=params)
        ml_run: dict[str, Any] | None = None
        status = "ready"
        if model is None:
            status = "insufficient_training"
        else:
            ml_run = _simulate_ml_scale_overlay(
                bars_by_pair,
                start=spec.start,
                end=spec.end,
                pairs=selected_pairs,
                regime_params=regime_params,
                scenario_params=scenario_params,
                model=model,
            )
        examples = _build_training_examples(
            bars_by_pair,
            start=spec.start,
            end=spec.end,
            pairs=selected_pairs,
            regime_params=regime_params,
            scenario_params=scenario_params,
        )
        training_examples.extend(examples)
        report = _window_report(
            window_set=spec.window_set,
            window_id=spec.window_id,
            start=spec.start,
            end=spec.end,
            preflight=preflight,
            training_examples_available=len(training_examples) - len(examples),
            training_examples_used=len(eligible_training_examples),
            training_examples_added=len(examples),
            status=status,
            baseline_run=baseline_run,
            handcoded_run=handcoded_run,
            ml_run=ml_run,
        )
        context = context_map.get((spec.window_set, spec.window_id))
        if context:
            report["market_bucket"] = context.get("market_bucket")
            report["evidence_bucket"] = context.get("evidence_bucket")
            report["benchmark_return_pct"] = context.get("benchmark_return_pct")
            report["basket_return_pct"] = context.get("basket_return_pct")
            report["benchmark_max_drawdown_pct"] = context.get(
                "benchmark_max_drawdown_pct"
            )
        window_reports.append(report)

    summary = _summary(window_reports, params=params, timeframe=timeframe)
    return MLRegimeOverlayResearchResult(
        generated_at=datetime.now(UTC),
        summary=summary,
        windows=window_reports,
    )


def _chronological_window_specs(
    window_sets: Mapping[str, Sequence[tuple[str, str, str]]],
) -> list[_WindowSpec]:
    specs: list[_WindowSpec] = []
    for window_set, windows in window_sets.items():
        for window_id, start_text, end_text in windows:
            specs.append(
                _WindowSpec(
                    window_set=window_set,
                    window_id=window_id,
                    start=parse_evidence_datetime(start_text),
                    end=parse_evidence_datetime(end_text),
                )
            )
    return sorted(
        specs,
        key=lambda spec: (
            spec.start,
            spec.end,
            spec.window_set,
            spec.window_id,
        ),
    )


def _examples_with_labels_before(
    examples: Sequence[Mapping[str, Any]],
    *,
    cutoff: datetime,
) -> list[Mapping[str, Any]]:
    cutoff_ts = int(cutoff.astimezone(UTC).timestamp())
    eligible: list[Mapping[str, Any]] = []
    for example in examples:
        value = example.get("label_end_timestamp")
        if value is not None and int(value) <= cutoff_ts:
            eligible.append(example)
    return eligible


def _load_window_bars(
    config: AppConfig,
    *,
    start: datetime,
    end: datetime,
    pairs: Sequence[str],
    timeframe: str,
    strict_data: bool,
) -> tuple[dict[str, list[Any]], dict[str, Any]]:
    market_data = BacktestMarketData(config, pairs, [timeframe], start, end)
    try:
        preflight = market_data.get_preflight()
        if strict_data and (preflight.missing_series or preflight.partial_series):
            raise ValueError(
                _strict_data_message("ML regime overlay research", preflight)
            )
        market_data.set_time(end)
        bars_by_pair = {
            pair: market_data.get_ohlc(pair, timeframe, lookback=1_000_000)
            for pair in pairs
        }
        return bars_by_pair, _preflight_to_dict(preflight)
    finally:
        shutdown = getattr(market_data, "shutdown", None)
        if callable(shutdown):
            shutdown()


def _fit_model(
    examples: Sequence[Mapping[str, Any]],
    *,
    params: MLRegimeOverlayResearchParams,
) -> MLOnlineModelBundle | None:
    if len(examples) < int(params.min_training_examples):
        return None
    rows = [list(example["features"]) for example in examples]
    labels = [int(example["label"]) for example in examples]
    model = MLOnlineModelBundle(
        model=PassiveAggressiveClassifier(
            max_iter=1000,
            tol=1e-3,
            random_state=MODEL_RANDOM_STATE,
        ),
        scaler=StandardScaler(),
    )
    model.partial_fit(rows, labels, classes=list(SCALE_CLASSES))
    return model


def _build_training_examples(
    bars_by_pair: Mapping[str, Sequence[Any]],
    *,
    start: datetime,
    end: datetime,
    pairs: Sequence[str],
    regime_params: MarketRegimeOverlayParams,
    scenario_params: MarketRegimeExposureScenarioParams,
) -> list[dict[str, Any]]:
    cleaned = {pair: _sort_bars(bars) for pair, bars in bars_by_pair.items()}
    price_maps = _price_maps(cleaned)
    timeline = _common_timeline(
        price_maps, pairs=pairs, start=_as_utc(start), end=_as_utc(end)
    )
    snapshots = {
        ts: classify_market_regime_snapshot(cleaned, timestamp=ts, params=regime_params)
        for ts in timeline
    }
    examples: list[dict[str, Any]] = []
    previous_scale = 1.0
    interval = int(scenario_params.rebalance_interval_bars)
    for index in range(0, max(len(timeline) - interval, 0), interval):
        next_index = min(index + interval, len(timeline) - 1)
        target_weights = _scenario_target_weights(
            "trend_rank_proxy",
            target_pairs=pairs,
            price_maps=price_maps,
            timeline=timeline,
            index=index,
            scenario_params=scenario_params,
        )
        if not target_weights:
            continue
        features = _feature_row(
            snapshots[timeline[index]],
            target_pairs=list(target_weights),
            price_maps=price_maps,
            timeline=timeline,
            index=index,
            previous_scale=previous_scale,
            scenario_params=scenario_params,
        )
        label, scale = _best_scale_label(
            target_weights,
            price_maps=price_maps,
            timeline=timeline,
            index=index,
            next_index=next_index,
            fee_bps=float(scenario_params.fee_bps),
        )
        previous_scale = scale
        examples.append(
            {
                "timestamp": timeline[index],
                "features": features,
                "label": label,
                "scale": scale,
                "label_end_timestamp": timeline[next_index],
            }
        )
    return examples


def _simulate_ml_scale_overlay(
    bars_by_pair: Mapping[str, Sequence[Any]],
    *,
    start: datetime,
    end: datetime,
    pairs: Sequence[str],
    regime_params: MarketRegimeOverlayParams,
    scenario_params: MarketRegimeExposureScenarioParams,
    model: MLOnlineModelBundle,
) -> dict[str, Any]:
    cleaned = {pair: _sort_bars(bars) for pair, bars in bars_by_pair.items()}
    price_maps = _price_maps(cleaned)
    timeline = _common_timeline(
        price_maps, pairs=pairs, start=_as_utc(start), end=_as_utc(end)
    )
    snapshots = {
        ts: classify_market_regime_snapshot(cleaned, timestamp=ts, params=regime_params)
        for ts in timeline
    }
    portfolio = _ScenarioPortfolio(
        cash_usd=float(scenario_params.starting_cash_usd),
        holdings={pair: 0.0 for pair in pairs},
    )
    equity_curve: list[float] = []
    exposure_curve: list[float] = []
    state_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    scale_counts: Counter[str] = Counter()
    trades = 0
    fees_usd = 0.0
    rebalance_count = 0
    cash_target_rebalances = 0
    previous_scale = 1.0

    for index, timestamp in enumerate(timeline):
        prices = {pair: float(price_maps[pair][timestamp]) for pair in pairs}
        snapshot = snapshots[timestamp]
        state_counts[snapshot.regime] += 1
        reason_counts.update(snapshot.reason_codes)

        if index % int(scenario_params.rebalance_interval_bars) == 0:
            rebalance_count += 1
            equity = _portfolio_equity(portfolio, prices)
            base_weights = _scenario_target_weights(
                "trend_rank_proxy",
                target_pairs=pairs,
                price_maps=price_maps,
                timeline=timeline,
                index=index,
                scenario_params=scenario_params,
            )
            if not base_weights:
                cash_target_rebalances += 1
                scale = 0.0
            else:
                features = _feature_row(
                    snapshot,
                    target_pairs=list(base_weights),
                    price_maps=price_maps,
                    timeline=timeline,
                    index=index,
                    previous_scale=previous_scale,
                    scenario_params=scenario_params,
                )
                scale = _scale_from_prediction(model.predict([features])[0])
            previous_scale = scale if scale > 0.0 else previous_scale
            scale_counts[f"{scale:.2f}"] += 1
            target_weights = {
                pair: weight * scale for pair, weight in base_weights.items()
            }
            plan = _target_plan(
                scenario_id="trend_rank_proxy",
                overlay_mode="ml_scale_overlay",
                timestamp=timestamp,
                portfolio=portfolio,
                prices=prices,
                target_weights=target_weights,
                equity_usd=equity,
            )
            executed = _execute_plan(
                portfolio,
                plan,
                prices,
                fee_bps=float(scenario_params.fee_bps),
            )
            trades += executed["trades"]
            fees_usd += executed["fees_usd"]

        equity = _portfolio_equity(portfolio, prices)
        exposure = _portfolio_exposure(portfolio, prices)
        equity_curve.append(equity)
        exposure_curve.append((exposure / equity) * 100.0 if equity > 0.0 else 0.0)

    ending_equity = equity_curve[-1]
    active_cycles = sum(1 for exposure in exposure_curve if exposure > 0.01)
    return {
        "scenario_id": "trend_rank_proxy",
        "overlay_mode": "ml_scale_overlay",
        "target_pairs": list(pairs),
        "allocation_pct": scenario_params.allocation_pct,
        "rebalance_interval_bars": scenario_params.rebalance_interval_bars,
        "starting_cash_usd": scenario_params.starting_cash_usd,
        "ending_equity_usd": ending_equity,
        "return_pct": (
            (ending_equity - scenario_params.starting_cash_usd)
            / scenario_params.starting_cash_usd
        )
        * 100.0,
        "max_drawdown_pct": _max_drawdown_pct(equity_curve),
        "trades": trades,
        "fees_usd": fees_usd,
        "rebalance_count": rebalance_count,
        "cash_target_rebalances": cash_target_rebalances,
        "total_cycles": len(equity_curve),
        "active_cycles": active_cycles,
        "cash_cycles": len(equity_curve) - active_cycles,
        "active_cycle_pct": (
            (active_cycles / len(equity_curve)) * 100.0 if equity_curve else 0.0
        ),
        "avg_exposure_pct": mean(exposure_curve) if exposure_curve else 0.0,
        "max_exposure_pct": max(exposure_curve) if exposure_curve else 0.0,
        "state_counts": dict(sorted(state_counts.items())),
        "reason_counts": dict(reason_counts.most_common()),
        "scale_counts": dict(sorted(scale_counts.items())),
    }


def _feature_row(
    snapshot: MarketRegimeSnapshot,
    *,
    target_pairs: Sequence[str],
    price_maps: Mapping[str, Mapping[int, float]],
    timeline: Sequence[int],
    index: int,
    previous_scale: float,
    scenario_params: MarketRegimeExposureScenarioParams,
) -> list[float]:
    benchmark = snapshot.features.get("benchmark") or {}
    basket = snapshot.features.get("basket") or {}
    momentums = sorted(
        (
            _momentum_bps(
                price_maps.get(pair, {}),
                timeline=timeline,
                index=index,
                lookback=int(scenario_params.target_lookback_bars),
            )
            for pair in target_pairs
        ),
        reverse=True,
    )
    top = momentums[0] if momentums else 0.0
    second = momentums[1] if len(momentums) > 1 else 0.0
    return [
        _feature_float(benchmark.get("momentum_bps")) / 10_000.0,
        _feature_float(benchmark.get("drawdown_pct")) / 100.0,
        _feature_float(benchmark.get("volatility_pct")) / 100.0,
        _feature_float(basket.get("momentum_bps")) / 10_000.0,
        _feature_float(basket.get("drawdown_pct")) / 100.0,
        _feature_float(basket.get("volatility_pct")) / 100.0,
        top / 10_000.0,
        second / 10_000.0,
        (top - second) / 10_000.0,
        float(len(target_pairs)) / max(float(scenario_params.max_target_pairs), 1.0),
        float(previous_scale),
    ]


def _best_scale_label(
    target_weights: Mapping[str, float],
    *,
    price_maps: Mapping[str, Mapping[int, float]],
    timeline: Sequence[int],
    index: int,
    next_index: int,
    fee_bps: float,
) -> tuple[int, float]:
    best_scale = SCALE_VALUES[0]
    best_score = float("-inf")
    for scale in SCALE_VALUES:
        score = _scale_score(
            target_weights,
            price_maps=price_maps,
            timeline=timeline,
            index=index,
            next_index=next_index,
            scale=scale,
            fee_bps=fee_bps,
        )
        if score > best_score or (
            abs(score - best_score) <= 1e-12 and scale < best_scale
        ):
            best_score = score
            best_scale = scale
    return CLASS_BY_SCALE[best_scale], best_scale


def _scale_score(
    target_weights: Mapping[str, float],
    *,
    price_maps: Mapping[str, Mapping[int, float]],
    timeline: Sequence[int],
    index: int,
    next_index: int,
    scale: float,
    fee_bps: float,
) -> float:
    gross_return = 0.0
    exposure = 0.0
    for pair, weight in target_weights.items():
        start_price = float(price_maps[pair][timeline[index]])
        end_price = float(price_maps[pair][timeline[next_index]])
        if start_price <= 0.0 or end_price <= 0.0:
            continue
        scaled_weight = float(weight) * float(scale)
        exposure += abs(scaled_weight)
        gross_return += scaled_weight * ((end_price - start_price) / start_price)
    round_trip_cost = exposure * (float(fee_bps) / 10_000.0) * 2.0
    net_return = gross_return - round_trip_cost
    drawdown_penalty = max(-net_return, 0.0) * 0.5
    return net_return - drawdown_penalty


def _momentum_bps(
    price_map: Mapping[int, float],
    *,
    timeline: Sequence[int],
    index: int,
    lookback: int,
) -> float:
    actual_lookback = min(int(lookback), index + 1)
    if actual_lookback < 2:
        return 0.0
    start_ts = timeline[index - actual_lookback + 1]
    end_ts = timeline[index]
    start_price = float(price_map.get(start_ts, 0.0) or 0.0)
    end_price = float(price_map.get(end_ts, 0.0) or 0.0)
    if start_price <= 0.0 or end_price <= 0.0:
        return 0.0
    return ((end_price - start_price) / start_price) * 10_000.0


def _scale_from_prediction(value: Any) -> float:
    try:
        label = int(round(float(value)))
    except (TypeError, ValueError):
        label = 1
    if label == 0:
        return SCALE_BY_CLASS[0]
    if label == 2:
        return SCALE_BY_CLASS[2]
    return SCALE_BY_CLASS[1]


def _feature_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _window_report(
    *,
    window_set: str,
    window_id: str,
    start: datetime,
    end: datetime,
    preflight: Mapping[str, Any],
    training_examples_available: int,
    training_examples_used: int,
    training_examples_added: int,
    status: str,
    baseline_run: Mapping[str, Any],
    handcoded_run: Mapping[str, Any],
    ml_run: Mapping[str, Any] | None,
) -> dict[str, Any]:
    report = {
        "window_set": window_set,
        "window_id": window_id,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "status": status,
        "strict_data_ready": not (
            preflight.get("missing_series") or preflight.get("partial_series")
        ),
        "training_examples_before": training_examples_available,
        "training_examples_used": training_examples_used,
        "training_examples_excluded_overlap": (
            training_examples_available - training_examples_used
        ),
        "training_examples_added": training_examples_added,
        "rows": {
            "no_overlay": _run_slice(baseline_run),
            "handcoded_top2_soft_target_scale": _run_slice(handcoded_run),
            "ml_scale_overlay": _run_slice(ml_run) if ml_run else None,
        },
    }
    if ml_run is not None:
        report["comparisons"] = {
            "ml_vs_handcoded": _delta(_run_slice(handcoded_run), _run_slice(ml_run)),
            "handcoded_vs_no_overlay": _delta(
                _run_slice(baseline_run), _run_slice(handcoded_run)
            ),
        }
    return report


def _run_slice(run: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if run is None:
        return None
    return {
        "return_pct": run["return_pct"],
        "max_drawdown_pct": run["max_drawdown_pct"],
        "trades": run["trades"],
        "fees_usd": run["fees_usd"],
        "active_cycle_pct": run["active_cycle_pct"],
        "avg_exposure_pct": run["avg_exposure_pct"],
    }


def _delta(
    baseline: Mapping[str, Any] | None,
    candidate: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if baseline is None or candidate is None:
        return None
    return {
        "delta_return_pct": float(candidate["return_pct"])
        - float(baseline["return_pct"]),
        "delta_max_drawdown_pct": float(candidate["max_drawdown_pct"])
        - float(baseline["max_drawdown_pct"]),
        "delta_avg_exposure_pct": float(candidate["avg_exposure_pct"])
        - float(baseline["avg_exposure_pct"]),
    }


def _summary(
    windows: Sequence[Mapping[str, Any]],
    *,
    params: MLRegimeOverlayResearchParams,
    timeframe: str,
) -> dict[str, Any]:
    promotion = summarize_ml_overlay_promotion_rows(
        [_overlay_promotion_row(window) for window in windows]
    )
    return {
        "research_only": True,
        "runtime_wiring_approved": False,
        "baseline_profile": "top2_soft_target_scale",
        "model_family": "passive_aggressive_classifier",
        "model_random_state": MODEL_RANDOM_STATE,
        "prediction_target": "exposure_scale_class",
        "scale_values": list(SCALE_VALUES),
        "feature_names": list(FEATURE_NAMES),
        "timeframe": timeframe,
        "params": {
            "allocation_pct": params.allocation_pct,
            "starting_cash_usd": params.starting_cash_usd,
            "fee_bps": params.fee_bps,
            "rebalance_interval_bars": params.rebalance_interval_bars,
            "target_lookback_bars": params.target_lookback_bars,
            "max_target_pairs": params.max_target_pairs,
            "min_training_examples": params.min_training_examples,
        },
        "window_count": len(windows),
        **promotion,
    }


def _overlay_promotion_row(window: Mapping[str, Any]) -> dict[str, Any]:
    comparison = (window.get("comparisons") or {}).get("ml_vs_handcoded")
    rows = window.get("rows") or {}
    ml_row = rows.get("ml_scale_overlay") or {}
    handcoded_row = rows.get("handcoded_top2_soft_target_scale") or {}
    return {
        "strict_data_ready": bool(window.get("strict_data_ready")),
        "evidence_bucket": window.get("evidence_bucket"),
        "delta_return_pct": (
            float(comparison["delta_return_pct"]) if comparison is not None else None
        ),
        "delta_max_drawdown_pct": (
            float(comparison["delta_max_drawdown_pct"])
            if comparison is not None
            else None
        ),
        "ml_avg_exposure_pct": _float_or_none(ml_row.get("avg_exposure_pct")),
        "handcoded_avg_exposure_pct": _float_or_none(
            handcoded_row.get("avg_exposure_pct")
        ),
        "training_examples_excluded_overlap": int(
            window.get("training_examples_excluded_overlap", 0) or 0
        ),
    }


def summarize_ml_overlay_promotion_rows(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    ready = [row for row in rows if row.get("delta_return_pct") is not None]
    avg_return_delta = _mean_or_none([float(row["delta_return_pct"]) for row in ready])
    avg_drawdown_delta = _mean_or_none(
        [float(row["delta_max_drawdown_pct"]) for row in ready]
    )
    avg_ml_exposure = _mean_or_none(
        [
            float(row["ml_avg_exposure_pct"])
            for row in rows
            if row.get("ml_avg_exposure_pct") is not None
        ]
    )
    avg_handcoded_exposure = _mean_or_none(
        [
            float(row["handcoded_avg_exposure_pct"])
            for row in rows
            if row.get("handcoded_avg_exposure_pct") is not None
        ]
    )
    min_required_ml_exposure = (
        avg_handcoded_exposure * 0.35 if avg_handcoded_exposure is not None else None
    )
    bucket_counts, regime_coverage_sufficient = summarize_regime_coverage(
        row.get("evidence_bucket") for row in rows
    )
    regime_bucket_results = _regime_bucket_results(ready)
    passing_regime_buckets = [
        bucket
        for bucket, result in regime_bucket_results.items()
        if bool(result["passed"])
    ]
    current_rows = [
        row for row in ready if row.get("evidence_bucket") == "current_rolling"
    ]
    current_rolling_window_count = sum(
        1 for row in rows if row.get("evidence_bucket") == "current_rolling"
    )
    current_rolling_present = current_rolling_window_count > 0
    current_rolling_evaluable = bool(current_rows)
    current_rolling_not_worse = bool(current_rows) and all(
        float(row["delta_return_pct"]) >= 0.0
        and float(row["delta_max_drawdown_pct"]) <= 0.0
        for row in current_rows
    )
    gate = {
        "research_flags": True,
        "strict_data_ready": all(bool(row.get("strict_data_ready")) for row in rows),
        "has_ml_windows": bool(ready),
        "regime_coverage_sufficient": regime_coverage_sufficient,
        "current_rolling_present": current_rolling_present,
        "current_rolling_evaluable": current_rolling_evaluable,
        "regime_bucket_return_drawdown": (
            len(passing_regime_buckets) >= REQUIRED_PASSING_REGIME_BUCKETS
        ),
        "current_rolling_not_worse": current_rolling_not_worse,
        "beats_handcoded_return": (
            avg_return_delta is not None and avg_return_delta > 0.0
        ),
        "beats_handcoded_drawdown": (
            avg_drawdown_delta is not None and avg_drawdown_delta < 0.0
        ),
        "not_cash_only": (
            avg_ml_exposure is not None
            and (
                min_required_ml_exposure is None
                or avg_ml_exposure >= min_required_ml_exposure
            )
        ),
    }
    gate["passed"] = all(gate.values())
    return {
        "ml_ready_windows": len(ready),
        "evidence_bucket_counts": bucket_counts,
        "regime_coverage_sufficient": regime_coverage_sufficient,
        "insufficient_regime_coverage": not regime_coverage_sufficient,
        "required_passing_regime_buckets": REQUIRED_PASSING_REGIME_BUCKETS,
        "passing_regime_buckets": passing_regime_buckets,
        "regime_bucket_results": regime_bucket_results,
        "current_rolling_window_count": current_rolling_window_count,
        "current_rolling_ready_window_count": len(current_rows),
        "current_rolling_present": current_rolling_present,
        "current_rolling_evaluable": current_rolling_evaluable,
        "current_rolling_not_worse": current_rolling_not_worse,
        "training_examples_excluded_overlap": sum(
            int(row.get("training_examples_excluded_overlap", 0) or 0) for row in rows
        ),
        "avg_ml_delta_return_pct": avg_return_delta,
        "avg_ml_delta_max_drawdown_pct": avg_drawdown_delta,
        "avg_ml_exposure_pct": avg_ml_exposure,
        "avg_handcoded_exposure_pct": avg_handcoded_exposure,
        "min_required_ml_exposure_pct": min_required_ml_exposure,
        "promotion_gate": gate,
    }


def _regime_bucket_results(
    ready_rows: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    for bucket in REQUIRED_REGIME_BUCKETS:
        bucket_rows = [
            row for row in ready_rows if row.get("evidence_bucket") == bucket
        ]
        avg_return_delta = _mean_or_none(
            [float(row["delta_return_pct"]) for row in bucket_rows]
        )
        avg_drawdown_delta = _mean_or_none(
            [float(row["delta_max_drawdown_pct"]) for row in bucket_rows]
        )
        results[bucket] = {
            "window_count": len(bucket_rows),
            "avg_delta_return_pct": avg_return_delta,
            "avg_delta_max_drawdown_pct": avg_drawdown_delta,
            "passed": (
                avg_return_delta is not None
                and avg_return_delta > 0.0
                and avg_drawdown_delta is not None
                and avg_drawdown_delta < 0.0
            ),
        }
    return results


def _mean_or_none(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _default_regime_params(timeframe: str) -> MarketRegimeOverlayParams:
    return MarketRegimeOverlayParams(
        timeframe=timeframe,
        neutral_allocation_multiplier=0.75,
        risk_off_allocation_multiplier=0.25,
        momentum_lookback_bars=63,
        basket_momentum_lookback_bars=63,
        volatility_lookback_bars=63,
        drawdown_lookback_bars=63,
    )


def _scenario_params(
    params: MLRegimeOverlayResearchParams,
) -> MarketRegimeExposureScenarioParams:
    return MarketRegimeExposureScenarioParams(
        allocation_pct=float(params.allocation_pct),
        rebalance_interval_bars=int(params.rebalance_interval_bars),
        starting_cash_usd=float(params.starting_cash_usd),
        fee_bps=float(params.fee_bps),
        target_lookback_bars=int(params.target_lookback_bars),
        max_target_pairs=int(params.max_target_pairs),
    )
