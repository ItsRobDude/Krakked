"""Helpers for Kraken order `userref`.

Kraken's REST API expects `userref` to be an **integer** (int32). Internally,
Krakked sometimes uses human-readable strategy tags (e.g. "alpha:1h") as a
reference.

This module provides a single, deterministic conversion to a valid integer so:
- the execution router never crashes on non-numeric refs
- order tagging remains stable across restarts/machines

If the input is already an int (or a numeric string), it's validated and used.
Otherwise, we derive a stable int32 from a SHA-256 hash.
"""

from __future__ import annotations

import hashlib
from typing import Optional, Union

# Kraken documents `userref` as an int32 on requests.
# Use the positive range (1..2_147_483_647) to avoid negative values.
_MAX_INT32_POS = 2_147_483_647

# Reserve the *upper* half of the int32 positive space for derived userrefs so
# that explicit userrefs (often small integers) almost never collide.
_DERIVED_OFFSET = 1_073_741_824  # 2^30
_DERIVED_RANGE = _MAX_INT32_POS - _DERIVED_OFFSET + 1

# Best-effort collision detection inside a single process.
# (Collisions are already extremely unlikely; this exists to fail fast if it happens.)
_DERIVED_SEEN: dict[int, str] = {}


def resolve_userref(value: Optional[Union[str, int]]) -> Optional[int]:
    """Convert a possibly-string user reference into a valid Kraken int32.

    Rules:
      - None -> None
      - int -> validated int
      - numeric string -> int
      - other string -> deterministic hash-derived int in [1, 2_147_483_647]

    Raises:
      - ValueError: for numeric refs outside the valid int32 positive range.
    """

    if value is None:
        return None

    # Normalize ints
    if isinstance(value, int):
        _validate_int32_pos(value)
        return int(value)

    # Normalize strings
    s = str(value).strip()
    if s == "":
        return None

    # Purely numeric -> use as-is
    if s.isdigit():
        as_int = int(s)
        _validate_int32_pos(as_int)
        return as_int

    # Derive deterministic int32 from hash
    digest = hashlib.sha256(s.encode("utf-8")).digest()
    raw = int.from_bytes(digest[:8], "big", signed=False)
    derived = (raw % _DERIVED_RANGE) + _DERIVED_OFFSET

    # Detect collisions within this process (different strings mapping to same int).
    previous = _DERIVED_SEEN.get(derived)
    if previous is not None and previous != s:
        raise ValueError(
            f"Derived userref collision: '{s}' and '{previous}' both map to {derived}. "
            "Set explicit StrategyConfig.userref values to resolve."
        )
    _DERIVED_SEEN[derived] = s

    return int(derived)


def _validate_int32_pos(v: int) -> None:
    if v < 1 or v > _MAX_INT32_POS:
        raise ValueError(f"userref must be in [1, {_MAX_INT32_POS}], got {v}")
