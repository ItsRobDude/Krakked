"""Internal helpers for comparing pair identities across display/canonical forms."""

from __future__ import annotations

from typing import Any


def pair_key(market_data: Any, pair: Any) -> str:
    raw = str(pair or "").strip()
    if not raw:
        return ""

    normalize_pair = getattr(market_data, "normalize_pair", None)
    if callable(normalize_pair):
        try:
            normalized = normalize_pair(raw)
        except Exception:  # pragma: no cover - defensive against adapter failures
            normalized = None
        if isinstance(normalized, str) and normalized.strip():
            return _fallback_pair_key(normalized)

    return _fallback_pair_key(raw)


def _fallback_pair_key(pair: str) -> str:
    return str(pair).strip().upper().replace("/", "")
