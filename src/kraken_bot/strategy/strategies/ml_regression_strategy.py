from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from kraken_bot.config import StrategyConfig
from kraken_bot.market_data.api import MarketDataAPI
from kraken_bot.market_data.exceptions import DataStaleError
from kraken_bot.portfolio.manager import PortfolioService
from kraken_bot.strategy.base import Strategy, StrategyContext
from kraken_bot.strategy.ml_models import PassiveAggressiveRegressor
from kraken_bot.strategy.ml_persistence import (
    load_model,
    load_training_window,
    record_example,
    save_model,
)
from kraken_bot.strategy.models import StrategyIntent

logger = logging.getLogger(__name__)

TRAINING_WINDOW_EXAMPLES = 5000


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
        )

        self.model = PassiveAggressiveRegressor(max_iter=1000, tol=1e-3)
        self.model_initialized = False

    def warmup(self, market_data: MarketDataAPI, portfolio: PortfolioService) -> None:
        return None

    def _learning_enabled(self) -> bool:
        return bool(self.config.params.get("continuous_learning", True))

    def _model_key(self, timeframe: str) -> str:
        return f"global|{timeframe}"

    def _maybe_bootstrap_from_history(
        self, ctx: StrategyContext, timeframe: str
    ) -> None:
        if self.model_initialized:
            return

        model_key = self._model_key(timeframe)
        loaded = load_model(ctx, self.id, model_key)
        updated_at = None

        if loaded is not None:
            restored_model, updated_at = loaded
            if restored_model is not None:
                self.model = restored_model
                self.model_initialized = True

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
                ohlc = ctx.market_data.get_ohlc(pair, timeframe, lookback=lookback + self.params.lookback_bars)
                if not ohlc or len(ohlc) < 2:
                    continue

                for i in range(self.params.lookback_bars, len(ohlc)):
                    bar_t = ohlc[i]
                    bar_prev = ohlc[i-1]

                    if bar_t.timestamp <= start_ts:
                        continue

                    start_slice = i - self.params.lookback_bars
                    if start_slice < 0:
                        continue

                    window_slice = ohlc[start_slice:i]
                    features = self._compute_features_from_window(window_slice)
                    if not features:
                        continue

                    delta = (bar_t.close - bar_prev.close) / bar_prev.close if bar_prev.close > 0 else 0.0

                    self.model.partial_fit([features], [delta])
                    training_count += 1

        except Exception as exc:
            logger.warning("Error during ML catch-up: %s", exc)

        if training_count > 0:
            logger.info("Caught up ML model with %d examples", training_count)

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

        label = (bar_t.close - bar_prev.close) / bar_prev.close if bar_prev.close > 0 else 0.0

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
                    except Exception as exc:  # pragma: no cover - defensive
                        logger.debug("Model update failed for %s: %s", pair, exc)

            # 2. Predict T
            features = self._extract_features(ctx, pair, timeframe)
            if not features:
                continue

            if not self.model_initialized:
                continue

            try:
                predicted_delta = float(self.model.predict([features])[0])
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("Prediction failed for %s: %s", pair, exc)
                continue

            confidence = self._confidence(predicted_delta)
            position = positions_by_pair.get(pair)
            has_long = bool(position and getattr(position, "base_size", 0) > 0)

            # Check profit threshold
            if predicted_delta > self.params.min_edge_pct:
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
            save_model(
                ctx,
                strategy_id=self.id,
                model_key=model_key,
                label_type="regression",
                framework="sklearn_passive_aggressive_regressor",
                model=self.model,
            )

        return intents
