# src/krakked/strategy/regime.py

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, Optional

import pandas as pd

from krakked.market_data.api import MarketDataAPI


class MarketRegime(str, Enum):
    TRENDING = "trending"
    MEAN_REVERTING = "mean_reverting"
    CHOPPY = "choppy"
    PANIC = "panic"


@dataclass
class RegimeSnapshot:
    per_pair: Dict[str, MarketRegime]
    as_of: str

    def regime_for(self, pair: str) -> Optional[MarketRegime]:
        for candidate in _pair_alias_candidates(pair):
            regime = self.per_pair.get(candidate)
            if regime is not None:
                return regime
        return None


def _pair_alias_candidates(pair: str) -> list[str]:
    normalized = str(pair).strip().upper()
    candidates = [normalized]

    if "/" in normalized:
        base, quote = normalized.split("/", 1)
    else:
        quote = "USD" if normalized.endswith("USD") else ""
        base = normalized[: -len(quote)] if quote else normalized

    base_variants = {base}
    if base == "BTC":
        base_variants.add("XBT")
    elif base == "XBT":
        base_variants.add("BTC")

    quote_variants = {quote} if quote else set()
    if quote == "USD":
        quote_variants.add("ZUSD")
    elif quote == "ZUSD":
        quote_variants.add("USD")

    for base_variant in base_variants:
        if not quote_variants:
            candidates.append(base_variant)
            continue
        for quote_variant in quote_variants:
            candidates.append(f"{base_variant}/{quote_variant}")
            candidates.append(f"{base_variant}{quote_variant}")

    ordered: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in ordered:
            ordered.append(candidate)
    return ordered


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
