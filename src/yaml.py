import json
from typing import Any


def safe_load(stream: str) -> Any:  # pragma: no cover - shim
    try:
        return json.loads(stream)
    except Exception:
        return {}


def safe_dump(data: Any, *_, **__):  # pragma: no cover - shim
    return json.dumps(data)
