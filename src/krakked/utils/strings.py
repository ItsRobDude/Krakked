"""String collection helpers."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def unique_strings(values: Iterable[Any | None]) -> list[str]:
    """Return non-empty string values in first-seen order."""

    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = str(value).strip() if value is not None else ""
        if not cleaned or cleaned in seen:
            continue
        result.append(cleaned)
        seen.add(cleaned)
    return result
