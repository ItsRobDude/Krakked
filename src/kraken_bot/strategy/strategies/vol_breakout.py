"""Volatility breakout strategy implementation."""

from dataclasses import asdict, dataclass
from typing import cast

import pandas as pd
from pandas import Series  # type: ignore[attr-defined]

from kraken_bot.config import StrategyConfig
from kraken_bot.strategy.base import Strategy, StrategyContext
from kraken_bot.strategy.models import StrategyIntent
from kraken_bot.strategy.risk import compute_atr


@dataclass
class VolBreakoutConfig:
    pairs: list[str]
    lookback_bars: int
    min_compression_bps: float
    breakout_multiple: float


class VolBreakoutStrategy(Strategy):
    def __init__(self, base_cfg: StrategyConfig):
        super().__init__(base_cfg)
        params = base_cfg.params or {}
        self.params = VolBreakoutConfig(
            pairs=params.get("pairs", []),
            lookback_bars=max(int(params.get("lookback_bars", 20)), 5),
            min_compression_bps=max(
                float(params.get("min_compression_bps", 10.0)), 0.0
            ),
            breakout_multiple=max(float(params.get("breakout_multiple", 1.5)), 0.0),
        )

    def warmup(self, market_data, portfolio) -> None:
        # No warmup required
        pass

    def generate_intents(self, ctx: StrategyContext) -> list[StrategyIntent]:
        intents: list[StrategyIntent] = []

        tf = ctx.timeframe or "1h"
        pairs = self.params.pairs or ctx.universe

        for pair in pairs:
            ohlc = ctx.market_data.get_ohlc(
                pair, tf, lookback=self.params.lookback_bars + 10
            )
            if not ohlc or len(ohlc) < self.params.lookback_bars:
                continue

            df: pd.DataFrame = pd.DataFrame([asdict(b) for b in ohlc])
            atr = compute_atr(df, window=self.params.lookback_bars)
            if atr <= 0:
                continue

            window_df = df.tail(self.params.lookback_bars)
            high_series = cast(Series, window_df["high"])
            low_series = cast(Series, window_df["low"])
            close_series = cast(Series, window_df["close"])

            high = float(high_series.max())
            low = float(low_series.min())
            last_close = float(close_series.iloc[-1])
            compression_bps = (
                ((high - low) / last_close) * 10_000 if last_close else 0.0
            )

            if compression_bps > self.params.min_compression_bps:
                continue

            prev_high = float(high_series.iloc[-2])

            breakout = last_close > prev_high + self.params.breakout_multiple * atr
            if breakout:
                side = "long"
                intent_type = "enter"
                confidence = 0.8
            else:
                side = "flat"
                intent_type = "exit"
                confidence = 0.5

            intents.append(
                StrategyIntent(
                    strategy_id=self.id,
                    pair=pair,
                    side=side,
                    intent_type=intent_type,
                    desired_exposure_usd=None,
                    confidence=confidence,
                    timeframe=tf,
                    generated_at=ctx.now,
                    metadata={
                        "atr": atr,
                        "compression_bps": compression_bps,
                        "prev_high": float(prev_high),
                        "last_close": float(last_close),
                    },
                )
            )

        return intents
