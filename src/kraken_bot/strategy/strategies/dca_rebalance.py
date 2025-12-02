# src/kraken_bot/strategy/strategies/dca_rebalance.py

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from kraken_bot.config import StrategyConfig
from kraken_bot.market_data.api import MarketDataAPI
from kraken_bot.portfolio.manager import PortfolioService
from kraken_bot.strategy.base import Strategy, StrategyContext
from kraken_bot.strategy.models import StrategyIntent


@dataclass
class DcaRebalanceConfig:
    pairs: List[str]
    target_weights: Dict[str, float]
    rebalance_threshold_pct: float
    dca_interval_minutes: int
    dca_notional_usd: float


class DcaRebalanceStrategy(Strategy):
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

        normalized_weights = {pair: weight / weight_sum for pair, weight in target_weights.items()}

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
        if self._last_dca:
            elapsed = ctx.now - self._last_dca
            if elapsed < timedelta(minutes=self.params.dca_interval_minutes):
                return []

        intents: List[StrategyIntent] = []

        equity_view = ctx.portfolio.get_equity()
        equity = equity_view.equity_base

        positions = {pos.pair: pos for pos in (ctx.portfolio.get_positions() or [])}

        pairs = self.params.pairs or ctx.universe
        target_weights = self.params.target_weights

        for pair in pairs:
            target_weight = target_weights.get(pair)
            if target_weight is None:
                continue

            price = ctx.market_data.get_latest_price(pair)
            if price is None:
                continue

            position = positions.get(pair)
            base_size = position.base_size if position else 0.0
            current_notional = base_size * price
            target_notional = equity * target_weight

            if target_notional <= 0:
                deviation_pct = 0.0 if current_notional == 0 else 100.0
            else:
                deviation_pct = (current_notional - target_notional) / target_notional * 100

            if abs(deviation_pct) < self.params.rebalance_threshold_pct:
                continue

            metadata = {
                "current_notional": current_notional,
                "target_notional": target_notional,
                "deviation_pct": deviation_pct,
            }

            if current_notional < target_notional:
                new_target = min(target_notional, current_notional + self.params.dca_notional_usd)
                intent_type = "enter" if current_notional == 0 else "increase"
                intents.append(
                    StrategyIntent(
                        strategy_id=self.id,
                        pair=pair,
                        side="long",
                        intent_type=intent_type,
                        desired_exposure_usd=new_target,
                        confidence=min(1.0, abs(deviation_pct) / 100),
                        timeframe=ctx.timeframe,
                        generated_at=ctx.now,
                        metadata=metadata,
                    )
                )
            else:
                new_target = max(target_notional, current_notional - self.params.dca_notional_usd)
                if new_target <= 0:
                    intents.append(
                        StrategyIntent(
                            strategy_id=self.id,
                            pair=pair,
                            side="flat",
                            intent_type="exit",
                            desired_exposure_usd=0.0,
                            confidence=min(1.0, abs(deviation_pct) / 100),
                            timeframe=ctx.timeframe,
                            generated_at=ctx.now,
                            metadata=metadata,
                        )
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
                            timeframe=ctx.timeframe,
                            generated_at=ctx.now,
                            metadata=metadata,
                        )
                    )

        if intents:
            self._last_dca = ctx.now

        return intents
