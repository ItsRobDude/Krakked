from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple, cast

from krakked.config import StrategyConfig
from krakked.market_data.api import MarketDataAPI
from krakked.portfolio.manager import PortfolioService
from krakked.strategy.base import Strategy, StrategyContext
from krakked.strategy.features import (
    ML_FEATURE_SCHEMA_VERSION,
    MLFeatureVector,
    compute_feature_vector_from_ohlc,
    feature_model_key_suffix,
    feature_names_for_profile,
    normalize_feature_profile,
)
from krakked.strategy.ml_labels import MLEdgeCostConfig, edge_cost_config_from_context
from krakked.strategy.ml_models import (
    DEFAULT_REGRESSION_EPSILON_PCT,
    DEFAULT_REGRESSION_MODEL_BACKEND,
    DEFAULT_SGD_L2_ALPHA,
    DEFAULT_SGD_LEARNING_RATE_INITIAL,
    ML_STANDARD_SCALER_SCHEMA_VERSION,
    MLOnlineModelBundle,
    create_regression_model_bundle,
    is_regression_model_for_backend,
    regression_model_backend,
    regression_model_config_key,
    regression_model_framework,
)
from krakked.strategy.ml_persistence import (
    load_model,
    load_training_checkpoint,
    load_training_window,
    record_example,
    save_model,
    save_training_checkpoint,
)
from krakked.strategy.models import StrategyIntent

logger = logging.getLogger(__name__)

TRAINING_WINDOW_EXAMPLES = 5000


def _nonnegative_float(value: object, default: float) -> float:
    if not isinstance(value, (int, float, str)):
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(parsed):
        return default
    return max(parsed, 0.0)


def _positive_float(value: object, default: float) -> float:
    parsed = _nonnegative_float(value, default)
    return parsed if parsed > 0 else default


@dataclass
class AIRegressionConfig:
    pairs: List[str]
    timeframe: str
    lookback_bars: int
    short_window: int
    long_window: int
    target_exposure_usd: Optional[float]
    continuous_learning: bool
    max_positions: int
    min_edge_pct: float = 0.003
    edge_fee_bps: float = 25.0
    edge_slippage_bps: float = 50.0
    edge_cost_multiplier: float = 1.0
    regression_epsilon_pct: float = DEFAULT_REGRESSION_EPSILON_PCT
    model_backend: str = DEFAULT_REGRESSION_MODEL_BACKEND
    sgd_l2_alpha: float = DEFAULT_SGD_L2_ALPHA
    sgd_learning_rate_initial: float = DEFAULT_SGD_LEARNING_RATE_INITIAL
    feature_profile: str = "all"


class AIRegressionStrategy(Strategy):
    """Online-learning strategy predicting price deltas with a Passive-Aggressive regressor."""

    def __init__(self, base_cfg: StrategyConfig):
        super().__init__(base_cfg)
        params = base_cfg.params or {}

        pairs_param = params.get("pairs") or []
        pairs = list(pairs_param) if isinstance(pairs_param, (list, tuple)) else []

        short_window = max(int(params.get("short_window", 5)), 2)
        long_window = max(int(params.get("long_window", 20)), short_window + 1)
        lookback_bars = max(int(params.get("lookback_bars", 50)), long_window + 1)
        edge_defaults = edge_cost_config_from_context(params, None)

        self.params = AIRegressionConfig(
            pairs=pairs,
            timeframe=params.get("timeframe", "1h"),
            lookback_bars=lookback_bars,
            short_window=short_window,
            long_window=long_window,
            target_exposure_usd=params.get("target_exposure_usd"),
            continuous_learning=bool(params.get("continuous_learning", True)),
            max_positions=max(int(params.get("max_positions", 2)), 1),
            min_edge_pct=float(params.get("min_edge_pct", 0.003)),
            edge_fee_bps=edge_defaults.fee_bps,
            edge_slippage_bps=edge_defaults.slippage_bps,
            edge_cost_multiplier=edge_defaults.cost_multiplier,
            regression_epsilon_pct=_nonnegative_float(
                params.get("regression_epsilon_pct"),
                DEFAULT_REGRESSION_EPSILON_PCT,
            ),
            model_backend=regression_model_backend(params.get("model_backend")),
            sgd_l2_alpha=_nonnegative_float(
                params.get("sgd_l2_alpha"),
                DEFAULT_SGD_L2_ALPHA,
            ),
            sgd_learning_rate_initial=_positive_float(
                params.get("sgd_learning_rate_initial"),
                DEFAULT_SGD_LEARNING_RATE_INITIAL,
            ),
            feature_profile=normalize_feature_profile(params.get("feature_profile")),
        )

        self.model = self._new_model()
        self.model_initialized = False

    def _model_config_key(self) -> str:
        return regression_model_config_key(
            self.params.regression_epsilon_pct,
            model_backend=self.params.model_backend,
            sgd_l2_alpha=self.params.sgd_l2_alpha,
            sgd_learning_rate_initial=self.params.sgd_learning_rate_initial,
        )

    def _model_framework(self) -> str:
        return regression_model_framework(self.params.model_backend)

    def _new_model(self) -> MLOnlineModelBundle:
        return create_regression_model_bundle(
            model_backend=self.params.model_backend,
            epsilon_pct=self.params.regression_epsilon_pct,
            sgd_l2_alpha=self.params.sgd_l2_alpha,
            sgd_learning_rate_initial=self.params.sgd_learning_rate_initial,
        )

    def warmup(self, market_data: MarketDataAPI, portfolio: PortfolioService) -> None:
        return None

    def _learning_enabled(self) -> bool:
        return bool(self.config.params.get("continuous_learning", True))

    def _model_key(self, timeframe: str) -> str:
        return (
            f"global|{timeframe}|{feature_model_key_suffix(self.params.feature_profile)}|"
            f"{self._model_config_key()}"
        )

    def _edge_cost_config(self, ctx: StrategyContext) -> MLEdgeCostConfig:
        return edge_cost_config_from_context(self.config.params, ctx)

    def _checkpoint_metadata(self) -> dict[str, object]:
        return {
            "model_initialized": self.model_initialized,
            "continuous_learning": self._learning_enabled(),
            "feature_schema_version": ML_FEATURE_SCHEMA_VERSION,
            "feature_profile": self.params.feature_profile,
            "feature_names": list(
                feature_names_for_profile(self.params.feature_profile)
            ),
            "model_backend": self.params.model_backend,
            "model_framework": self._model_framework(),
            "model_config_key": self._model_config_key(),
            "regression_epsilon_pct": self.params.regression_epsilon_pct,
            "sgd_l2_alpha": self.params.sgd_l2_alpha,
            "sgd_learning_rate_initial": self.params.sgd_learning_rate_initial,
            "scaler_schema_version": ML_STANDARD_SCALER_SCHEMA_VERSION,
            "scaler_initialized": bool(
                getattr(self.model, "scaler_initialized", False)
            ),
        }

    def _save_training_checkpoint(
        self,
        ctx: StrategyContext,
        timeframe: str,
        *,
        checkpoint_state: str,
        extra_metadata: Optional[dict[str, object]] = None,
    ) -> None:
        metadata = self._checkpoint_metadata()
        if extra_metadata:
            metadata.update(extra_metadata)
        save_training_checkpoint(
            ctx,
            strategy_id=self.id,
            model_key=self._model_key(timeframe),
            label_type="regression",
            framework=self._model_framework(),
            model=self.model,
            checkpoint_state=checkpoint_state,
            metadata=metadata,
        )

    def _maybe_bootstrap_from_history(
        self, ctx: StrategyContext, timeframe: str
    ) -> None:
        if self.model_initialized:
            return

        model_key = self._model_key(timeframe)
        live_model = load_model(ctx, self.id, model_key)
        checkpoint = load_training_checkpoint(ctx, self.id, model_key)
        updated_at = None

        checkpoint_candidate: Optional[tuple[object, datetime, bool]]
        checkpoint_candidate = None
        if checkpoint is not None:
            restored_model, checkpoint_updated_at, _state, metadata = checkpoint
            if is_regression_model_for_backend(
                restored_model, self.params.model_backend
            ):
                checkpoint_candidate = (
                    restored_model,
                    checkpoint_updated_at,
                    bool(metadata.get("model_initialized", True)),
                )

        live_candidate: Optional[tuple[object, datetime]]
        live_candidate = None
        if live_model is not None:
            restored_model, updated_at = live_model
            if is_regression_model_for_backend(
                restored_model, self.params.model_backend
            ):
                live_candidate = (restored_model, updated_at)

        if checkpoint_candidate and checkpoint_candidate[2]:
            if live_candidate is None or checkpoint_candidate[1] >= live_candidate[1]:
                self.model = cast(MLOnlineModelBundle, checkpoint_candidate[0])
                self.model_initialized = True
                updated_at = checkpoint_candidate[1]

        if not self.model_initialized and live_candidate is not None:
            self.model = cast(MLOnlineModelBundle, live_candidate[0])
            self.model_initialized = True
            updated_at = live_candidate[1]

        if not self.model_initialized and checkpoint_candidate is not None:
            self.model = cast(MLOnlineModelBundle, checkpoint_candidate[0])
            self.model_initialized = checkpoint_candidate[2]
            updated_at = checkpoint_candidate[1]

        if self.model_initialized:
            if updated_at and self._learning_enabled():
                self._catch_up_model(ctx, timeframe, updated_at)
            return

        # If no model exists, bootstrap from stored examples if available
        X, y = load_training_window(
            ctx,
            strategy_id=self.id,
            model_key=model_key,
            max_examples=TRAINING_WINDOW_EXAMPLES,
        )
        if not X or not y:
            return

        try:
            self.model.partial_fit(X, y)
            self.model_initialized = True
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("ML bootstrap failed for %s: %s", self.id, exc)

    def _catch_up_model(
        self, ctx: StrategyContext, timeframe: str, last_updated: datetime
    ) -> None:
        """Backfill training on candles missed while the bot was offline."""

        now = ctx.now
        if not isinstance(now, datetime):
            now = datetime.now(timezone.utc)

        if (now - last_updated).total_seconds() < 60:
            return

        logger.info(
            "Catching up ML model for %s (last updated: %s)",
            timeframe,
            last_updated.isoformat(),
        )

        pairs = self.params.pairs or list(ctx.universe or [])
        if not pairs:
            return

        catch_up_start = max(last_updated, now - timedelta(days=7))
        start_ts = catch_up_start.timestamp() + 1

        training_count = 0
        try:
            tf_seconds = 3600
            if timeframe.endswith("m"):
                tf_seconds = int(timeframe[:-1]) * 60
            elif timeframe.endswith("h"):
                tf_seconds = int(timeframe[:-1]) * 3600
            elif timeframe.endswith("d"):
                tf_seconds = int(timeframe[:-1]) * 86400

            gap_seconds = (now - last_updated).total_seconds()
            bars_missed = int(gap_seconds / tf_seconds)

            if bars_missed <= 0:
                return

            lookback = min(bars_missed + 5, 500)

            for pair in pairs:
                ohlc = ctx.market_data.get_ohlc(
                    pair, timeframe, lookback=lookback + self.params.lookback_bars
                )
                if not ohlc or len(ohlc) < 2:
                    continue

                for i in range(self.params.lookback_bars, len(ohlc)):
                    bar_t = ohlc[i]
                    bar_prev = ohlc[i - 1]

                    if bar_t.timestamp <= start_ts:
                        continue

                    start_slice = i - self.params.lookback_bars
                    if start_slice < 0:
                        continue

                    window_slice = ohlc[start_slice:i]
                    features = self._compute_features_from_window(window_slice)
                    if not features:
                        continue

                    delta = (
                        (bar_t.close - bar_prev.close) / bar_prev.close
                        if bar_prev.close > 0
                        else 0.0
                    )

                    self.model.partial_fit([features], [delta])
                    training_count += 1

        except Exception as exc:
            logger.warning("Error during ML catch-up: %s", exc)

        if training_count > 0:
            logger.info("Caught up ML model with %d examples", training_count)
            self._save_training_checkpoint(
                ctx,
                timeframe,
                checkpoint_state="ready",
                extra_metadata={"catch_up_examples": training_count},
            )
            save_model(
                ctx,
                strategy_id=self.id,
                model_key=self._model_key(timeframe),
                label_type="regression",
                framework=self._model_framework(),
                model=self.model,
            )

    def _compute_feature_vector_from_window(
        self, ohlc_window: list
    ) -> Optional[MLFeatureVector]:
        return compute_feature_vector_from_ohlc(
            ohlc_window,
            self.params.short_window,
            self.params.long_window,
            self.params.lookback_bars,
            feature_profile=self.params.feature_profile,
        )

    def _compute_features_from_window(self, ohlc_window: list) -> Optional[List[float]]:
        vector = self._compute_feature_vector_from_window(ohlc_window)
        return list(vector.values) if vector is not None else None

    def _extract_feature_vector(
        self, ctx: StrategyContext, pair: str, timeframe: str
    ) -> Optional[MLFeatureVector]:
        lookback = max(
            self.params.lookback_bars,
            self.params.long_window + 1,
            self.params.short_window + 1,
        )
        ohlc = ctx.market_data.get_ohlc(pair, timeframe, lookback=lookback)
        return self._compute_feature_vector_from_window(ohlc)

    def _extract_features(
        self, ctx: StrategyContext, pair: str, timeframe: str
    ) -> Optional[List[float]]:
        vector = self._extract_feature_vector(ctx, pair, timeframe)
        return list(vector.values) if vector is not None else None

    def _extract_training_example(
        self, ctx: StrategyContext, pair: str, timeframe: str
    ) -> Optional[Tuple[List[float], float]]:
        """Reconstruct the training example for the previous completed candle (T-1).

        Returns (features, label) where:
          - features are calculated from OHLC history up to T-1.
          - label is (Close(T) - Close(T-1)) / Close(T-1).
        """
        lookback = max(
            self.params.lookback_bars,
            self.params.long_window + 1,
            self.params.short_window + 1,
        )
        ohlc = ctx.market_data.get_ohlc(pair, timeframe, lookback=lookback + 1)

        if not ohlc or len(ohlc) < lookback + 1:
            return None

        bar_t = ohlc[-1]
        bar_prev = ohlc[-2]

        label = (
            (bar_t.close - bar_prev.close) / bar_prev.close
            if bar_prev.close > 0
            else 0.0
        )

        features_window = ohlc[:-1]
        features = self._compute_features_from_window(features_window)

        if features:
            return features, label
        return None

    def _confidence(self, predicted_delta: float) -> float:
        magnitude = abs(predicted_delta)
        return 1.0 - (1.0 / (1.0 + math.exp(magnitude)))

    def generate_intents(self, ctx: StrategyContext) -> List[StrategyIntent]:
        intents: List[StrategyIntent] = []
        training_updated = False

        timeframe = ctx.timeframe or self.params.timeframe
        model_key = self._model_key(timeframe)
        self._maybe_bootstrap_from_history(ctx, timeframe)

        universe = list(ctx.universe or [])
        if not universe:
            return []

        base_pairs = list(self.params.pairs or universe)
        allowed_universe = set(universe)
        pairs = [pair for pair in base_pairs if pair in allowed_universe]

        if not pairs:
            return []

        positions_by_pair = self._owned_positions_by_pair_key(ctx)
        open_positions_count = len(positions_by_pair)

        for pair in pairs:
            # 1. Train on T-1
            if self._learning_enabled():
                train_data = self._extract_training_example(ctx, pair, timeframe)
                if train_data:
                    features_prev, label_prev = train_data

                    record_example(
                        ctx,
                        strategy_id=self.id,
                        model_key=model_key,
                        label_type="regression",
                        features=features_prev,
                        label=label_prev,
                    )
                    try:
                        if not self.model_initialized:
                            self.model.partial_fit([features_prev], [label_prev])
                            self.model_initialized = True
                        else:
                            self.model.partial_fit([features_prev], [label_prev])
                        training_updated = True
                        self._save_training_checkpoint(
                            ctx,
                            timeframe,
                            checkpoint_state="training",
                            extra_metadata={
                                "last_pair": pair,
                                "training_mode": "online",
                            },
                        )
                    except Exception as exc:  # pragma: no cover - defensive
                        logger.debug("Model update failed for %s: %s", pair, exc)

            # 2. Predict T
            feature_vector = self._extract_feature_vector(ctx, pair, timeframe)
            if not feature_vector:
                continue
            features = feature_vector.values

            if not self.model_initialized:
                continue

            try:
                predicted_delta = float(self.model.predict([features])[0])
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("Prediction failed for %s: %s", pair, exc)
                continue

            confidence = self._confidence(predicted_delta)
            edge_config = self._edge_cost_config(ctx)
            effective_min_edge_pct = edge_config.effective_min_edge_pct(
                self.params.min_edge_pct
            )
            position = positions_by_pair.get(self._pair_key(ctx, pair))
            has_long = bool(position and getattr(position, "base_size", 0) > 0)

            if predicted_delta > effective_min_edge_pct:
                if not has_long and open_positions_count >= self.params.max_positions:
                    continue

                side = "long"
                intent_type = "increase" if has_long else "enter"
                desired_exposure = self.params.target_exposure_usd

                if not has_long:
                    open_positions_count += 1
            else:
                side = "flat"
                intent_type = "reduce" if has_long else "exit"
                desired_exposure = 0.0

            intents.append(
                StrategyIntent(
                    strategy_id=self.id,
                    pair=pair,
                    side=side,
                    intent_type=intent_type,
                    desired_exposure_usd=desired_exposure,
                    confidence=confidence,
                    timeframe=timeframe,
                    generated_at=ctx.now,
                    metadata={
                        "predicted_delta": predicted_delta,
                        "prediction_target": "signed_return_delta",
                        "predicted_positive_edge": side == "long",
                        "min_edge_pct": self.params.min_edge_pct,
                        "effective_min_edge_pct": effective_min_edge_pct,
                        **edge_config.to_metadata(),
                        "learning_enabled": self._learning_enabled(),
                        "confidence_source": "predicted_delta_magnitude",
                        "feature_schema_version": feature_vector.schema_version,
                        "features": feature_vector.to_metadata(),
                    },
                )
            )

        if self.model_initialized:
            if training_updated:
                self._save_training_checkpoint(
                    ctx,
                    timeframe,
                    checkpoint_state="ready",
                    extra_metadata={"training_mode": "online"},
                )
            save_model(
                ctx,
                strategy_id=self.id,
                model_key=model_key,
                label_type="regression",
                framework=self._model_framework(),
                model=self.model,
            )

        return intents
