# src/krakked/strategy/base.py

import abc
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from krakked.config import StrategyConfig
from krakked.market_data.api import MarketDataAPI
from krakked.portfolio.manager import PortfolioService
from krakked.strategy.regime import RegimeSnapshot

from .evaluation import StrategyEvaluationResult
from .models import StrategyIntent
from .pair_keys import pair_key


@dataclass
class StrategyContext:
    now: datetime
    universe: List[str]  # pairs this strategy is allowed to trade
    market_data: MarketDataAPI  # for pulling OHLC and prices
    portfolio: PortfolioService  # for current positions and exposures
    timeframe: Optional[str] = (
        None  # the timeframe for this decision cycle ("1h", etc.)
    )
    regime: Optional[RegimeSnapshot] = None


class Strategy(abc.ABC):
    requires_closed_bar_context = True

    def __init__(self, config: StrategyConfig):
        self.config = config
        self.id = config.name

    def _pair_key(self, ctx: StrategyContext, pair: Any) -> str:
        return pair_key(ctx.market_data, pair)

    def _owned_positions_by_pair_key(
        self, ctx: StrategyContext, *, positive_only: bool = True
    ) -> Dict[str, Any]:
        positions = ctx.portfolio.get_positions() or []
        positions_by_pair: Dict[str, Any] = {}
        for position in positions:
            if getattr(position, "strategy_tag", None) != self.id:
                continue
            if positive_only and getattr(position, "base_size", 0) <= 0:
                continue
            key = self._pair_key(ctx, getattr(position, "pair", ""))
            if key and key not in positions_by_pair:
                positions_by_pair[key] = position
        return positions_by_pair

    @abc.abstractmethod
    def warmup(self, market_data: MarketDataAPI, portfolio: PortfolioService) -> None:
        """
        Optional pre-run (e.g. build indicators from history)
        """
        pass

    @abc.abstractmethod
    def generate_intents(self, ctx: StrategyContext) -> List[StrategyIntent]:
        """
        Called on each decision cycle, returns intents
        """
        pass

    def explain_no_signal(self, ctx: StrategyContext) -> List[Dict[str, Any]]:
        """Return display-only reasons when a fresh evaluation emits no intents."""
        return []

    def evaluate(self, ctx: StrategyContext) -> StrategyEvaluationResult:
        """Evaluate once and return intents plus display-only diagnostics."""
        intents = self.generate_intents(ctx)
        return StrategyEvaluationResult(
            intents=intents,
            no_signal_reasons=[] if intents else self.explain_no_signal(ctx),
        )
