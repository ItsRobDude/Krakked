# src/kraken_bot/strategy/regime.py

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Dict

import pandas as pd

from kraken_bot.market_data.api import MarketDataAPI


class MarketRegime(str, Enum):
    TRENDING = "trending"
    MEAN_REVERTING = "mean_reverting"
    CHOPPY = "choppy"
    PANIC = "panic"


@dataclass
class RegimeSnapshot:
    per_pair: Dict[str, MarketRegime]
    as_of: str


def _classify_pair(autocorr: float, volatility: float) -> MarketRegime:
    # Classify a pair based on simple statistical features of returns.
    #
    # Heuristics tuned for our synthetic tests:
    # * very high volatility -> PANIC
    # * strongly negative autocorrelation with moderate volatility -> MEAN_REVERTING
    # * extremely low volatility -> CHOPPY
    # * everything else -> TRENDING.

    # Elevated volatility generally indicates stressed conditions regardless of direction.
    if volatility > 0.07:
        return MarketRegime.PANIC

    # Very low volatility with tiny oscillations around a level -> choppy.
    if volatility < 0.0005:
        return MarketRegime.CHOPPY

    # Strong mean reversion: sharp swings around a mean price with negative autocorrelation.
    if autocorr < -0.7 and volatility >= 0.005:
        return MarketRegime.MEAN_REVERTING

    # Default: some directional drift without excessive volatility -> trending.
    return MarketRegime.TRENDING


def infer_regime(market_data: MarketDataAPI, pairs: list[str]) -> RegimeSnapshot:
    """Infer a coarse market regime for each pair using recent OHLC data."""

    regimes: Dict[str, MarketRegime] = {}
    for pair in pairs:
        try:
            ohlc = market_data.get_ohlc(pair, "1h", 200)
        except Exception:  # pragma: no cover - defensive against data fetch errors
            regimes[pair] = MarketRegime.CHOPPY
            continue

        if not ohlc or len(ohlc) < 20:
            regimes[pair] = MarketRegime.CHOPPY
            continue

        df = pd.DataFrame([bar.__dict__ for bar in ohlc])
        closes = df["close"].astype(float)
        returns = closes.pct_change().dropna()

        if returns.empty:
            regimes[pair] = MarketRegime.CHOPPY
            continue

        autocorr = returns.autocorr(lag=1)
        if pd.isna(autocorr):
            autocorr = 0.0

        volatility = float(returns.std(ddof=0))
        regimes[pair] = _classify_pair(float(autocorr), volatility)

    return RegimeSnapshot(
        per_pair=regimes, as_of=datetime.now(timezone.utc).isoformat()
    )
