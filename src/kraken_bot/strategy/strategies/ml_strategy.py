from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from kraken_bot.config import StrategyConfig
from kraken_bot.market_data.api import MarketDataAPI
from kraken_bot.market_data.exceptions import DataStaleError
from kraken_bot.portfolio.manager import PortfolioService
from kraken_bot.strategy.base import Strategy, StrategyContext
from kraken_bot.strategy.models import StrategyIntent
from kraken_bot.strategy.strategies.ml_models import PassiveAggressiveClassifier

logger = logging.getLogger(__name__)


@dataclass
class AIPredictorConfig:
    pairs: List[str]
    timeframe: str
    lookback_bars: int
    short_window: int
    long_window: int
    target_exposure_usd: Optional[float]
    continuous_learning: bool


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
        )

        self.model = PassiveAggressiveClassifier(max_iter=1000, tol=1e-3)
        self.classes = [0, 1]
        self.model_initialized = False
        self._last_observation: Dict[Tuple[str, str], Tuple[List[float], float]] = {}

    def warmup(self, market_data: MarketDataAPI, portfolio: PortfolioService) -> None:
        return None

    def _learning_enabled(self) -> bool:
        return bool(self.config.params.get("continuous_learning", True))

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

    def _confidence(self, features: List[float]) -> float:
        try:
            score = float(self.model.decision_function([features])[0])
        except Exception:
            return 0.5
        magnitude = abs(score)
        return 1.0 / (1.0 + math.exp(-magnitude))

    def generate_intents(self, ctx: StrategyContext) -> List[StrategyIntent]:
        intents: List[StrategyIntent] = []

        timeframe = ctx.timeframe or self.params.timeframe
        pairs = self.params.pairs or ctx.universe

        positions = ctx.portfolio.get_positions() or []
        positions_by_pair = {
            pos.pair: pos for pos in positions if getattr(pos, "base_size", 0) > 0
        }

        for pair in pairs:
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

            last_obs = self._last_observation.get((pair, timeframe))
            if last_obs:
                last_features, last_price = last_obs
                label = 1 if current_price > last_price else 0
                try:
                    if not self.model_initialized:
                        self.model.partial_fit([last_features], [label], classes=self.classes)
                        self.model_initialized = True
                    elif self._learning_enabled():
                        self.model.partial_fit([last_features], [label])
                except Exception as exc:  # pragma: no cover - defensive
                    logger.debug("Model update failed for %s: %s", pair, exc)

            features = self._extract_features(ctx, pair, timeframe)
            if not features:
                continue

            self._last_observation[(pair, timeframe)] = (features, current_price)

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
                side = "long"
                intent_type = "increase" if has_long else "enter"
                desired_exposure = self.params.target_exposure_usd
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

        return intents
