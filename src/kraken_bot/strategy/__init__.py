"""Strategy orchestration package.

This package wires configured strategies into a risk engine to produce
execution plans that downstream services can execute. Use
:class:`StrategyEngine` to run a decision cycle and persist the resulting plan.
"""

from .engine import StrategyEngine, StrategyRiskEngine

__all__ = ["StrategyEngine", "StrategyRiskEngine"]
