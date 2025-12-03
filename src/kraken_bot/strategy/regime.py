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
    # Elevated volatility generally indicates stressed conditions regardless of direction
    if volatility > 0.07:
        return MarketRegime.PANIC

    if autocorr > 0.2:
        return MarketRegime.TRENDING

    if autocorr < -0.1:
        return MarketRegime.MEAN_REVERTING

    if volatility < 0.01:
        return MarketRegime.CHOPPY

    return MarketRegime.CHOPPY


def infer_regime(market_data: MarketDataAPI, pairs: list[str]) -> RegimeSnapshot:
    """Infer a coarse market regime for each pair using recent OHLC data."""

    regimes: Dict[str, MarketRegime] = {}
    for pair in pairs:
        try:
            ohlc = market_data.get_ohlc(pair, "1h", lookback=200)
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

    return RegimeSnapshot(per_pair=regimes, as_of=datetime.now(timezone.utc).isoformat())
