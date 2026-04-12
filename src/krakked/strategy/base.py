# src/krakked/strategy/base.py

import abc
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from krakked.config import StrategyConfig
from krakked.market_data.api import MarketDataAPI
from krakked.portfolio.manager import PortfolioService
from krakked.strategy.regime import RegimeSnapshot

from .models import StrategyIntent


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
    def __init__(self, config: StrategyConfig):
        self.config = config
        self.id = config.name

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
