# src/kraken_bot/strategy/strategies/demo_strategy.py

from dataclasses import dataclass
from typing import List, Dict, Any
from datetime import datetime, timezone

from kraken_bot.config import StrategyConfig
from kraken_bot.strategy.base import Strategy, StrategyContext
from kraken_bot.strategy.models import StrategyIntent
from kraken_bot.market_data.api import MarketDataAPI
from kraken_bot.portfolio.manager import PortfolioService

@dataclass
class TrendFollowingConfig:
    timeframes: List[str]
    ma_fast: int
    ma_slow: int
    min_confidence: float

class TrendFollowingStrategy(Strategy):
    def __init__(self, base_cfg: StrategyConfig):
        super().__init__(base_cfg)
        # Parse params into typed config
        self.params = TrendFollowingConfig(
            timeframes=base_cfg.params.get("timeframes", ["1h"]),
            ma_fast=base_cfg.params.get("ma_fast", 20),
            ma_slow=base_cfg.params.get("ma_slow", 50),
            min_confidence=base_cfg.params.get("min_confidence", 0.5)
        )

    def warmup(self, market_data: MarketDataAPI, portfolio: PortfolioService) -> None:
        # Pre-load data if needed
        pass

    def generate_intents(self, ctx: StrategyContext) -> List[StrategyIntent]:
        intents = []

        tf = ctx.timeframe

        for pair in ctx.universe:
            # Get OHLC
            ohlc = ctx.market_data.get_ohlc(pair, tf, lookback=self.params.ma_slow + 10)
            if not ohlc or len(ohlc) < self.params.ma_slow:
                continue

            # Compute indicators
            closes = [b.close for b in ohlc]

            # Simple SMA
            def sma(data, period):
                if len(data) < period: return 0
                return sum(data[-period:]) / period

            fast_ma = sma(closes, self.params.ma_fast)
            slow_ma = sma(closes, self.params.ma_slow)

            # Logic: Golden Cross
            # Prev Check?
            # For simplicity: If Fast > Slow -> Long. Else Flat.

            side = "flat"
            confidence = 0.0

            if fast_ma > slow_ma:
                side = "long"
                confidence = 0.8
            else:
                side = "flat"
                confidence = 0.5

            # Determine Intent Type
            # Need to know current position?
            # Strategy can look at portfolio to decide intent_type "enter" vs "hold" vs "exit"
            # But the requirement says Risk Engine does sizing.
            # Strategy just says "I want to be LONG".

            # If side is long, intent is "enter" (or "maintain long").
            # If side is flat, intent is "exit".

            intent_type = "enter" if side == "long" else "exit"

            intents.append(StrategyIntent(
                strategy_id=self.id,
                pair=pair,
                side=side,
                intent_type=intent_type,
                desired_exposure_usd=None, # Let risk engine size it
                confidence=confidence,
                timeframe=tf,
                generated_at=ctx.now,
                metadata={"ma_fast": fast_ma, "ma_slow": slow_ma}
            ))

        return intents
