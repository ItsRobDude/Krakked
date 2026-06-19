# src/krakked/strategy/strategies/demo_strategy.py

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from krakked.config import StrategyConfig
from krakked.market_data.api import MarketDataAPI
from krakked.portfolio.manager import PortfolioService
from krakked.strategy.base import Strategy, StrategyContext
from krakked.strategy.evaluation import StrategyEvaluationResult
from krakked.strategy.models import StrategyIntent
from krakked.strategy.regime import MarketRegime


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
            "conservative": {
                "ma_fast": 20,
                "ma_slow": 50,
                "min_trend_strength_bps": 15.0,
            },
            "balanced": {"ma_fast": 10, "ma_slow": 20, "min_trend_strength_bps": 10.0},
            "aggressive": {"ma_fast": 5, "ma_slow": 13, "min_trend_strength_bps": 8.0},
        }
        defaults = profile_defaults.get(risk_profile, profile_defaults["balanced"])

        ma_fast = params.get("ma_fast", defaults["ma_fast"])
        ma_slow = params.get("ma_slow", defaults["ma_slow"])
        min_trend_strength_bps = params.get(
            "min_trend_strength_bps", defaults["min_trend_strength_bps"]
        )

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

    @staticmethod
    def _sma(data: List[float], period: int) -> float:
        if len(data) < period or period <= 0:
            return 0.0
        return sum(data[-period:]) / period

    @staticmethod
    def _trend_strength_bps(fast: float, slow: float) -> float:
        if slow <= 0:
            return 0.0
        return (fast - slow) / slow * 10000.0

    def _threshold_for_regime(self, regime_type: MarketRegime | None) -> float:
        threshold = self.params.min_trend_strength_bps
        if regime_type == MarketRegime.CHOPPY:
            threshold *= 1.5
        elif regime_type == MarketRegime.MEAN_REVERTING:
            threshold *= 1.25
        elif regime_type == MarketRegime.PANIC:
            threshold *= 2.0
        return threshold

    def _min_liquidity(self, ctx: StrategyContext) -> Optional[float]:
        if hasattr(ctx.portfolio, "app_config") and getattr(
            ctx.portfolio.app_config, "risk", None
        ):
            return ctx.portfolio.app_config.risk.min_liquidity_24h_usd
        return None

    def generate_intents(self, ctx: StrategyContext) -> List[StrategyIntent]:
        return self.evaluate(ctx).intents

    def evaluate(self, ctx: StrategyContext) -> StrategyEvaluationResult:
        intents: List[StrategyIntent] = []
        reasons: List[Dict[str, Any]] = []
        context_summaries: List[Dict[str, Any]] = []

        tf = ctx.timeframe or "1h"
        pairs = self.params.pairs or ctx.universe
        min_liquidity = self._min_liquidity(ctx)

        positions_by_pair_key = self._owned_positions_by_pair_key(ctx)

        for pair in pairs:
            base: Dict[str, Any] = {"pair": pair, "timeframe": tf}
            metadata = ctx.market_data.get_pair_metadata(pair)
            if metadata and min_liquidity is not None:
                liquidity_24h_usd = getattr(metadata, "liquidity_24h_usd", None)
                if liquidity_24h_usd is not None and liquidity_24h_usd < min_liquidity:
                    reason = {
                        **base,
                        "status": "no_signal",
                        "reason": "liquidity_below_minimum",
                        "message": f"{pair} below minimum liquidity for trend entry",
                        "liquidity_24h_usd": liquidity_24h_usd,
                        "min_liquidity_24h_usd": min_liquidity,
                    }
                    reasons.append(reason)
                    context_summaries.append(reason)
                    continue

            ohlc = ctx.market_data.get_ohlc(pair, tf, lookback=self.params.ma_slow + 10)
            if not ohlc or len(ohlc) < self.params.ma_slow:
                reason = {
                    **base,
                    "status": "no_signal",
                    "reason": "insufficient_bars",
                    "message": f"{pair} has too few {tf} bars for trend MA",
                    "bars": len(ohlc or []),
                    "required_bars": self.params.ma_slow,
                }
                reasons.append(reason)
                context_summaries.append(reason)
                continue

            regime_ohlc = ctx.market_data.get_ohlc(
                pair, self.params.regime_timeframe, lookback=self.params.ma_slow + 10
            )
            if not regime_ohlc or len(regime_ohlc) < self.params.ma_slow:
                reason = {
                    **base,
                    "status": "no_signal",
                    "reason": "regime_timeframe_insufficient_bars",
                    "message": (
                        f"{pair} has too few {self.params.regime_timeframe} bars "
                        "for regime trend"
                    ),
                    "regime_timeframe": self.params.regime_timeframe,
                    "bars": len(regime_ohlc or []),
                    "required_bars": self.params.ma_slow,
                }
                reasons.append(reason)
                context_summaries.append(reason)
                continue

            closes = [b.close for b in ohlc]
            regime_closes = [b.close for b in regime_ohlc]

            fast_ma = self._sma(closes, self.params.ma_fast)
            slow_ma = self._sma(closes, self.params.ma_slow)
            regime_ma = self._sma(regime_closes, self.params.ma_slow)

            strength_bps = self._trend_strength_bps(fast_ma, slow_ma)

            regime_type = None
            if ctx.regime:
                regime_type = ctx.regime.regime_for(pair)

            threshold = self._threshold_for_regime(regime_type)

            higher_tf_uptrend = regime_ma > 0 and regime_closes[-1] > regime_ma
            local_uptrend = fast_ma > slow_ma and strength_bps >= threshold

            side = "long" if higher_tf_uptrend and local_uptrend else "flat"

            # Confidence scales with how much stronger the fast MA is relative to the slow MA
            confidence = 0.0
            if local_uptrend and higher_tf_uptrend:
                confidence = min(
                    1.0,
                    max(0.1, strength_bps / (self.params.min_trend_strength_bps * 2)),
                )

            existing_position = positions_by_pair_key.get(self._pair_key(ctx, pair))
            has_position = (
                existing_position is not None
                and getattr(existing_position, "base_size", 0) > 0
            )

            if side == "long":
                intent_type = "increase" if has_position else "enter"
            else:
                if not has_position:
                    if not higher_tf_uptrend:
                        reason = {
                            **base,
                            "status": "no_signal",
                            "reason": "daily_regime_not_uptrend",
                            "message": f"{pair} regime timeframe is not in an uptrend",
                            "regime_timeframe": self.params.regime_timeframe,
                            "regime_ma": regime_ma,
                            "last_regime_close": regime_closes[-1],
                            "regime": regime_type.value if regime_type else None,
                        }
                    elif not local_uptrend:
                        reason = {
                            **base,
                            "status": "no_signal",
                            "reason": "local_trend_below_threshold",
                            "message": f"{pair} local trend is below entry threshold",
                            "trend_strength_bps": strength_bps,
                            "required_strength_bps": threshold,
                            "fast_ma": fast_ma,
                            "slow_ma": slow_ma,
                            "regime": regime_type.value if regime_type else None,
                        }
                    else:
                        reason = {
                            **base,
                            "status": "no_signal",
                            "reason": "no_entry_signal",
                            "message": f"{pair} did not meet trend entry rules",
                        }
                    reasons.append(reason)
                    context_summaries.append(reason)
                    continue
                intent_type = "reduce"

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
                        "fast_ma": fast_ma,
                        "slow_ma": slow_ma,
                        "regime_ma": regime_ma,
                        "trend_strength_bps": strength_bps,
                        "regime_trend": "uptrend" if higher_tf_uptrend else "neutral",
                        "risk_profile": self.params.risk_profile,
                        "regime_timeframe": self.params.regime_timeframe,
                        "regime": regime_type.value if regime_type else None,
                    },
                )
            )
            context_summaries.append(
                {
                    **base,
                    "status": "intents_emitted",
                    "message": f"{pair} trend rules emitted {intent_type}",
                    "reason": f"trend_{intent_type}",
                    "intents_emitted": 1,
                    "trend_strength_bps": strength_bps,
                    "regime": regime_type.value if regime_type else None,
                }
            )

        return StrategyEvaluationResult(
            intents=intents,
            no_signal_reasons=[] if intents else reasons,
            context_summaries=context_summaries,
            status="intents_emitted" if intents else "no_signal",
            message=(
                f"Generated {len(intents)} trend intent(s)"
                if intents
                else (reasons[0]["message"] if reasons else "No trend signal")
            ),
        )

    def explain_no_signal(self, ctx: StrategyContext) -> List[Dict[str, Any]]:
        return self.evaluate(ctx).no_signal_reasons
