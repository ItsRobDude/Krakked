"""Shared ML model imports with graceful fallbacks when scikit-learn is unavailable."""

from __future__ import annotations

import importlib.util
import logging
from typing import Iterable, List, Protocol

logger = logging.getLogger(__name__)

try:
    _sklearn_spec = importlib.util.find_spec("sklearn")
except ModuleNotFoundError:  # pragma: no cover - defensive
    _sklearn_spec = None

class _PassiveAggressiveClassifierProtocol(Protocol):
    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN001, ANN002
        ...

    def partial_fit(
        self, X: Iterable[Iterable[float]], y: List[float], classes=None
    ) -> "_PassiveAggressiveClassifierProtocol":
        ...

    def predict(self, X: Iterable[Iterable[float]]) -> list[float]:
        ...

    def decision_function(self, X: Iterable[Iterable[float]]) -> list[float]:
        ...


class _PassiveAggressiveRegressorProtocol(Protocol):
    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN001, ANN002
        ...

    def partial_fit(
        self, X: Iterable[Iterable[float]], y: List[float], classes=None
    ) -> "_PassiveAggressiveRegressorProtocol":
        ...

    def predict(self, X: Iterable[Iterable[float]]) -> list[float]:
        ...


_PassiveAggressiveClassifierImpl: type
_PassiveAggressiveRegressorImpl: type

if _sklearn_spec:
    # Real sklearn models at runtime. mypy doesn’t have stubs for scikit-learn,
    # so we tell it to treat these imports as untyped.
    from sklearn.linear_model import (  # type: ignore[import-untyped]
        PassiveAggressiveClassifier as _SklearnPassiveAggressiveClassifier,
        PassiveAggressiveRegressor as _SklearnPassiveAggressiveRegressor,
    )
    _PassiveAggressiveClassifierImpl = _SklearnPassiveAggressiveClassifier
    _PassiveAggressiveRegressorImpl = _SklearnPassiveAggressiveRegressor
else:  # pragma: no cover - fallback path
    logger.warning(
        "scikit-learn not installed; using lightweight Passive-Aggressive fallbacks"
    )

    class _BasePassiveAggressive:
        def __init__(self, *args, **kwargs) -> None:  # noqa: D401, ANN001, ANN002
            """
            Minimal stub that matches the scikit-learn API surface the bot uses.
            """
            self._last_value: float = 0.0

        def partial_fit(
            self,
            X: Iterable[Iterable[float]],
            y: List[float],
            classes=None,
        ) -> "_BasePassiveAggressive":  # noqa: ANN001
            if y:
                self._last_value = float(y[-1])
            return self

        def predict(self, X: Iterable[Iterable[float]]) -> list[float]:
            return [self._last_value for _ in X]

        def decision_function(self, X: Iterable[Iterable[float]]) -> list[float]:
            return [self._last_value if self._last_value != 0 else -1.0 for _ in X]

    class _PassiveAggressiveClassifierFallback(_BasePassiveAggressive):
        pass

    class _PassiveAggressiveRegressorFallback(_BasePassiveAggressive):
        pass

    _PassiveAggressiveClassifierImpl = _PassiveAggressiveClassifierFallback
    _PassiveAggressiveRegressorImpl = _PassiveAggressiveRegressorFallback

# Single public aliases used everywhere else
class PassiveAggressiveClassifier(_PassiveAggressiveClassifierImpl):
    pass


class PassiveAggressiveRegressor(_PassiveAggressiveRegressorImpl):
    pass


__all__ = ["PassiveAggressiveClassifier", "PassiveAggressiveRegressor"]
