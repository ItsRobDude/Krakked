"""Strategy orchestration package.

This package wires configured strategies into a risk engine to produce
execution plans that downstream services can execute. Use
:class:`StrategyEngine` to run a decision cycle and persist the resulting plan.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - import-time cycle guard for type checkers
    from kraken_bot.strategy.engine import StrategyEngine, StrategyRiskEngine

__all__ = ["StrategyEngine", "StrategyRiskEngine"]


def __getattr__(name: str) -> Any:  # pragma: no cover - thin lazy import shim
    if name in __all__:
        engine_module = import_module("kraken_bot.strategy.engine")
        return getattr(engine_module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
