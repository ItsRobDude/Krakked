"""Shared, deterministic ``cl_ord_id`` attribution for live order recovery.

Classifying a Kraken OpenOrders/ClosedOrders lookup into one of four states lets
adoption (recover) and absence-clear paths apply *opposite* fail-closed rules
from a single source of truth:

- ``NONE``        : zero raw candidates returned.
- ``EXACT``       : exactly one raw candidate whose payload echoes the expected
                    ``cl_ord_id``.
- ``UNVERIFIED``  : exactly one raw candidate, but it does not echo the expected
                    ``cl_ord_id`` (missing or mismatched). The endpoint / filter /
                    echo contract is unproven, so this is NOT the same as ``NONE``.
- ``AMBIGUOUS``   : more than one raw candidate, even if one appears to match.

Safety contract (see docs/money-safety-proof-plan.md):

- Adoption is allowed only on ``EXACT``.
- Absence-clear is allowed only when both endpoints are ``NONE``.
- ``UNVERIFIED`` and ``AMBIGUOUS`` must block both adoption and normal clear and
  require an explicit, audited operator force path.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Optional


class CorrelationState(str, Enum):
    NONE = "none"
    EXACT = "exact"
    UNVERIFIED = "unverified"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class CorrelationResult:
    """Classification of a single endpoint's raw lookup result."""

    state: CorrelationState
    raw_count: int
    expected_client_order_id: str
    kraken_order_id: Optional[str] = None
    payload: Optional[Mapping[str, Any]] = None
    reason: str = ""

    @property
    def is_exact(self) -> bool:
        return self.state is CorrelationState.EXACT

    @property
    def is_none(self) -> bool:
        return self.state is CorrelationState.NONE


def payload_client_order_id(payload: Any) -> Optional[str]:
    """Return the echoed client order id from a Kraken order payload, if present.

    Kraken may surface the value at the top level or nested under ``descr``.
    Only a non-empty string is treated as an echoed value.
    """

    if not isinstance(payload, Mapping):
        return None
    candidates = [payload]
    descr = payload.get("descr")
    if isinstance(descr, Mapping):
        candidates.append(descr)
    for source in candidates:
        for key in ("cl_ord_id", "clOrdId"):
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def classify_client_order_id_matches(
    matches: Optional[Mapping[str, Any]],
    *,
    expected_client_order_id: str,
) -> CorrelationResult:
    """Classify a raw ``{kraken_id: payload}`` result map for one endpoint."""

    raw = dict(matches or {})
    raw_count = len(raw)
    expected = str(expected_client_order_id)

    if raw_count == 0:
        return CorrelationResult(
            state=CorrelationState.NONE,
            raw_count=0,
            expected_client_order_id=expected,
            reason="no raw candidates returned",
        )

    if raw_count > 1:
        return CorrelationResult(
            state=CorrelationState.AMBIGUOUS,
            raw_count=raw_count,
            expected_client_order_id=expected,
            reason=f"{raw_count} raw candidates returned",
        )

    kraken_id, payload = next(iter(raw.items()))
    echoed = payload_client_order_id(payload)
    if echoed is None:
        return CorrelationResult(
            state=CorrelationState.UNVERIFIED,
            raw_count=1,
            expected_client_order_id=expected,
            reason="single candidate did not echo cl_ord_id",
        )
    if echoed != expected:
        return CorrelationResult(
            state=CorrelationState.UNVERIFIED,
            raw_count=1,
            expected_client_order_id=expected,
            reason=f"single candidate echoed mismatched cl_ord_id {echoed!r}",
        )

    return CorrelationResult(
        state=CorrelationState.EXACT,
        raw_count=1,
        expected_client_order_id=expected,
        kraken_order_id=str(kraken_id),
        payload=payload,
        reason="exact echoed cl_ord_id match",
    )
