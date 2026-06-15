from __future__ import annotations

import logging
import math
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, cast

from krakked.config import StrategyConfig
from krakked.market_data.api import MarketDataAPI
from krakked.market_data.exceptions import DataStaleError
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
from krakked.strategy.ml_labels import (
    FEE_ADJUSTED_CLASSIFICATION_LABEL_TYPE,
    FEE_ADJUSTED_EDGE_PREDICTION_TARGET,
    NO_POSITIVE_EDGE_PREDICTION,
    POSITIVE_EDGE_PREDICTION,
    FeeAdjustedLabelConfig,
    classify_fee_adjusted_return,
    label_config_from_context,
)
from krakked.strategy.ml_models import (
    ML_STANDARD_SCALER_SCHEMA_VERSION,
    MLOnlineModelBundle,
    PassiveAggressiveClassifier,
    StandardScaler,
    classifier_model_config_key,
    is_passive_aggressive_classifier_model,
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

from .ml_strategy import AIPredictorConfig

logger = logging.getLogger(__name__)

TRAINING_WINDOW_EXAMPLES = 5000
MODEL_FRAMEWORK = "sklearn_passive_aggressive_classifier"


class AIPredictorAltStrategy(Strategy):
    """Per-pair online-learning strategy using Passive-Aggressive models."""

    def __init__(self, base_cfg: StrategyConfig) -> None:
        super().__init__(base_cfg)
        params = base_cfg.params or {}

        pairs_param = params.get("pairs") or []
        pairs = list(pairs_param) if isinstance(pairs_param, (list, tuple)) else []

        short_window = max(int(params.get("short_window", 5)), 2)
        long_window = max(int(params.get("long_window", 20)), short_window + 1)
        lookback_bars = max(int(params.get("lookback_bars", 50)), long_window + 1)
        label_defaults = label_config_from_context(params, None)

        self.params = AIPredictorConfig(
            pairs=pairs,
            timeframe=params.get("timeframe", "1h"),
            lookback_bars=lookback_bars,
            short_window=short_window,
            long_window=long_window,
            target_exposure_usd=params.get("target_exposure_usd"),
            continuous_learning=bool(params.get("continuous_learning", True)),
            max_positions=max(int(params.get("max_positions", 2)), 1),
            label_fee_bps=label_defaults.fee_bps,
            label_slippage_bps=label_defaults.slippage_bps,
            label_cost_multiplier=label_defaults.cost_multiplier,
            feature_profile=normalize_feature_profile(params.get("feature_profile")),
        )

        self.classes = [0, 1]
        self.models: Dict[Tuple[str, str], MLOnlineModelBundle] = {}
        self.model_initialized: Dict[Tuple[str, str], bool] = {}
        self._last_observation: Dict[Tuple[str, str], Tuple[List[float], float]] = {}

    def _model_config_key(self) -> str:
        return classifier_model_config_key()

    def _new_model(self) -> MLOnlineModelBundle:
        return MLOnlineModelBundle(
            model=PassiveAggressiveClassifier(max_iter=1000, tol=1e-3),
            scaler=StandardScaler(),
        )

    def warmup(self, market_data: MarketDataAPI, portfolio: PortfolioService) -> None:
        return None

    def _learning_enabled(self) -> bool:
        return bool(self.config.params.get("continuous_learning", True))

    def _get_model(self, key: Tuple[str, str]) -> MLOnlineModelBundle:
        model = self.models.get(key)
        if model is None:
            model = self._new_model()
            self.models[key] = model
            self.model_initialized[key] = False
        return model

    def _label_config(self, ctx: StrategyContext) -> FeeAdjustedLabelConfig:
        return label_config_from_context(self.config.params, ctx)

    def _model_key(
        self,
        pair: str,
        timeframe: str,
        label_config: Optional[FeeAdjustedLabelConfig] = None,
    ) -> str:
        if label_config is None:
            label_config = FeeAdjustedLabelConfig(
                fee_bps=self.params.label_fee_bps,
                slippage_bps=self.params.label_slippage_bps,
                cost_multiplier=self.params.label_cost_multiplier,
            )
        return (
            f"{pair}|{timeframe}|{feature_model_key_suffix(self.params.feature_profile)}|"
            f"{label_config.model_key_suffix()}|{self._model_config_key()}"
        )

    def _checkpoint_metadata(
        self,
        key: Tuple[str, str],
        label_config: Optional[FeeAdjustedLabelConfig] = None,
    ) -> dict[str, object]:
        if label_config is None:
            label_config = FeeAdjustedLabelConfig(
                fee_bps=self.params.label_fee_bps,
                slippage_bps=self.params.label_slippage_bps,
                cost_multiplier=self.params.label_cost_multiplier,
            )
        metadata: dict[str, object] = {
            "model_initialized": self.model_initialized.get(key, False),
            "continuous_learning": self._learning_enabled(),
            "feature_schema_version": ML_FEATURE_SCHEMA_VERSION,
            "feature_profile": self.params.feature_profile,
            "feature_names": list(
                feature_names_for_profile(self.params.feature_profile)
            ),
            "model_config_key": self._model_config_key(),
            "scaler_schema_version": ML_STANDARD_SCALER_SCHEMA_VERSION,
            "scaler_initialized": bool(
                getattr(self.models.get(key), "scaler_initialized", False)
            ),
            "label": label_config.to_metadata(),
        }
        observation = self._last_observation.get(key)
        if observation is not None:
            features, price = observation
            metadata["last_observation"] = {
                "features": [float(value) for value in features],
                "price": float(price),
            }
        return metadata

    def _restore_checkpoint_metadata(
        self, key: Tuple[str, str], metadata: dict[str, object]
    ) -> None:
        observation = metadata.get("last_observation")
        if not isinstance(observation, dict):
            return

        features = observation.get("features")
        price = observation.get("price")
        if not isinstance(features, list) or price is None:
            return

        try:
            restored_features = [float(value) for value in features]
            restored_price = float(price)
        except (TypeError, ValueError):
            return

        self._last_observation[key] = (restored_features, restored_price)

    def _save_training_checkpoint(
        self,
        ctx: StrategyContext,
        key: Tuple[str, str],
        model: object,
        *,
        checkpoint_state: str,
        label_config: FeeAdjustedLabelConfig,
    ) -> None:
        pair, timeframe = key
        save_training_checkpoint(
            ctx,
            strategy_id=self.id,
            model_key=self._model_key(pair, timeframe, label_config),
            label_type=FEE_ADJUSTED_CLASSIFICATION_LABEL_TYPE,
            framework=MODEL_FRAMEWORK,
            model=model,
            checkpoint_state=checkpoint_state,
            metadata=self._checkpoint_metadata(key, label_config),
        )

    def _maybe_bootstrap_from_history(
        self,
        ctx: StrategyContext,
        key: Tuple[str, str],
        model: MLOnlineModelBundle,
    ) -> None:
        if self.model_initialized.get(key):
            return

        pair, timeframe = key
        label_config = self._label_config(ctx)
        model_key = self._model_key(pair, timeframe, label_config)

        live_model = load_model(ctx, self.id, model_key)
        checkpoint = load_training_checkpoint(ctx, self.id, model_key)

        checkpoint_candidate: Optional[tuple[object, datetime, bool, dict[str, object]]]
        checkpoint_candidate = None
        if checkpoint is not None:
            restored_model, checkpoint_updated_at, _state, metadata = checkpoint
            if is_passive_aggressive_classifier_model(restored_model):
                checkpoint_candidate = (
                    restored_model,
                    checkpoint_updated_at,
                    bool(metadata.get("model_initialized", True)),
                    metadata,
                )

        live_candidate: Optional[tuple[object, datetime]] = None
        if live_model is not None:
            restored_model, live_updated_at = live_model
            if is_passive_aggressive_classifier_model(restored_model):
                live_candidate = (restored_model, live_updated_at)

        if checkpoint_candidate and checkpoint_candidate[2]:
            if live_candidate is None or checkpoint_candidate[1] >= live_candidate[1]:
                self.models[key] = cast(MLOnlineModelBundle, checkpoint_candidate[0])
                self.model_initialized[key] = True
                self._restore_checkpoint_metadata(key, checkpoint_candidate[3])
                return

        if live_candidate is not None:
            self.models[key] = cast(MLOnlineModelBundle, live_candidate[0])
            self.model_initialized[key] = True
            return

        if checkpoint_candidate is not None:
            self.models[key] = cast(MLOnlineModelBundle, checkpoint_candidate[0])
            self.model_initialized[key] = checkpoint_candidate[2]
            self._restore_checkpoint_metadata(key, checkpoint_candidate[3])
            return

        X, y = load_training_window(
            ctx,
            strategy_id=self.id,
            model_key=model_key,
            max_examples=TRAINING_WINDOW_EXAMPLES,
        )
        if not X or not y:
            return

        try:
            model.partial_fit(X, [int(v) for v in y], classes=self.classes)
            self.model_initialized[key] = True
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("ML bootstrap failed for %s key=%s: %s", self.id, key, exc)

    def _extract_feature_vector(
        self, ctx: StrategyContext, pair: str, timeframe: str
    ) -> Optional[MLFeatureVector]:
        lookback = max(
            self.params.lookback_bars,
            self.params.long_window + 1,
            self.params.short_window + 1,
        )
        ohlc = ctx.market_data.get_ohlc(pair, timeframe, lookback=lookback)
        return compute_feature_vector_from_ohlc(
            ohlc,
            self.params.short_window,
            self.params.long_window,
            self.params.lookback_bars,
            feature_profile=self.params.feature_profile,
        )

    def _extract_features(
        self, ctx: StrategyContext, pair: str, timeframe: str
    ) -> Optional[List[float]]:
        vector = self._extract_feature_vector(ctx, pair, timeframe)
        return list(vector.values) if vector is not None else None

    def _confidence(self, model: object, features: List[float]) -> float:
        decision_function = getattr(model, "decision_function", None)
        if not callable(decision_function):
            return 0.5
        try:
            result: Any = decision_function([features])
            score = float(result[0])
        except Exception:
            return 0.5
        magnitude = abs(score)
        return 1.0 / (1.0 + math.exp(-magnitude))

    def generate_intents(self, ctx: StrategyContext) -> List[StrategyIntent]:
        intents: List[StrategyIntent] = []

        timeframe = ctx.timeframe or self.params.timeframe
        label_config = self._label_config(ctx)

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
            key = (pair, timeframe)
            model_key = self._model_key(pair, timeframe, label_config)
            model = self._get_model(key)
            initialized = self.model_initialized.get(key, False)

            self._maybe_bootstrap_from_history(ctx, key, model)
            initialized = self.model_initialized.get(key, False)

            try:
                current_price = ctx.market_data.get_latest_price(pair)
            except DataStaleError as exc:
                logger.debug("Skipping stale data for %s: %s", pair, exc)
                continue
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("Skipping price fetch error for %s: %s", pair, exc)
                continue

            if current_price is None:
                continue

            last_obs = self._last_observation.get(key)
            training_updated = False
            learning_enabled = self._learning_enabled()
            if last_obs and learning_enabled:
                last_features, last_price = last_obs
                label_result = classify_fee_adjusted_return(
                    float(last_price), float(current_price), label_config
                )
                if label_result is not None:
                    label = label_result.value
                    record_example(
                        ctx,
                        strategy_id=self.id,
                        model_key=model_key,
                        label_type=FEE_ADJUSTED_CLASSIFICATION_LABEL_TYPE,
                        features=last_features,
                        label=float(label),
                    )
                    try:
                        if not initialized:
                            model.partial_fit(
                                [last_features], [label], classes=self.classes
                            )
                            initialized = True
                            self.model_initialized[key] = True
                        else:
                            model.partial_fit([last_features], [label])
                        training_updated = True
                    except Exception as exc:  # pragma: no cover - defensive
                        logger.debug("Model update failed for %s: %s", pair, exc)

            feature_vector = self._extract_feature_vector(ctx, pair, timeframe)
            if not feature_vector:
                continue
            features = feature_vector.values

            self._last_observation[key] = (features, current_price)
            self._save_training_checkpoint(
                ctx,
                key,
                model,
                checkpoint_state="training" if training_updated else "ready",
                label_config=label_config,
            )

            if not initialized:
                continue

            try:
                prediction = int(model.predict([features])[0])
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("Prediction failed for %s: %s", pair, exc)
                continue

            confidence = self._confidence(model, features)
            predicted_positive_edge = prediction == 1
            position = positions_by_pair.get(self._pair_key(ctx, pair))
            has_long = bool(position and getattr(position, "base_size", 0) > 0)

            if predicted_positive_edge:
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
                        "prediction": (
                            POSITIVE_EDGE_PREDICTION
                            if predicted_positive_edge
                            else NO_POSITIVE_EDGE_PREDICTION
                        ),
                        "prediction_target": FEE_ADJUSTED_EDGE_PREDICTION_TARGET,
                        "predicted_positive_edge": predicted_positive_edge,
                        "learning_enabled": self._learning_enabled(),
                        "confidence_source": "decision_function_magnitude",
                        "feature_schema_version": feature_vector.schema_version,
                        "label": label_config.to_metadata(),
                        "features": feature_vector.to_metadata(),
                    },
                )
            )

            if initialized:
                self._save_training_checkpoint(
                    ctx,
                    key,
                    model,
                    checkpoint_state="ready",
                    label_config=label_config,
                )
                save_model(
                    ctx,
                    strategy_id=self.id,
                    model_key=model_key,
                    label_type=FEE_ADJUSTED_CLASSIFICATION_LABEL_TYPE,
                    framework=MODEL_FRAMEWORK,
                    model=model,
                )

        return intents
