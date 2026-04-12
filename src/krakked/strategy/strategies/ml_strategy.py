from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from krakked.config import StrategyConfig
from krakked.market_data.api import MarketDataAPI
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

logger = logging.getLogger(__name__)

TRAINING_WINDOW_EXAMPLES = 5000
MODEL_FRAMEWORK = "sklearn_passive_aggressive_classifier"


@dataclass
class AIPredictorConfig:
    pairs: List[str]
    timeframe: str
    lookback_bars: int
    short_window: int
    long_window: int
    target_exposure_usd: Optional[float]
    continuous_learning: bool
    max_positions: int


class AIPredictorStrategy(Strategy):
    """Online-learning strategy using a Passive-Aggressive classifier."""

    def __init__(self, base_cfg: StrategyConfig):
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

        self.model = PassiveAggressiveClassifier(max_iter=1000, tol=1e-3)
        self.classes = [0, 1]
        self.model_initialized = False

    def warmup(self, market_data: MarketDataAPI, portfolio: PortfolioService) -> None:
        return None

    def _learning_enabled(self) -> bool:
        return bool(self.config.params.get("continuous_learning", True))

    def _model_key(self, timeframe: str) -> str:
        return f"global|{timeframe}"

    def _checkpoint_metadata(self) -> dict[str, object]:
        return {
            "model_initialized": self.model_initialized,
            "continuous_learning": self._learning_enabled(),
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
            label_type="classification",
            framework=MODEL_FRAMEWORK,
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

        checkpoint_candidate: Optional[tuple[PassiveAggressiveClassifier, datetime, bool]]
        checkpoint_candidate = None
        if checkpoint is not None:
            restored_model, checkpoint_updated_at, _state, metadata = checkpoint
            if isinstance(restored_model, PassiveAggressiveClassifier):
                checkpoint_candidate = (
                    restored_model,
                    checkpoint_updated_at,
                    bool(metadata.get("model_initialized", True)),
                )

        live_candidate: Optional[tuple[PassiveAggressiveClassifier, datetime]]
        live_candidate = None
        if live_model is not None:
            restored_model, updated_at = live_model
            if isinstance(restored_model, PassiveAggressiveClassifier):
                live_candidate = (restored_model, updated_at)

        if checkpoint_candidate and checkpoint_candidate[2]:
            if live_candidate is None or checkpoint_candidate[1] >= live_candidate[1]:
                self.model = checkpoint_candidate[0]
                self.model_initialized = True
                updated_at = checkpoint_candidate[1]

        if not self.model_initialized and live_candidate is not None:
            self.model = live_candidate[0]
            self.model_initialized = True
            updated_at = live_candidate[1]

        if not self.model_initialized and checkpoint_candidate is not None:
            self.model = checkpoint_candidate[0]
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
            self.model.partial_fit(X, [int(v) for v in y], classes=self.classes)
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

        # We limit catch-up to a reasonable window to avoid excessive load
        catch_up_start = max(last_updated, now - timedelta(days=7))

        # Simple heuristic: fetch data for one representative pair to get timestamps,
        # then iterate and train. Or iterate per pair.
        # Since this is a global model, we should train on all pairs in the window.

        # NOTE: Full multi-pair catch-up logic can be complex.
        # Here we perform a simplified catch-up: iterate configured pairs and
        # extract training examples from the gap.

        training_count = 0

        # To avoid re-training on the last seen candle, add a small buffer
        start_ts = catch_up_start.timestamp() + 1

        # We need OHLC since start_ts.
        # But `_extract_training_example` works by `shift=1` (T-1).
        # We can re-use `_extract_features` on historical bars if we can access them.
        # MarketDataAPI `get_ohlc` gets *recent* bars.
        # To robustly catch up, we rely on `get_ohlc_since` logic or just fetch a sufficient lookback.

        # Simplified approach: Train on the last N bars that cover the gap.
        # Calculate how many bars fit in the gap.
        # e.g. 1h timeframe, gap 10 hours -> last 10 bars.

        try:
            # Parse timeframe to seconds (rough approx for estimation)
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

            lookback = min(bars_missed + 5, 500)  # Cap at 500 bars catch-up

            for pair in pairs:
                # Get history covering the gap
                ohlc = ctx.market_data.get_ohlc(
                    pair, timeframe, lookback=lookback + self.params.lookback_bars
                )
                if not ohlc or len(ohlc) < 2:
                    continue

                # We iterate through history to reconstruct (Feature(T-1), Label(T-1)) pairs.
                # Label(T-1) requires Close(T) and Close(T-1).
                # Feature(T-1) requires OHLC up to T-1.

                # Iterate from index that corresponds to last_updated up to end
                for i in range(self.params.lookback_bars, len(ohlc)):
                    bar_t = ohlc[i]
                    bar_prev = ohlc[i - 1]

                    if bar_t.timestamp <= start_ts:
                        continue

                    # Reconstruct features for T-1
                    # Slice ohlc up to i-1 (inclusive)
                    # We need a slice of length `lookback_bars` ending at i-1
                    start_slice = i - self.params.lookback_bars
                    if start_slice < 0:
                        continue

                    window_slice = ohlc[start_slice:i]
                    features = self._compute_features_from_window(window_slice)
                    if not features:
                        continue

                    label = 1 if bar_t.close > bar_prev.close else 0

                    self.model.partial_fit([features], [label], classes=self.classes)
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
                label_type="classification",
                framework=MODEL_FRAMEWORK,
                model=self.model,
            )

    def _compute_features_from_window(self, ohlc_window: list) -> Optional[List[float]]:
        if not ohlc_window or len(ohlc_window) < 3:
            return None

        closes = [float(bar.close) for bar in ohlc_window]
        last_close, prev_close = closes[-1], closes[-2]
        if prev_close <= 0:
            return None

        pct_change = (last_close - prev_close) / prev_close

        short_len = min(self.params.short_window, len(closes))
        long_len = min(self.params.long_window, len(closes))
        short_ma = sum(closes[-short_len:]) / short_len if short_len > 0 else 0.0
        long_ma = sum(closes[-long_len:]) / long_len if long_len > 0 else 0.0
        trend_diff = ((short_ma - long_ma) / long_ma) if long_ma > 0 else 0.0

        mean_close = sum(closes) / len(closes)
        volatility = 0.0
        if mean_close > 0 and len(closes) > 1:
            variance = sum((c - mean_close) ** 2 for c in closes) / len(closes)
            volatility = math.sqrt(variance) / mean_close

        return [pct_change, trend_diff, volatility]

    def _extract_features(
        self, ctx: StrategyContext, pair: str, timeframe: str
    ) -> Optional[List[float]]:
        # This wrapper retrieves OHLC and delegates to _compute_features_from_window
        lookback = max(
            self.params.lookback_bars,
            self.params.long_window + 1,
            self.params.short_window + 1,
        )
        ohlc = ctx.market_data.get_ohlc(pair, timeframe, lookback=lookback)
        return self._compute_features_from_window(ohlc)

    def _extract_training_example(
        self, ctx: StrategyContext, pair: str, timeframe: str
    ) -> Optional[Tuple[List[float], float]]:
        """Reconstruct the training example for the previous completed candle (T-1).

        Returns (features, label) where:
          - features are calculated from OHLC history up to T-1.
          - label is 1 if Close(T) > Close(T-1), else 0.
        """
        # We need OHLC including the just-closed bar (T) and enough history for T-1 features.
        # T-1 features require `lookback` bars ending at T-1.
        # So we need `lookback + 1` bars total (the +1 is bar T for the label).
        lookback = max(
            self.params.lookback_bars,
            self.params.long_window + 1,
            self.params.short_window + 1,
        )
        # Fetch lookback + 1 bars
        ohlc = ctx.market_data.get_ohlc(pair, timeframe, lookback=lookback + 1)

        if not ohlc or len(ohlc) < lookback + 1:
            return None

        # Bar T is ohlc[-1] (latest closed candle)
        # Bar T-1 is ohlc[-2]
        bar_t = ohlc[-1]
        bar_prev = ohlc[-2]

        # Label: did T close higher than T-1?
        label = 1.0 if bar_t.close > bar_prev.close else 0.0

        # Features for T-1: use window ohlc[:-1] (excluding T)
        # And we take the last `lookback` bars of THAT slice.
        # But we fetched exactly `lookback + 1`, so ohlc[:-1] has length `lookback`.
        features_window = ohlc[:-1]
        features = self._compute_features_from_window(features_window)

        if features:
            return features, label
        return None

    def _confidence(self, features: List[float]) -> float:
        try:
            score = float(self.model.decision_function([features])[0])
        except Exception:
            return 0.5
        magnitude = abs(score)
        return 1.0 / (1.0 + math.exp(-magnitude))

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

        positions = ctx.portfolio.get_positions() or []
        positions_by_pair = {
            pos.pair: pos
            for pos in positions
            if getattr(pos, "base_size", 0) > 0
            and getattr(pos, "strategy_tag", None) == self.id
        }
        open_positions_count = len(positions_by_pair)

        for pair in pairs:
            # 1. Train on the previous completed candle (Deterministic Learning)
            if self._learning_enabled():
                train_data = self._extract_training_example(ctx, pair, timeframe)
                if train_data:
                    features_prev, label_prev = train_data

                    # Persist example
                    record_example(
                        ctx,
                        strategy_id=self.id,
                        model_key=model_key,
                        label_type="classification",
                        features=features_prev,
                        label=label_prev,
                    )

                    try:
                        if not self.model_initialized:
                            self.model.partial_fit(
                                [features_prev], [label_prev], classes=self.classes
                            )
                            self.model_initialized = True
                        else:
                            self.model.partial_fit([features_prev], [label_prev])
                        training_updated = True
                        self._save_training_checkpoint(
                            ctx,
                            timeframe,
                            checkpoint_state="training",
                            extra_metadata={"last_pair": pair, "training_mode": "online"},
                        )
                    except Exception as exc:  # pragma: no cover - defensive
                        logger.debug("Model update failed for %s: %s", pair, exc)

            # 2. Predict for the current state (T) to target T+1
            features = self._extract_features(ctx, pair, timeframe)
            if not features:
                continue

            if not self.model_initialized:
                continue

            try:
                prediction = int(self.model.predict([features])[0])
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("Prediction failed for %s: %s", pair, exc)
                continue

            confidence = self._confidence(features)
            position = positions_by_pair.get(pair)
            has_long = bool(position and getattr(position, "base_size", 0) > 0)

            if prediction == 1:
                # respect per-strategy cap on new positions
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
                label_type="classification",
                framework=MODEL_FRAMEWORK,
                model=self.model,
            )

        return intents
