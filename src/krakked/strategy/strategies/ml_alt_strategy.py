from __future__ import annotations

import logging
import math
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from krakked.config import StrategyConfig
from krakked.market_data.api import MarketDataAPI
from krakked.market_data.exceptions import DataStaleError
from krakked.portfolio.manager import PortfolioService
from krakked.strategy.base import Strategy, StrategyContext
from krakked.strategy.ml_models import PassiveAggressiveClassifier
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

        self.params = AIPredictorConfig(
            pairs=pairs,
            timeframe=params.get("timeframe", "1h"),
            lookback_bars=lookback_bars,
            short_window=short_window,
            long_window=long_window,
            target_exposure_usd=params.get("target_exposure_usd"),
            continuous_learning=bool(params.get("continuous_learning", True)),
            max_positions=max(int(params.get("max_positions", 2)), 1),
        )

        self.classes = [0, 1]
        self.models: Dict[Tuple[str, str], PassiveAggressiveClassifier] = {}
        self.model_initialized: Dict[Tuple[str, str], bool] = {}
        self._last_observation: Dict[Tuple[str, str], Tuple[List[float], float]] = {}

    def warmup(self, market_data: MarketDataAPI, portfolio: PortfolioService) -> None:
        return None

    def _learning_enabled(self) -> bool:
        return bool(self.config.params.get("continuous_learning", True))

    def _get_model(self, key: Tuple[str, str]) -> PassiveAggressiveClassifier:
        model = self.models.get(key)
        if model is None:
            model = PassiveAggressiveClassifier(max_iter=1000, tol=1e-3)
            self.models[key] = model
            self.model_initialized[key] = False
        return model

    def _model_key(self, pair: str, timeframe: str) -> str:
        return f"{pair}|{timeframe}"

    def _checkpoint_metadata(self, key: Tuple[str, str]) -> dict[str, object]:
        metadata: dict[str, object] = {
            "model_initialized": self.model_initialized.get(key, False),
            "continuous_learning": self._learning_enabled(),
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
        model: PassiveAggressiveClassifier,
        *,
        checkpoint_state: str,
    ) -> None:
        pair, timeframe = key
        save_training_checkpoint(
            ctx,
            strategy_id=self.id,
            model_key=self._model_key(pair, timeframe),
            label_type="classification",
            framework=MODEL_FRAMEWORK,
            model=model,
            checkpoint_state=checkpoint_state,
            metadata=self._checkpoint_metadata(key),
        )

    def _maybe_bootstrap_from_history(
        self,
        ctx: StrategyContext,
        key: Tuple[str, str],
        model: PassiveAggressiveClassifier,
    ) -> None:
        if self.model_initialized.get(key):
            return

        pair, timeframe = key
        model_key = self._model_key(pair, timeframe)

        live_model = load_model(ctx, self.id, model_key)
        checkpoint = load_training_checkpoint(ctx, self.id, model_key)

        checkpoint_candidate: Optional[
            tuple[PassiveAggressiveClassifier, datetime, bool, dict[str, object]]
        ] = None
        if checkpoint is not None:
            restored_model, checkpoint_updated_at, _state, metadata = checkpoint
            if isinstance(restored_model, PassiveAggressiveClassifier):
                checkpoint_candidate = (
                    restored_model,
                    checkpoint_updated_at,
                    bool(metadata.get("model_initialized", True)),
                    metadata,
                )

        live_candidate: Optional[tuple[PassiveAggressiveClassifier, datetime]] = None
        if live_model is not None:
            restored_model, live_updated_at = live_model
            if isinstance(restored_model, PassiveAggressiveClassifier):
                live_candidate = (restored_model, live_updated_at)

        if checkpoint_candidate and checkpoint_candidate[2]:
            if live_candidate is None or checkpoint_candidate[1] >= live_candidate[1]:
                self.models[key] = checkpoint_candidate[0]
                self.model_initialized[key] = True
                self._restore_checkpoint_metadata(key, checkpoint_candidate[3])
                return

        if live_candidate is not None:
            self.models[key] = live_candidate[0]
            self.model_initialized[key] = True
            return

        if checkpoint_candidate is not None:
            self.models[key] = checkpoint_candidate[0]
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

    def _extract_features(
        self, ctx: StrategyContext, pair: str, timeframe: str
    ) -> Optional[List[float]]:
        lookback = max(
            self.params.lookback_bars,
            self.params.long_window + 1,
            self.params.short_window + 1,
        )
        ohlc = ctx.market_data.get_ohlc(pair, timeframe, lookback=lookback)
        if not ohlc or len(ohlc) < 3:
            return None

        closes = [float(bar.close) for bar in ohlc]
        last_close, prev_close = closes[-1], closes[-2]
        if prev_close <= 0:
            return None

        pct_change = (last_close - prev_close) / prev_close

        short_len = min(self.params.short_window, len(closes))
        long_len = min(self.params.long_window, len(closes))
        short_ma = sum(closes[-short_len:]) / short_len if short_len > 0 else 0.0
        long_ma = sum(closes[-long_len:]) / long_len if long_len > 0 else 0.0
        trend_diff = ((short_ma - long_ma) / long_ma) if long_ma > 0 else 0.0

        window = closes[-self.params.lookback_bars :]
        mean_close = sum(window) / len(window)
        volatility = 0.0
        if mean_close > 0 and len(window) > 1:
            variance = sum((c - mean_close) ** 2 for c in window) / len(window)
            volatility = math.sqrt(variance) / mean_close

        return [pct_change, trend_diff, volatility]

    def _confidence(
        self, model: PassiveAggressiveClassifier, features: List[float]
    ) -> float:
        try:
            score = float(model.decision_function([features])[0])
        except Exception:
            return 0.5
        magnitude = abs(score)
        return 1.0 / (1.0 + math.exp(-magnitude))

    def generate_intents(self, ctx: StrategyContext) -> List[StrategyIntent]:
        intents: List[StrategyIntent] = []

        timeframe = ctx.timeframe or self.params.timeframe

        universe = list(ctx.universe or [])
        if not universe:
            return []

        base_pairs = list(self.params.pairs or universe)
        allowed_universe = set(universe)
        pairs = [pair for pair in base_pairs if pair in allowed_universe]

        if not pairs:
            return []

        positions = ctx.portfolio.get_positions() or []
        positions_by_pair = {
            pos.pair: pos
            for pos in positions
            if getattr(pos, "base_size", 0) > 0
            and getattr(pos, "strategy_tag", None) == self.id
        }
        open_positions_count = len(positions_by_pair)

        for pair in pairs:
            key = (pair, timeframe)
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
            if last_obs:
                last_features, last_price = last_obs
                label = 1 if current_price > last_price else 0
                record_example(
                    ctx,
                    strategy_id=self.id,
                    model_key=self._model_key(pair, timeframe),
                    label_type="classification",
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
                    elif self._learning_enabled():
                        model.partial_fit([last_features], [label])
                    training_updated = True
                except Exception as exc:  # pragma: no cover - defensive
                    logger.debug("Model update failed for %s: %s", pair, exc)

            features = self._extract_features(ctx, pair, timeframe)
            if not features:
                continue

            self._last_observation[key] = (features, current_price)
            self._save_training_checkpoint(
                ctx,
                key,
                model,
                checkpoint_state="training" if training_updated else "ready",
            )

            if not initialized:
                continue

            try:
                prediction = int(model.predict([features])[0])
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("Prediction failed for %s: %s", pair, exc)
                continue

            confidence = self._confidence(model, features)
            position = positions_by_pair.get(pair)
            has_long = bool(position and getattr(position, "base_size", 0) > 0)

            if prediction == 1:
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
                        "prediction": "up" if prediction == 1 else "down",
                        "learning_enabled": self._learning_enabled(),
                        "features": {
                            "pct_change": features[0],
                            "trend_diff": features[1],
                            "volatility": features[2],
                        },
                    },
                )
            )

            if initialized:
                self._save_training_checkpoint(
                    ctx,
                    key,
                    model,
                    checkpoint_state="ready",
                )
                save_model(
                    ctx,
                    strategy_id=self.id,
                    model_key=self._model_key(pair, timeframe),
                    label_type="classification",
                    framework=MODEL_FRAMEWORK,
                    model=model,
                )

        return intents
