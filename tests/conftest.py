"""Test configuration for environments without optional heavy dependencies."""

from __future__ import annotations

import importlib.util
from types import SimpleNamespace

import pytest

REQUIRED_MODULES = [
    "pandas",
    "pyarrow",
    "fastapi",
    "pydantic",
    "starlette",
]

missing = [mod for mod in REQUIRED_MODULES if importlib.util.find_spec(mod) is None]
if missing:
    pytest.skip(
        "Missing optional dependencies: " + ", ".join(sorted(missing)),
        allow_module_level=True,
    )


@pytest.fixture
def inactive_risk_status():
    """Returns a callable that reports an inactive kill switch."""

    return lambda: SimpleNamespace(kill_switch_active=False)
