from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List

import pandas as pd
from pandas import Series  # type: ignore[attr-defined]

from krakked.config import StrategyConfig
from krakked.strategy.base import Strategy, StrategyContext
from krakked.strategy.evaluation import StrategyEvaluationResult
from krakked.strategy.models import StrategyIntent
from krakked.strategy.regime import MarketRegime


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
        pairs_param = params.get("pairs") or ["XBTUSD", "ETHUSD"]
        pairs = (
            list(pairs_param) if isinstance(pairs_param, list) else ["XBTUSD", "ETHUSD"]
        )
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
        return self.evaluate(ctx).intents

    def evaluate(self, ctx: StrategyContext) -> StrategyEvaluationResult:
        intents: List[StrategyIntent] = []
        reasons: List[Dict[str, Any]] = []
        context_summaries: List[Dict[str, Any]] = []

        timeframe = ctx.timeframe or self.params.timeframe
        pairs = self.params.pairs or ctx.universe

        positions_by_pair = self._owned_positions_by_pair_key(ctx)
        open_positions_count = self._count_open_positions(positions_by_pair.values())

        for pair in pairs:
            base: Dict[str, Any] = {"pair": pair, "timeframe": timeframe}
            pair_key = self._pair_key(ctx, pair)
            ohlc = ctx.market_data.get_ohlc(
                pair, timeframe, lookback=self.params.lookback_bars
            )
            if not ohlc or len(ohlc) < self.params.lookback_bars:
                reason = {
                    **base,
                    "status": "no_signal",
                    "reason": "insufficient_bars",
                    "message": f"{pair} has too few {timeframe} bars for mean reversion",
                    "bars": len(ohlc or []),
                    "required_bars": self.params.lookback_bars,
                }
                reasons.append(reason)
                context_summaries.append(reason)
                continue

            df = pd.DataFrame([asdict(bar) for bar in ohlc])
            close_series: Series = df["close"].tail(self.params.lookback_bars)

            ma = float(close_series.mean())
            std = float(close_series.std(ddof=0))
            if ma <= 0:
                reason = {
                    **base,
                    "status": "no_signal",
                    "reason": "invalid_moving_average",
                    "message": f"{pair} mean reversion baseline is invalid",
                    "ma": ma,
                }
                reasons.append(reason)
                context_summaries.append(reason)
                continue

            pair_regime = None
            if ctx.regime:
                pair_regime = ctx.regime.regime_for(pair)

            band = ma * (self.params.band_width_bps / 10_000)
            upper_band = ma + band
            lower_band = ma - band

            last_close = float(close_series.iloc[-1])
            position = positions_by_pair.get(pair_key)
            has_long = bool(position and position.base_size > 0)

            if not has_long and open_positions_count >= self.params.max_positions:
                reason = {
                    **base,
                    "status": "no_signal",
                    "reason": "max_positions_reached",
                    "message": "Mean reversion max positions already reached",
                    "open_positions": open_positions_count,
                    "max_positions": self.params.max_positions,
                }
                reasons.append(reason)
                context_summaries.append(reason)
                continue

            if last_close < lower_band and not has_long:
                if (
                    pair_regime is not None
                    and pair_regime != MarketRegime.MEAN_REVERTING
                ):
                    reason = {
                        **base,
                        "status": "no_signal",
                        "reason": "regime_not_mean_reverting",
                        "message": (
                            f"{pair} is below the band but regime is not mean reverting"
                        ),
                        "regime": pair_regime.value,
                        "last_close": last_close,
                        "lower_band": lower_band,
                        "ma": ma,
                        "std": std,
                    }
                    reasons.append(reason)
                    context_summaries.append(reason)
                    continue
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
                            "regime": pair_regime.value if pair_regime else None,
                        },
                    )
                )
                context_summaries.append(
                    {
                        **base,
                        "status": "intents_emitted",
                        "reason": "below_lower_band",
                        "message": f"{pair} is below the mean-reversion band",
                        "intents_emitted": 1,
                        "last_close": last_close,
                        "lower_band": lower_band,
                        "ma": ma,
                        "std": std,
                        "regime": pair_regime.value if pair_regime else None,
                    }
                )
                open_positions_count += 1
                continue

            if not has_long and last_close >= lower_band:
                reason = {
                    **base,
                    "status": "no_signal",
                    "reason": "not_below_lower_band",
                    "message": f"{pair} is not below the lower mean-reversion band",
                    "last_close": last_close,
                    "lower_band": lower_band,
                    "upper_band": upper_band,
                    "ma": ma,
                    "std": std,
                    "regime": pair_regime.value if pair_regime else None,
                }
                reasons.append(reason)
                context_summaries.append(reason)
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
                            "regime": pair_regime.value if pair_regime else None,
                        },
                    )
                )
                context_summaries.append(
                    {
                        **base,
                        "status": "intents_emitted",
                        "reason": "reverted_to_mean",
                        "message": f"{pair} reverted to the mean for exit",
                        "intents_emitted": 1,
                        "last_close": last_close,
                        "ma": ma,
                        "std": std,
                        "regime": pair_regime.value if pair_regime else None,
                    }
                )
                continue

            if has_long and last_close < ma:
                reason = {
                    **base,
                    "status": "no_signal",
                    "reason": "exit_not_at_mean",
                    "message": f"{pair} has not reverted to the mean for exit",
                    "last_close": last_close,
                    "ma": ma,
                    "std": std,
                    "regime": pair_regime.value if pair_regime else None,
                }
                reasons.append(reason)
                context_summaries.append(reason)
                continue

        return StrategyEvaluationResult(
            intents=intents,
            no_signal_reasons=[] if intents else reasons,
            context_summaries=context_summaries,
            status="intents_emitted" if intents else "no_signal",
            message=(
                f"Generated {len(intents)} mean-reversion intent(s)"
                if intents
                else (reasons[0]["message"] if reasons else "No mean-reversion signal")
            ),
        )

    def explain_no_signal(self, ctx: StrategyContext) -> List[Dict[str, Any]]:
        return self.evaluate(ctx).no_signal_reasons
