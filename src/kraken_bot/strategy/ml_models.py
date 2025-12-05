"""Shared ML model imports with graceful fallbacks when scikit-learn is unavailable."""
from __future__ import annotations

import importlib.util
import logging
from typing import Iterable, List

logger = logging.getLogger(__name__)

try:
    _sklearn_spec = importlib.util.find_spec("sklearn")
except ModuleNotFoundError:  # pragma: no cover - defensive
    _sklearn_spec = None

if _sklearn_spec:
    from sklearn.linear_model import PassiveAggressiveClassifier, PassiveAggressiveRegressor  # type: ignore
else:  # pragma: no cover - fallback path
    logger.warning(
        "scikit-learn not installed; using lightweight Passive-Aggressive fallbacks"
    )

    class _BasePassiveAggressive:
        def __init__(self, *args, **kwargs):
            self._last_value: float = 0.0

        def partial_fit(
            self, X: Iterable[Iterable[float]], y: List[float], classes=None
        ) -> "_BasePassiveAggressive":
            if y:
                self._last_value = float(y[-1])
            return self

    class PassiveAggressiveClassifier(_BasePassiveAggressive):
        def predict(self, X: Iterable[Iterable[float]]):
            label = 1 if self._last_value >= 0.5 else 0
            return [label for _ in X]

        def decision_function(self, X: Iterable[Iterable[float]]):
            score = self._last_value if self._last_value != 0 else -1.0
            return [score for _ in X]

    class PassiveAggressiveRegressor(_BasePassiveAggressive):
        def predict(self, X: Iterable[Iterable[float]]):
            return [float(self._last_value) for _ in X]

__all__ = ["PassiveAggressiveClassifier", "PassiveAggressiveRegressor"]
