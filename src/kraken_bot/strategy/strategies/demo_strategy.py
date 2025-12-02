# src/kraken_bot/strategy/strategies/demo_strategy.py

from dataclasses import dataclass
from typing import Any, List, Optional

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
    regime_timeframe: str
    min_trend_strength_bps: float
    risk_profile: str
    pairs: Optional[List[str]] = None

class TrendFollowingStrategy(Strategy):
    def __init__(self, base_cfg: StrategyConfig):
        super().__init__(base_cfg)
        self.params = self._parse_config(base_cfg)

    def _parse_config(self, base_cfg: StrategyConfig) -> TrendFollowingConfig:
        params = base_cfg.params or {}

        risk_profile = params.get("risk_profile", "balanced")
        profile_defaults = {
            "conservative": {"ma_fast": 20, "ma_slow": 50, "min_trend_strength_bps": 15.0},
            "balanced": {"ma_fast": 10, "ma_slow": 20, "min_trend_strength_bps": 10.0},
            "aggressive": {"ma_fast": 5, "ma_slow": 13, "min_trend_strength_bps": 8.0},
        }
        defaults = profile_defaults.get(risk_profile, profile_defaults["balanced"])

        ma_fast = params.get("ma_fast", defaults["ma_fast"])
        ma_slow = params.get("ma_slow", defaults["ma_slow"])
        min_trend_strength_bps = params.get("min_trend_strength_bps", defaults["min_trend_strength_bps"])

        return TrendFollowingConfig(
            timeframes=params.get("timeframes", ["1h"]),
            ma_fast=ma_fast,
            ma_slow=ma_slow,
            regime_timeframe=params.get("regime_timeframe", "1d"),
            min_trend_strength_bps=min_trend_strength_bps,
            risk_profile=risk_profile,
            pairs=params.get("pairs"),
        )

    def warmup(self, market_data: MarketDataAPI, portfolio: PortfolioService) -> None:
        # Pre-load data if needed
        pass

    def generate_intents(self, ctx: StrategyContext) -> List[StrategyIntent]:
        intents = []

        tf = ctx.timeframe
        pairs = self.params.pairs or ctx.universe

        def sma(data: List[float], period: int) -> float:
            if len(data) < period or period <= 0:
                return 0.0
            return sum(data[-period:]) / period

        def trend_strength_bps(fast: float, slow: float) -> float:
            if slow <= 0:
                return 0.0
            return (fast - slow) / slow * 10000.0

        min_liquidity = None
        if hasattr(ctx.portfolio, "app_config") and getattr(ctx.portfolio.app_config, "risk", None):
            min_liquidity = ctx.portfolio.app_config.risk.min_liquidity_24h_usd

        positions: List[Any] = []
        if hasattr(ctx.portfolio, "get_positions"):
            positions = ctx.portfolio.get_positions() or []

        for pair in pairs:
            metadata = ctx.market_data.get_pair_metadata(pair)
            if metadata and min_liquidity is not None:
                if getattr(metadata, "liquidity_24h_usd", 0) < min_liquidity:
                    continue

            ohlc = ctx.market_data.get_ohlc(pair, tf, lookback=self.params.ma_slow + 10)
            if not ohlc or len(ohlc) < self.params.ma_slow:
                continue

            regime_ohlc = ctx.market_data.get_ohlc(
                pair, self.params.regime_timeframe, lookback=self.params.ma_slow + 10
            )
            if not regime_ohlc or len(regime_ohlc) < self.params.ma_slow:
                continue

            closes = [b.close for b in ohlc]
            regime_closes = [b.close for b in regime_ohlc]

            fast_ma = sma(closes, self.params.ma_fast)
            slow_ma = sma(closes, self.params.ma_slow)
            regime_ma = sma(regime_closes, self.params.ma_slow)

            strength_bps = trend_strength_bps(fast_ma, slow_ma)

            higher_tf_uptrend = regime_ma > 0 and regime_closes[-1] > regime_ma
            local_uptrend = fast_ma > slow_ma and strength_bps >= self.params.min_trend_strength_bps

            side = "long" if higher_tf_uptrend and local_uptrend else "flat"

            # Confidence scales with how much stronger the fast MA is relative to the slow MA
            confidence = 0.0
            if local_uptrend and higher_tf_uptrend:
                confidence = min(1.0, max(0.1, strength_bps / (self.params.min_trend_strength_bps * 2)))

            existing_position = next(
                (pos for pos in positions if getattr(pos, "pair", None) == pair), None
            )
            has_position = existing_position is not None and getattr(existing_position, "base_size", 0) > 0

            if side == "long":
                intent_type = "increase" if has_position else "enter"
            else:
                intent_type = "reduce" if has_position else "exit"

            intents.append(StrategyIntent(
                strategy_id=self.id,
                pair=pair,
                side=side,
                intent_type=intent_type,
                desired_exposure_usd=None,
                confidence=confidence,
                timeframe=tf,
                generated_at=ctx.now,
                metadata={
                    "fast_ma": fast_ma,
                    "slow_ma": slow_ma,
                    "regime_ma": regime_ma,
                    "trend_strength_bps": strength_bps,
                    "regime_trend": "uptrend" if higher_tf_uptrend else "neutral",
                    "risk_profile": self.params.risk_profile,
                    "regime_timeframe": self.params.regime_timeframe,
                }
            ))

        return intents
