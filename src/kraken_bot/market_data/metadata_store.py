"""Utilities for persisting market metadata to disk."""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Iterable, List

from kraken_bot.config import PairMetadata, get_config_dir


class PairMetadataStore:
    """Persists :class:`PairMetadata` entries to a JSON file."""

    def __init__(self, path: Path | None = None):
        self._path = Path(path) if path else get_config_dir() / "pair_metadata.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        return self._path

    def save(self, pairs: Iterable[PairMetadata]) -> None:
        """Write the provided metadata entries to disk."""
        payload: List[dict] = [asdict(p) for p in pairs if is_dataclass(p)]
        if payload:
            self._path.write_text(json.dumps(payload, indent=2, sort_keys=True))

    def load(self) -> List[PairMetadata]:
        """Load persisted metadata if it exists, returning an empty list otherwise."""
        if not self._path.exists():
            return []

        data = json.loads(self._path.read_text())
        return [PairMetadata(**item) for item in data]
