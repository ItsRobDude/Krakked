"""Kraken Futures symbol selection helpers."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from krakked.market_data.api import ASSET_ALIASES


def instrument_candidates(
    pair: str,
    instruments: Sequence[Mapping[str, Any]],
    tickers_by_symbol: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    base, quote = split_pair(pair)
    base_aliases = asset_aliases(base)
    quote_aliases = asset_aliases(quote)
    candidates: list[dict[str, Any]] = []
    for instrument in instruments:
        symbol = str(instrument.get("symbol") or "").upper()
        if not symbol:
            continue
        inst_quote = str(instrument.get("quote") or "").upper()
        inst_base = str(instrument.get("base") or "").upper()
        inst_pair = str(instrument.get("pair") or "").upper().replace(":", "/")
        family = contract_family(symbol)
        is_perpetual = symbol.startswith(("PI_", "PF_")) or str(
            instrument.get("type") or ""
        ).lower().endswith("perpetual")
        symbol_base, symbol_quote = symbol_base_quote(symbol, quote_aliases)
        pair_matches = any(
            inst_pair == f"{alias}/{quote}"
            for alias in base_aliases
            for quote in quote_aliases
        )
        base_match = (
            inst_base in base_aliases or symbol_base in base_aliases or pair_matches
        )
        quote_match = (
            inst_quote in quote_aliases
            or any(symbol.endswith(alias) for alias in quote_aliases)
            or symbol_quote in quote_aliases
        )
        if not is_perpetual or not base_match or not quote_match:
            continue
        ticker = tickers_by_symbol.get(symbol, {})
        row = dict(instrument)
        row.update(
            {
                "symbol": symbol,
                "contract_family": family,
                "ticker": dict(ticker),
                "suspended": bool(ticker.get("suspended", False)),
                "fundingRate": ticker.get("fundingRate"),
                "fundingRatePrediction": ticker.get("fundingRatePrediction"),
                "markPrice": ticker.get("markPrice"),
                "indexPrice": ticker.get("indexPrice"),
            }
        )
        candidates.append(row)
    return sorted(candidates, key=candidate_sort_key)


def select_candidate(candidates: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    for candidate in candidates:
        if bool(candidate.get("tradeable", True)) and not bool(
            candidate.get("suspended")
        ):
            return dict(candidate)
    return dict(candidates[0]) if candidates else None


def split_pair(pair: str) -> tuple[str, str]:
    text = str(pair or "").strip().upper()
    if "/" in text:
        base, quote = text.split("/", 1)
    elif ":" in text:
        base, quote = text.split(":", 1)
    elif len(text) > 3:
        base, quote = text[:-3], text[-3:]
    else:
        raise ValueError(f"Invalid pair: {pair}")
    return base.strip(), quote.strip()


def asset_aliases(asset: str) -> set[str]:
    normalized = str(asset or "").strip().upper()
    aliases = {normalized}
    if normalized in ASSET_ALIASES:
        aliases.add(ASSET_ALIASES[normalized])
    for alias, target in ASSET_ALIASES.items():
        if target == normalized:
            aliases.add(alias)
    return {alias for alias in aliases if alias}


def contract_family(symbol: str) -> str:
    return str(symbol or "").split("_", 1)[0].upper()


def symbol_base_quote(
    symbol: str, quote_aliases: set[str]
) -> tuple[str | None, str | None]:
    text = str(symbol or "").upper()
    if "_" in text:
        text = text.split("_", 1)[1]
    for quote in sorted(quote_aliases, key=len, reverse=True):
        if text.endswith(quote) and len(text) > len(quote):
            return text[: -len(quote)], quote
    return None, None


def candidate_sort_key(candidate: Mapping[str, Any]) -> tuple[int, str]:
    family = str(candidate.get("contract_family") or "")
    family_rank = 0 if family == "PF" else 1 if family == "PI" else 2
    return (family_rank, str(candidate.get("symbol") or ""))


__all__ = [
    "asset_aliases",
    "candidate_sort_key",
    "contract_family",
    "instrument_candidates",
    "select_candidate",
    "split_pair",
    "symbol_base_quote",
]
