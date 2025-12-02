from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import List

import pandas as pd
from pandas import Series  # type: ignore[attr-defined]

from kraken_bot.config import StrategyConfig
from kraken_bot.strategy.base import Strategy, StrategyContext
from kraken_bot.strategy.models import StrategyIntent


@dataclass
class MeanReversionConfig:
    pairs: List[str]
    timeframe: str
    lookback_bars: int
    band_width_bps: float
    max_positions: int


class MeanReversionStrategy(Strategy):
    def __init__(self, base_cfg: StrategyConfig):
        super().__init__(base_cfg)
        params = base_cfg.params or {}
        pairs_param = params.get("pairs") or ["BTC/USD", "ETH/USD"]
        pairs = list(pairs_param) if isinstance(pairs_param, list) else ["BTC/USD", "ETH/USD"]
        self.params = MeanReversionConfig(
            pairs=pairs,
            timeframe=params.get("timeframe", "1h"),
            lookback_bars=max(int(params.get("lookback_bars", 50)), 5),
            band_width_bps=max(float(params.get("band_width_bps", 150.0)), 0.0),
            max_positions=max(int(params.get("max_positions", 2)), 1),
        )

    def warmup(self, market_data, portfolio) -> None:
        # No warmup required for static bands
        return None

    def _count_open_positions(self, positions) -> int:
        return sum(1 for pos in positions if getattr(pos, "base_size", 0) > 0)

    def generate_intents(self, ctx: StrategyContext) -> List[StrategyIntent]:
        intents: List[StrategyIntent] = []

        timeframe = ctx.timeframe or self.params.timeframe
        pairs = self.params.pairs or ctx.universe

        positions = ctx.portfolio.get_positions() or []
        positions_by_pair = {pos.pair: pos for pos in positions if getattr(pos, "base_size", 0) > 0}
        open_positions_count = self._count_open_positions(positions_by_pair.values())

        for pair in pairs:
            ohlc = ctx.market_data.get_ohlc(pair, timeframe, lookback=self.params.lookback_bars)
            if not ohlc or len(ohlc) < self.params.lookback_bars:
                continue

            df = pd.DataFrame([asdict(bar) for bar in ohlc])
            close_series: Series = df["close"].tail(self.params.lookback_bars)

            ma = float(close_series.mean())
            std = float(close_series.std(ddof=0))
            if ma <= 0:
                continue

            band = ma * (self.params.band_width_bps / 10_000)
            upper_band = ma + band
            lower_band = ma - band

            last_close = float(close_series.iloc[-1])
            position = positions_by_pair.get(pair)
            has_long = bool(position and position.base_size > 0)

            if not has_long and open_positions_count >= self.params.max_positions:
                continue

            if last_close < lower_band and not has_long:
                confidence = min(1.0, (lower_band - last_close) / ma)
                intents.append(
                    StrategyIntent(
                        strategy_id=self.id,
                        pair=pair,
                        side="long",
                        intent_type="enter",
                        desired_exposure_usd=None,
                        confidence=confidence,
                        timeframe=timeframe,
                        generated_at=ctx.now,
                        metadata={
                            "ma": ma,
                            "std": std,
                            "upper_band": upper_band,
                            "lower_band": lower_band,
                            "last_close": last_close,
                        },
                    )
                )
                open_positions_count += 1
                continue

            if has_long and last_close >= ma:
                confidence = min(1.0, abs(last_close - ma) / ma)
                intents.append(
                    StrategyIntent(
                        strategy_id=self.id,
                        pair=pair,
                        side="flat",
                        intent_type="exit",
                        desired_exposure_usd=0.0,
                        confidence=confidence,
                        timeframe=timeframe,
                        generated_at=ctx.now,
                        metadata={
                            "ma": ma,
                            "std": std,
                            "upper_band": upper_band,
                            "lower_band": lower_band,
                            "last_close": last_close,
                        },
                    )
                )

        return intents
