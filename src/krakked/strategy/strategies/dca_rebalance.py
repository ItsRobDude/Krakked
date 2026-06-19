# src/krakked/strategy/strategies/dca_rebalance.py

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from krakked.config import StrategyConfig
from krakked.market_data.api import MarketDataAPI
from krakked.market_data.exceptions import DataStaleError
from krakked.portfolio.manager import PortfolioService
from krakked.strategy.base import Strategy, StrategyContext
from krakked.strategy.evaluation import StrategyEvaluationResult
from krakked.strategy.models import StrategyIntent


@dataclass
class DcaRebalanceConfig:
    pairs: List[str]
    target_weights: Dict[str, float]
    rebalance_threshold_pct: float
    dca_interval_minutes: int
    dca_notional_usd: float


class DcaRebalanceStrategy(Strategy):
    requires_closed_bar_context = False

    def __init__(self, base_cfg: StrategyConfig):
        super().__init__(base_cfg)
        self.params = self._parse_config(base_cfg)
        self._last_dca: Optional[datetime] = None

    def _parse_config(self, base_cfg: StrategyConfig) -> DcaRebalanceConfig:
        params = base_cfg.params or {}

        pairs_param = params.get("pairs") or []
        pairs = list(pairs_param) if isinstance(pairs_param, list) else []

        target_weights_param = params.get("target_weights") or {}
        target_weights: Dict[str, float] = (
            dict(target_weights_param) if isinstance(target_weights_param, dict) else {}
        )

        if not pairs and target_weights:
            pairs = list(target_weights.keys())

        if not target_weights and pairs:
            equal_weight = 1.0 / len(pairs)
            target_weights = {pair: equal_weight for pair in pairs}

        weight_sum = sum(target_weights.values())
        if weight_sum <= 0:
            raise ValueError("Target weights must sum to a positive value")

        normalized_weights = {
            pair: weight / weight_sum for pair, weight in target_weights.items()
        }

        rebalance_threshold_pct = float(params.get("rebalance_threshold_pct", 1.0))
        if rebalance_threshold_pct < 0:
            raise ValueError("Rebalance threshold percent must be non-negative")

        dca_interval_minutes = int(params.get("dca_interval_minutes", 60))
        if dca_interval_minutes <= 0:
            raise ValueError("DCA interval minutes must be greater than zero")

        dca_notional_usd = float(params.get("dca_notional_usd", 100.0))
        if dca_notional_usd <= 0:
            raise ValueError("DCA notional USD must be greater than zero")

        return DcaRebalanceConfig(
            pairs=pairs,
            target_weights=normalized_weights,
            rebalance_threshold_pct=rebalance_threshold_pct,
            dca_interval_minutes=dca_interval_minutes,
            dca_notional_usd=dca_notional_usd,
        )

    def warmup(self, market_data: MarketDataAPI, portfolio: PortfolioService) -> None:
        self._last_dca = None

    def generate_intents(self, ctx: StrategyContext) -> List[StrategyIntent]:
        return self.evaluate(ctx).intents

    @staticmethod
    def _diagnostic(
        *,
        reason: str,
        message: str,
        pair: Optional[str] = None,
        timeframe: str,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "status": "no_signal",
            "reason": reason,
            "message": message,
            "timeframe": timeframe,
        }
        if pair:
            payload["pair"] = pair
        if extra:
            payload.update(extra)
        return payload

    def evaluate(self, ctx: StrategyContext) -> StrategyEvaluationResult:
        tf = ctx.timeframe or "1h"
        if self._last_dca:
            elapsed = ctx.now - self._last_dca
            if elapsed < timedelta(minutes=self.params.dca_interval_minutes):
                reason = self._diagnostic(
                    reason="rebalance_interval_not_elapsed",
                    message="DCA rebalance interval has not elapsed",
                    timeframe=tf,
                    extra={
                        "elapsed_seconds": elapsed.total_seconds(),
                        "required_seconds": self.params.dca_interval_minutes * 60,
                    },
                )
                return StrategyEvaluationResult(
                    no_signal_reasons=[reason],
                    context_summaries=[reason],
                    status="no_signal",
                    message=reason["message"],
                )

        intents: List[StrategyIntent] = []
        reasons: List[Dict[str, Any]] = []
        context_summaries: List[Dict[str, Any]] = []

        equity_view = ctx.portfolio.get_equity()
        equity = float(getattr(equity_view, "equity_base", 0.0) or 0.0)
        if equity <= 0:
            reason = self._diagnostic(
                reason="equity_unavailable_or_zero",
                message="Portfolio equity is unavailable or zero",
                timeframe=tf,
                extra={"equity_usd": equity},
            )
            return StrategyEvaluationResult(
                no_signal_reasons=[reason],
                context_summaries=[reason],
                status="no_signal",
                message=reason["message"],
            )

        positions_by_pair_key = {}
        for position in ctx.portfolio.get_positions() or []:
            key = self._pair_key(ctx, getattr(position, "pair", ""))
            if key:
                positions_by_pair_key[key] = position

        pairs = self.params.pairs or ctx.universe
        target_weights = self.params.target_weights

        for pair in pairs:
            target_weight = target_weights.get(pair)
            if target_weight is None:
                reason = self._diagnostic(
                    reason="target_weight_missing",
                    message=f"{pair} has no target DCA weight",
                    pair=pair,
                    timeframe=tf,
                )
                reasons.append(reason)
                context_summaries.append(reason)
                continue

            try:
                price = ctx.market_data.get_latest_price(pair)
            except DataStaleError:
                price = None

            if price is None:
                reason = self._diagnostic(
                    reason="price_unavailable",
                    message=f"{pair} price is unavailable for DCA evaluation",
                    pair=pair,
                    timeframe=tf,
                )
                reasons.append(reason)
                context_summaries.append(reason)
                continue

            position = positions_by_pair_key.get(self._pair_key(ctx, pair))
            base_size = position.base_size if position else 0.0
            current_notional = base_size * price
            target_notional = equity * target_weight

            if target_notional <= 0:
                deviation_pct = 0.0 if current_notional == 0 else 100.0
            else:
                deviation_pct = (
                    (current_notional - target_notional) / target_notional * 100
                )

            if abs(deviation_pct) < self.params.rebalance_threshold_pct:
                reason = self._diagnostic(
                    reason="within_rebalance_threshold",
                    message=f"{pair} is within the DCA rebalance threshold",
                    pair=pair,
                    timeframe=tf,
                    extra={
                        "current_notional": current_notional,
                        "target_notional": target_notional,
                        "deviation_pct": deviation_pct,
                        "rebalance_threshold_pct": self.params.rebalance_threshold_pct,
                    },
                )
                reasons.append(reason)
                context_summaries.append(reason)
                continue

            metadata = {
                "current_notional": current_notional,
                "target_notional": target_notional,
                "deviation_pct": deviation_pct,
            }

            if current_notional < target_notional:
                new_target = min(
                    target_notional, current_notional + self.params.dca_notional_usd
                )
                intent_type = "enter" if current_notional == 0 else "increase"
                intents.append(
                    StrategyIntent(
                        strategy_id=self.id,
                        pair=pair,
                        side="long",
                        intent_type=intent_type,
                        desired_exposure_usd=new_target,
                        confidence=min(1.0, abs(deviation_pct) / 100),
                        timeframe=tf,
                        generated_at=ctx.now,
                        metadata=metadata,
                    )
                )
                context_summaries.append(
                    {
                        "status": "intents_emitted",
                        "pair": pair,
                        "timeframe": tf,
                        "message": f"{pair} is below target DCA allocation",
                        "reason": "below_target_allocation",
                        "current_notional": current_notional,
                        "target_notional": target_notional,
                        "deviation_pct": deviation_pct,
                    }
                )
            else:
                new_target = max(
                    target_notional, current_notional - self.params.dca_notional_usd
                )
                if new_target <= 0:
                    intents.append(
                        StrategyIntent(
                            strategy_id=self.id,
                            pair=pair,
                            side="flat",
                            intent_type="exit",
                            desired_exposure_usd=0.0,
                            confidence=min(1.0, abs(deviation_pct) / 100),
                            timeframe=tf,
                            generated_at=ctx.now,
                            metadata=metadata,
                        )
                    )
                    context_summaries.append(
                        {
                            "status": "intents_emitted",
                            "pair": pair,
                            "timeframe": tf,
                            "message": f"{pair} is above target DCA allocation",
                            "reason": "above_target_allocation_exit",
                            "current_notional": current_notional,
                            "target_notional": target_notional,
                            "deviation_pct": deviation_pct,
                        }
                    )
                else:
                    intents.append(
                        StrategyIntent(
                            strategy_id=self.id,
                            pair=pair,
                            side="long",
                            intent_type="reduce",
                            desired_exposure_usd=new_target,
                            confidence=min(1.0, abs(deviation_pct) / 100),
                            timeframe=tf,
                            generated_at=ctx.now,
                            metadata=metadata,
                        )
                    )
                    context_summaries.append(
                        {
                            "status": "intents_emitted",
                            "pair": pair,
                            "timeframe": tf,
                            "message": f"{pair} is above target DCA allocation",
                            "reason": "above_target_allocation_reduce",
                            "current_notional": current_notional,
                            "target_notional": target_notional,
                            "deviation_pct": deviation_pct,
                        }
                    )

        if intents:
            self._last_dca = ctx.now

        return StrategyEvaluationResult(
            intents=intents,
            no_signal_reasons=[] if intents else reasons,
            context_summaries=context_summaries,
            status="intents_emitted" if intents else "no_signal",
            message=(
                f"Generated {len(intents)} DCA intent(s)"
                if intents
                else (
                    reasons[0]["message"]
                    if reasons
                    else "DCA evaluated without a matching pair"
                )
            ),
        )
