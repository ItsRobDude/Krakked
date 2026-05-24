"""Walk-forward evaluation for ML strategies on cached OHLC data."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Iterable, Optional

from krakked import APP_VERSION
from krakked.backtest.runner import (
    BacktestMarketData,
    BacktestPortfolioService,
    _configured_backtest_pairs,
    _timeframe_seconds,
)
from krakked.config import AppConfig
from krakked.strategy.engine import StrategyEngine
from krakked.strategy.ml_labels import (
    FEE_ADJUSTED_EDGE_PREDICTION_TARGET,
    NO_POSITIVE_EDGE_PREDICTION,
    POSITIVE_EDGE_PREDICTION,
)
from krakked.strategy.models import StrategyIntent

ML_STRATEGY_TYPES = {
    "machine_learning",
    "machine_learning_alt",
    "machine_learning_regression",
}
EVALUATION_MODE = "rolling_window_isolated"


@dataclass
class MLWalkForwardPrediction:
    fold_index: int
    generated_at: datetime
    strategy_id: str
    pair: str
    timeframe: str
    side: str
    intent_type: str
    confidence: float
    prediction_target: str
    predicted_positive_edge: bool
    predicted_direction: Optional[str]
    current_close: float
    future_close: float
    realized_return: float
    round_trip_cost_pct: float
    directional_correct: Optional[bool]
    fee_adjusted_correct: bool
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fold_index": self.fold_index,
            "generated_at": self.generated_at.astimezone(UTC).isoformat(),
            "strategy_id": self.strategy_id,
            "pair": self.pair,
            "timeframe": self.timeframe,
            "side": self.side,
            "intent_type": self.intent_type,
            "confidence": self.confidence,
            "prediction_target": self.prediction_target,
            "predicted_positive_edge": self.predicted_positive_edge,
            "predicted_direction": self.predicted_direction,
            "current_close": self.current_close,
            "future_close": self.future_close,
            "realized_return": self.realized_return,
            "round_trip_cost_pct": self.round_trip_cost_pct,
            "directional_correct": self.directional_correct,
            "fee_adjusted_correct": self.fee_adjusted_correct,
            "metadata": copy.deepcopy(self.metadata),
        }


@dataclass
class MLWalkForwardFold:
    fold_index: int
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime
    train_cycles: int
    test_cycles: int
    predictions: list[MLWalkForwardPrediction] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fold_index": self.fold_index,
            "train_start": self.train_start.astimezone(UTC).isoformat(),
            "train_end": self.train_end.astimezone(UTC).isoformat(),
            "test_start": self.test_start.astimezone(UTC).isoformat(),
            "test_end": self.test_end.astimezone(UTC).isoformat(),
            "train_cycles": self.train_cycles,
            "test_cycles": self.test_cycles,
            "prediction_count": len(self.predictions),
            "metrics": _build_prediction_metrics(self.predictions),
            "confidence_buckets": _build_confidence_buckets(self.predictions),
        }


@dataclass
class MLWalkForwardSummary:
    start: datetime
    end: datetime
    strategy_id: str
    timeframe: str
    train_bars: int
    test_bars: int
    folds: list[MLWalkForwardFold]
    fee_bps: float
    slippage_bps: float
    pairs: list[str]
    coverage_status: str
    warnings: list[str]
    evaluation_mode: str = EVALUATION_MODE
    model_state_reused_across_folds: bool = False

    def to_dict(self) -> dict[str, Any]:
        predictions = [
            prediction for fold in self.folds for prediction in fold.predictions
        ]
        metrics = _build_prediction_metrics(predictions)
        promotable, promotable_reasons = _assess_promotability(metrics)
        return {
            "start": self.start.astimezone(UTC).isoformat(),
            "end": self.end.astimezone(UTC).isoformat(),
            "strategy_id": self.strategy_id,
            "timeframe": self.timeframe,
            "train_bars": self.train_bars,
            "test_bars": self.test_bars,
            "evaluation_mode": self.evaluation_mode,
            "model_state_reused_across_folds": self.model_state_reused_across_folds,
            "fold_count": len(self.folds),
            "pairs": list(self.pairs),
            "fee_bps": self.fee_bps,
            "slippage_bps": self.slippage_bps,
            "round_trip_cost_bps": _round_trip_cost_bps(
                fee_bps=self.fee_bps, slippage_bps=self.slippage_bps
            ),
            "coverage_status": self.coverage_status,
            "warnings": list(self.warnings),
            "metrics": metrics,
            "confidence_buckets": _build_confidence_buckets(predictions),
            "promotable": promotable,
            "promotable_reasons": promotable_reasons,
            "folds": [fold.to_dict() for fold in self.folds],
        }


@dataclass
class MLWalkForwardResult:
    summary: MLWalkForwardSummary

    def to_report_dict(self) -> dict[str, Any]:
        return {
            "report_version": 2,
            "generated_at": datetime.now(UTC).isoformat(),
            "summary": self.summary.to_dict(),
            "provenance": {
                "app_version": APP_VERSION,
                "generated_by": "krakked ml-walk-forward",
            },
        }


def _round_trip_cost_bps(*, fee_bps: float, slippage_bps: float) -> float:
    return 2.0 * (max(float(fee_bps), 0.0) + max(float(slippage_bps), 0.0))


def _round_trip_cost_pct(*, fee_bps: float, slippage_bps: float) -> float:
    return _round_trip_cost_bps(fee_bps=fee_bps, slippage_bps=slippage_bps) / 10_000.0


def _build_walk_forward_folds(
    timestamps: list[int], *, train_bars: int, test_bars: int
) -> list[tuple[list[int], list[int]]]:
    if train_bars <= 0:
        raise ValueError("train_bars must be greater than 0")
    if test_bars <= 0:
        raise ValueError("test_bars must be greater than 0")

    folds: list[tuple[list[int], list[int]]] = []
    start_index = 0
    while start_index + train_bars + test_bars <= len(timestamps):
        train = timestamps[start_index : start_index + train_bars]
        test = timestamps[
            start_index + train_bars : start_index + train_bars + test_bars
        ]
        folds.append((train, test))
        start_index += test_bars
    return folds


def _set_strategy_learning(config: AppConfig, strategy_id: str, enabled: bool) -> None:
    strat_cfg = config.strategies.configs[strategy_id]
    params = dict(strat_cfg.params or {})
    params["continuous_learning"] = bool(enabled)
    strat_cfg.params = params


def _prepare_ml_config(
    config: AppConfig, *, strategy_id: str, timeframe: str, fee_bps: float
) -> AppConfig:
    config_copy = copy.deepcopy(config)
    if strategy_id not in config_copy.strategies.configs:
        raise ValueError(f"Unknown strategy: {strategy_id}")

    strat_cfg = config_copy.strategies.configs[strategy_id]
    if strat_cfg.type not in ML_STRATEGY_TYPES:
        raise ValueError(f"Strategy {strategy_id} is not an ML strategy")

    config_copy.execution.mode = "simulation"
    config_copy.execution.validate_only = False
    config_copy.execution.allow_live_trading = False
    config_copy.execution.max_plan_age_seconds = 0
    config_copy.ml.enabled = True
    config_copy.strategies.enabled = [strategy_id]
    strat_cfg.enabled = True
    params = dict(strat_cfg.params or {})
    params["timeframe"] = timeframe
    if strat_cfg.type in {"machine_learning", "machine_learning_alt"}:
        params["label_fee_bps"] = float(fee_bps)
    if strat_cfg.type == "machine_learning_regression":
        params["edge_fee_bps"] = float(fee_bps)
    params.pop("timeframes", None)
    strat_cfg.params = params
    return config_copy


def _fold_db_path(base_path: Path, fold_index: int) -> Path:
    suffix = base_path.suffix or ".db"
    return base_path.with_name(f"{base_path.stem}.fold-{fold_index:03d}{suffix}")


def _reset_sqlite_path(db_path: Path) -> None:
    for candidate in (
        db_path,
        Path(str(db_path) + "-wal"),
        Path(str(db_path) + "-shm"),
        Path(str(db_path) + "-journal"),
    ):
        try:
            candidate.unlink(missing_ok=True)
        except PermissionError:
            raise ValueError(f"Cannot reset existing fold database: {candidate}")


def _coerce_bool(value: object) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no"}:
            return False
    return None


def _prediction_target(intent: StrategyIntent) -> str:
    target = intent.metadata.get("prediction_target")
    if isinstance(target, str) and target:
        return target
    if "predicted_delta" in intent.metadata:
        return "signed_return_delta"
    prediction = intent.metadata.get("prediction")
    if prediction in {POSITIVE_EDGE_PREDICTION, NO_POSITIVE_EDGE_PREDICTION}:
        return FEE_ADJUSTED_EDGE_PREDICTION_TARGET
    if prediction in {"up", "down"}:
        return "signed_return_direction"
    return "strategy_intent"


def _predicted_positive_edge(intent: StrategyIntent) -> bool:
    metadata_value = _coerce_bool(intent.metadata.get("predicted_positive_edge"))
    if metadata_value is not None:
        return metadata_value
    prediction = intent.metadata.get("prediction")
    if prediction == POSITIVE_EDGE_PREDICTION:
        return True
    if prediction == NO_POSITIVE_EDGE_PREDICTION:
        return False
    if prediction == "up":
        return True
    if prediction == "down":
        return False
    return intent.side == "long"


def _prediction_direction(intent: StrategyIntent) -> Optional[str]:
    prediction = intent.metadata.get("prediction")
    if prediction == POSITIVE_EDGE_PREDICTION:
        return "up"
    if prediction == NO_POSITIVE_EDGE_PREDICTION:
        return None
    if "predicted_delta" in intent.metadata:
        try:
            predicted_delta = float(intent.metadata["predicted_delta"])
            if predicted_delta > 0:
                return "up"
            if predicted_delta < 0:
                return "down"
            return None
        except (TypeError, ValueError):
            return "up" if intent.side == "long" else None
    if prediction in {"up", "down"}:
        return str(prediction)
    return "up" if intent.side == "long" else None


def _score_intent(
    *,
    fold_index: int,
    intent: StrategyIntent,
    market_data: BacktestMarketData,
    generated_at: datetime,
    fee_bps: float,
    slippage_bps: float,
) -> Optional[MLWalkForwardPrediction]:
    timeframe = intent.timeframe
    current_ts = int(generated_at.timestamp())
    target_ts = current_ts + _timeframe_seconds(timeframe)
    current_bar = market_data.get_bar_at_or_before(intent.pair, timeframe, current_ts)
    future_bar = market_data.get_bar_at_or_after(intent.pair, timeframe, target_ts)
    if current_bar is None or future_bar is None:
        return None

    current_close = float(current_bar.close)
    future_close = float(future_bar.close)
    if current_close <= 0:
        return None

    realized_return = (future_close - current_close) / current_close
    prediction_target = _prediction_target(intent)
    predicted_positive_edge = _predicted_positive_edge(intent)
    predicted_direction = _prediction_direction(intent)
    realized_up = realized_return > 0.0
    round_trip_cost = _round_trip_cost_pct(fee_bps=fee_bps, slippage_bps=slippage_bps)
    tradeable_up = realized_return > round_trip_cost
    directional_correct = None
    if predicted_direction is not None:
        predicted_up = predicted_direction == "up"
        directional_correct = predicted_up == realized_up
    return MLWalkForwardPrediction(
        fold_index=fold_index,
        generated_at=generated_at,
        strategy_id=intent.strategy_id,
        pair=market_data.get_display_pair(intent.pair),
        timeframe=timeframe,
        side=intent.side,
        intent_type=intent.intent_type,
        confidence=float(intent.confidence),
        prediction_target=prediction_target,
        predicted_positive_edge=predicted_positive_edge,
        predicted_direction=predicted_direction,
        current_close=current_close,
        future_close=future_close,
        realized_return=realized_return,
        round_trip_cost_pct=round_trip_cost,
        directional_correct=directional_correct,
        fee_adjusted_correct=predicted_positive_edge == tradeable_up,
        metadata=copy.deepcopy(intent.metadata),
    )


def _rate(successes: int, total: int) -> Optional[float]:
    if total <= 0:
        return None
    return successes / total


def _average(values: Iterable[float]) -> Optional[float]:
    collected = list(values)
    if not collected:
        return None
    return sum(collected) / len(collected)


def _build_prediction_metrics(
    predictions: list[MLWalkForwardPrediction],
) -> dict[str, Any]:
    total = len(predictions)
    positive_edge_predictions = [
        prediction for prediction in predictions if prediction.predicted_positive_edge
    ]
    no_positive_edge_predictions = [
        prediction
        for prediction in predictions
        if not prediction.predicted_positive_edge
    ]
    directional_predictions = [
        prediction
        for prediction in predictions
        if prediction.directional_correct is not None
    ]
    tradeable_long_hits = [
        prediction
        for prediction in positive_edge_predictions
        if prediction.realized_return > prediction.round_trip_cost_pct
    ]
    return {
        "prediction_count": total,
        "positive_edge_prediction_count": len(positive_edge_predictions),
        "no_positive_edge_prediction_count": len(no_positive_edge_predictions),
        "long_prediction_count": len(positive_edge_predictions),
        "flat_prediction_count": len(no_positive_edge_predictions),
        "directional_prediction_count": len(directional_predictions),
        "directional_accuracy": _rate(
            sum(
                1
                for prediction in directional_predictions
                if prediction.directional_correct
            ),
            len(directional_predictions),
        ),
        "edge_prediction_accuracy": _rate(
            sum(1 for prediction in predictions if prediction.fee_adjusted_correct),
            total,
        ),
        "fee_adjusted_hit_rate": _rate(
            sum(1 for prediction in predictions if prediction.fee_adjusted_correct),
            total,
        ),
        "precision_long": _rate(
            len(tradeable_long_hits), len(positive_edge_predictions)
        ),
        "avg_realized_return": _average(
            prediction.realized_return for prediction in predictions
        ),
        "avg_realized_return_when_long": _average(
            prediction.realized_return for prediction in positive_edge_predictions
        ),
        "avg_realized_return_when_flat": _average(
            prediction.realized_return for prediction in no_positive_edge_predictions
        ),
        "avg_confidence": _average(prediction.confidence for prediction in predictions),
    }


def _build_confidence_buckets(
    predictions: list[MLWalkForwardPrediction],
) -> list[dict[str, Any]]:
    ranges = [
        (0.0, 0.5),
        (0.5, 0.6),
        (0.6, 0.7),
        (0.7, 0.8),
        (0.8, 0.9),
        (0.9, 1.000000001),
    ]
    buckets: list[dict[str, Any]] = []
    for lower, upper in ranges:
        bucket_predictions = [
            prediction
            for prediction in predictions
            if lower <= prediction.confidence < upper
        ]
        if not bucket_predictions:
            continue
        display_upper = 1.0 if upper > 1.0 else upper
        total = len(bucket_predictions)
        buckets.append(
            {
                "bucket": f"{lower:.2f}-{display_upper:.2f}",
                "min_confidence": lower,
                "max_confidence": display_upper,
                "prediction_count": total,
                "edge_prediction_accuracy": _rate(
                    sum(
                        1
                        for prediction in bucket_predictions
                        if prediction.fee_adjusted_correct
                    ),
                    total,
                ),
                "directional_accuracy": _rate(
                    sum(
                        1
                        for prediction in bucket_predictions
                        if prediction.directional_correct
                    ),
                    sum(
                        1
                        for prediction in bucket_predictions
                        if prediction.directional_correct is not None
                    ),
                ),
                "fee_adjusted_hit_rate": _rate(
                    sum(
                        1
                        for prediction in bucket_predictions
                        if prediction.fee_adjusted_correct
                    ),
                    total,
                ),
                "avg_realized_return": _average(
                    prediction.realized_return for prediction in bucket_predictions
                ),
            }
        )
    return buckets


def _metric_value(metrics: dict[str, Any], key: str) -> float:
    value = metrics.get(key)
    return float(value) if isinstance(value, (int, float)) else 0.0


def _assess_promotability(metrics: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    prediction_count = int(metrics.get("prediction_count") or 0)
    directional_count = int(metrics.get("directional_prediction_count") or 0)
    if prediction_count < 20:
        reasons.append("Fewer than 20 scored out-of-sample predictions.")
    if int(metrics.get("positive_edge_prediction_count") or 0) <= 0:
        reasons.append("No positive-edge predictions were scored.")
    if (
        directional_count == prediction_count
        and _metric_value(metrics, "directional_accuracy") < 0.52
    ):
        reasons.append("Directional accuracy is below 52%.")
    if _metric_value(metrics, "edge_prediction_accuracy") < 0.50:
        reasons.append("Edge prediction accuracy is below 50%.")
    precision_long = metrics.get("precision_long")
    if precision_long is not None and float(precision_long) < 0.50:
        reasons.append("Long precision is below 50% after estimated costs.")
    if reasons:
        return False, reasons
    return True, ["Walk-forward metrics clear the initial promotion thresholds."]


def run_ml_walk_forward(
    config: AppConfig,
    start: datetime,
    end: datetime,
    *,
    strategy_id: str,
    timeframe: str,
    train_bars: int,
    test_bars: int,
    fee_bps: float = 25.0,
    db_path: Optional[str] = None,
    strict_data: bool = False,
) -> MLWalkForwardResult:
    """Evaluate an ML strategy by training and freezing over rolling windows."""

    if end <= start:
        raise ValueError("Walk-forward end must be after start")
    if fee_bps < 0:
        raise ValueError("fee_bps must be greater than or equal to 0")

    config_copy = _prepare_ml_config(
        config,
        strategy_id=strategy_id,
        timeframe=timeframe,
        fee_bps=fee_bps,
    )
    pairs = _configured_backtest_pairs(config_copy)
    market_data = BacktestMarketData(config_copy, pairs, [timeframe], start, end)
    preflight = market_data.get_preflight()
    if preflight.usable_series_count == 0:
        raise ValueError("No usable OHLC series found for ML walk-forward evaluation")
    if strict_data and (preflight.missing_series or preflight.partial_series):
        details: list[str] = []
        if preflight.missing_series:
            details.append("missing: " + ", ".join(preflight.missing_series))
        if preflight.partial_series:
            details.append("partial: " + ", ".join(preflight.partial_series))
        raise ValueError(
            "Historical data coverage failed in strict mode: " + "; ".join(details)
        )

    timestamps = list(market_data.iter_timestamps())
    fold_ranges = _build_walk_forward_folds(
        timestamps,
        train_bars=train_bars,
        test_bars=test_bars,
    )
    if not fold_ranges:
        raise ValueError(
            "Not enough replay bars to build one walk-forward fold "
            f"({len(timestamps)} available, {train_bars + test_bars} required)."
        )

    temp_dir: Optional[TemporaryDirectory[str]] = None
    if db_path:
        resolved_db_base_path = Path(db_path).expanduser().resolve()
        resolved_db_base_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        temp_dir = TemporaryDirectory(prefix="krakked-ml-walk-forward-")
        resolved_db_base_path = Path(temp_dir.name) / "ml-walk-forward.db"

    folds: list[MLWalkForwardFold] = []
    try:
        for fold_index, (train_timestamps, test_timestamps) in enumerate(
            fold_ranges,
            start=1,
        ):
            fold_config = copy.deepcopy(config_copy)
            fold_db_path = _fold_db_path(resolved_db_base_path, fold_index)
            _reset_sqlite_path(fold_db_path)
            portfolio_service = BacktestPortfolioService(
                fold_config,
                market_data,
                db_path=str(fold_db_path),
                starting_cash_usd=10_000.0,
            )
            try:
                _set_strategy_learning(fold_config, strategy_id, True)
                strategy_engine = StrategyEngine(
                    fold_config, market_data, portfolio_service
                )
                strategy_engine.initialize()

                for ts in train_timestamps:
                    now = datetime.fromtimestamp(ts, tz=UTC)
                    market_data.set_time(now)
                    strategy_engine.run_cycle(now=now)

                predictions: list[MLWalkForwardPrediction] = []
                _set_strategy_learning(fold_config, strategy_id, False)
                for ts in test_timestamps:
                    now = datetime.fromtimestamp(ts, tz=UTC)
                    market_data.set_time(now)
                    strategy_engine.run_cycle(now=now)
                    intents = [
                        intent
                        for intent in strategy_engine.last_cycle_intents
                        if intent.strategy_id == strategy_id
                        and intent.timeframe == timeframe
                    ]
                    for intent in intents:
                        scored = _score_intent(
                            fold_index=fold_index,
                            intent=intent,
                            market_data=market_data,
                            generated_at=now,
                            fee_bps=fee_bps,
                            slippage_bps=float(fold_config.execution.max_slippage_bps),
                        )
                        if scored is not None:
                            predictions.append(scored)

                folds.append(
                    MLWalkForwardFold(
                        fold_index=fold_index,
                        train_start=datetime.fromtimestamp(train_timestamps[0], tz=UTC),
                        train_end=datetime.fromtimestamp(train_timestamps[-1], tz=UTC),
                        test_start=datetime.fromtimestamp(test_timestamps[0], tz=UTC),
                        test_end=datetime.fromtimestamp(test_timestamps[-1], tz=UTC),
                        train_cycles=len(train_timestamps),
                        test_cycles=len(test_timestamps),
                        predictions=predictions,
                    )
                )
            finally:
                close_store = getattr(portfolio_service.store, "close", None)
                if callable(close_store):
                    close_store()
    finally:
        shutdown = getattr(market_data, "shutdown", None)
        if callable(shutdown):
            shutdown()
        if temp_dir is not None:
            temp_dir.cleanup()

    display_pairs = [
        pair_meta.ws_symbol for pair_meta in market_data.get_universe_metadata()
    ]
    summary = MLWalkForwardSummary(
        start=start,
        end=end,
        strategy_id=strategy_id,
        timeframe=timeframe,
        train_bars=train_bars,
        test_bars=test_bars,
        folds=folds,
        fee_bps=float(fee_bps),
        slippage_bps=float(config_copy.execution.max_slippage_bps),
        pairs=display_pairs,
        coverage_status=preflight.status,
        warnings=list(preflight.warnings),
    )
    return MLWalkForwardResult(summary=summary)


__all__ = [
    "MLWalkForwardFold",
    "MLWalkForwardPrediction",
    "MLWalkForwardResult",
    "MLWalkForwardSummary",
    "run_ml_walk_forward",
]
