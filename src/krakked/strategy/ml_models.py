"""Shared ML model helpers with graceful fallbacks when scikit-learn is unavailable."""

from __future__ import annotations

from dataclasses import dataclass
import importlib.util
import inspect
import logging
import math
from typing import Any, Iterable, Protocol

logger = logging.getLogger(__name__)

ML_STANDARD_SCALER_SCHEMA_VERSION = "standard_v1"
ML_STANDARD_SCALER_MODEL_KEY_SUFFIX = "scalerstdv1"
DEFAULT_REGRESSION_EPSILON_PCT = 0.001
DEFAULT_REGRESSION_MODEL_BACKEND = "pa"
DEFAULT_SGD_L2_ALPHA = 0.0001
DEFAULT_SGD_LEARNING_RATE_INITIAL = 0.001
REGRESSION_MODEL_BACKENDS = frozenset(
    {"pa", "sgd_huber", "sgd_squared_error"}
)
REGRESSION_MODEL_FRAMEWORKS = {
    "pa": "sklearn_passive_aggressive_regressor",
    "sgd_huber": "sklearn_sgd_regressor_huber",
    "sgd_squared_error": "sklearn_sgd_regressor_squared_error",
}

try:
    _sklearn_spec = importlib.util.find_spec("sklearn")
except ModuleNotFoundError:  # pragma: no cover - defensive
    _sklearn_spec = None


class _PassiveAggressiveClassifierProtocol(Protocol):
    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN001, ANN002
        ...

    def partial_fit(
        self, X: Iterable[Iterable[float]], y: Iterable[float], classes=None
    ) -> "_PassiveAggressiveClassifierProtocol": ...

    def predict(self, X: Iterable[Iterable[float]]) -> list[float]: ...

    def decision_function(self, X: Iterable[Iterable[float]]) -> list[float]: ...


class _PassiveAggressiveRegressorProtocol(Protocol):
    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN001, ANN002
        ...

    def partial_fit(
        self, X: Iterable[Iterable[float]], y: Iterable[float], classes=None
    ) -> "_PassiveAggressiveRegressorProtocol": ...

    def predict(self, X: Iterable[Iterable[float]]) -> list[float]: ...


class _SGDRegressorProtocol(Protocol):
    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN001, ANN002
        ...

    def partial_fit(
        self, X: Iterable[Iterable[float]], y: Iterable[float], classes=None
    ) -> "_SGDRegressorProtocol": ...

    def predict(self, X: Iterable[Iterable[float]]) -> list[float]: ...


class _StandardScalerProtocol(Protocol):
    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN001, ANN002
        ...

    def partial_fit(
        self, X: Iterable[Iterable[float]]
    ) -> "_StandardScalerProtocol": ...

    def transform(self, X: Iterable[Iterable[float]]) -> list[list[float]]: ...


_PassiveAggressiveClassifierImpl: type
_PassiveAggressiveRegressorImpl: type
_SGDRegressorImpl: type
_StandardScalerImpl: type

if _sklearn_spec:
    # Real sklearn models at runtime. mypy doesn’t have stubs for scikit-learn,
    # so we tell it to treat these imports as untyped.
    from sklearn.linear_model import (
        PassiveAggressiveClassifier as _SklearnPassiveAggressiveClassifier,  # pyright: ignore[reportMissingTypeStubs]; type: ignore[import-untyped]
    )
    from sklearn.linear_model import (
        PassiveAggressiveRegressor as _SklearnPassiveAggressiveRegressor,  # pyright: ignore[reportMissingTypeStubs]; type: ignore[import-untyped]
    )
    from sklearn.linear_model import (
        SGDRegressor as _SklearnSGDRegressor,  # pyright: ignore[reportMissingTypeStubs]; type: ignore[import-untyped]
    )
    from sklearn.preprocessing import (
        StandardScaler as _SklearnStandardScaler,  # pyright: ignore[reportMissingTypeStubs]; type: ignore[import-untyped]
    )

    _PassiveAggressiveClassifierImpl = _SklearnPassiveAggressiveClassifier
    _PassiveAggressiveRegressorImpl = _SklearnPassiveAggressiveRegressor
    _SGDRegressorImpl = _SklearnSGDRegressor
    _StandardScalerImpl = _SklearnStandardScaler
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
            y: Iterable[float],
            classes=None,
        ) -> "_BasePassiveAggressive":  # noqa: ANN001
            y_values = list(y)
            if y_values:
                self._last_value = float(y_values[-1])
            return self

        def predict(self, X: Iterable[Iterable[float]]) -> list[float]:
            return [self._last_value for _ in X]

        def decision_function(self, X: Iterable[Iterable[float]]) -> list[float]:
            return [self._last_value if self._last_value != 0 else -1.0 for _ in X]

    class _PassiveAggressiveClassifierFallback(_BasePassiveAggressive):
        pass

    class _PassiveAggressiveRegressorFallback(_BasePassiveAggressive):
        pass

    class _SGDRegressorFallback(_BasePassiveAggressive):
        pass

    class _StandardScalerFallback:
        def __init__(self, *args, **kwargs) -> None:  # noqa: ANN001, ANN002
            self.mean_: list[float] = []
            self.scale_: list[float] = []
            self.n_samples_seen_ = 0

        def partial_fit(
            self, X: Iterable[Iterable[float]]
        ) -> "_StandardScalerFallback":
            rows = _as_2d_float_list(X)
            if rows and not self.mean_:
                width = len(rows[0])
                self.mean_ = [0.0] * width
                self.scale_ = [1.0] * width
            self.n_samples_seen_ += len(rows)
            return self

        def transform(self, X: Iterable[Iterable[float]]) -> list[list[float]]:
            return _as_2d_float_list(X)

    _PassiveAggressiveClassifierImpl = _PassiveAggressiveClassifierFallback
    _PassiveAggressiveRegressorImpl = _PassiveAggressiveRegressorFallback
    _SGDRegressorImpl = _SGDRegressorFallback
    _StandardScalerImpl = _StandardScalerFallback


# Single public aliases used everywhere else
class PassiveAggressiveClassifier(_PassiveAggressiveClassifierImpl):
    pass


class PassiveAggressiveRegressor(_PassiveAggressiveRegressorImpl):
    pass


class SGDRegressor(_SGDRegressorImpl):
    pass


class StandardScaler(_StandardScalerImpl):
    pass


def _as_2d_float_list(X: Iterable[Iterable[float]]) -> list[list[float]]:
    rows: list[list[float]] = []
    for row in X:
        rows.append([float(value) for value in row])
    return rows


def _format_model_key_number(value: float) -> str:
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return text.replace(".", "p")


def _positive_or_default(value: float, default: float) -> float:
    return value if math.isfinite(value) and value > 0 else default


def regression_model_backend(value: object) -> str:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in REGRESSION_MODEL_BACKENDS:
            return normalized
    return DEFAULT_REGRESSION_MODEL_BACKEND


def classifier_model_config_key() -> str:
    return f"pa_cls_{ML_STANDARD_SCALER_MODEL_KEY_SUFFIX}"


def regression_model_config_key(
    epsilon_pct: float,
    *,
    model_backend: object = DEFAULT_REGRESSION_MODEL_BACKEND,
    sgd_l2_alpha: float = DEFAULT_SGD_L2_ALPHA,
    sgd_learning_rate_initial: float = DEFAULT_SGD_LEARNING_RATE_INITIAL,
) -> str:
    backend = regression_model_backend(model_backend)
    safe_epsilon = epsilon_pct if math.isfinite(epsilon_pct) else 0.0
    if backend == "pa":
        return (
            "pa_reg"
            f"_eps{_format_model_key_number(max(safe_epsilon, 0.0))}"
            f"_{ML_STANDARD_SCALER_MODEL_KEY_SUFFIX}"
        )

    safe_alpha = sgd_l2_alpha if math.isfinite(sgd_l2_alpha) else 0.0
    safe_eta = _positive_or_default(
        sgd_learning_rate_initial,
        DEFAULT_SGD_LEARNING_RATE_INITIAL,
    )
    key = (
        f"{backend}"
        f"_alpha{_format_model_key_number(max(safe_alpha, 0.0))}"
        f"_eta{_format_model_key_number(max(safe_eta, 0.0))}"
    )
    if backend == "sgd_huber":
        key += f"_eps{_format_model_key_number(max(safe_epsilon, 0.0))}"
    return f"{key}_{ML_STANDARD_SCALER_MODEL_KEY_SUFFIX}"


def regression_model_framework(model_backend: object) -> str:
    return REGRESSION_MODEL_FRAMEWORKS[
        regression_model_backend(model_backend)
    ]


def create_regression_model_bundle(
    *,
    model_backend: object = DEFAULT_REGRESSION_MODEL_BACKEND,
    epsilon_pct: float = DEFAULT_REGRESSION_EPSILON_PCT,
    sgd_l2_alpha: float = DEFAULT_SGD_L2_ALPHA,
    sgd_learning_rate_initial: float = DEFAULT_SGD_LEARNING_RATE_INITIAL,
) -> MLOnlineModelBundle:
    backend = regression_model_backend(model_backend)
    if backend == "pa":
        model = PassiveAggressiveRegressor(
            max_iter=1000,
            tol=1e-3,
            epsilon=epsilon_pct,
        )
    else:
        kwargs: dict[str, object] = {
            "loss": "huber" if backend == "sgd_huber" else "squared_error",
            "penalty": "l2",
            "alpha": max(float(sgd_l2_alpha), 0.0),
            "learning_rate": "constant",
            "eta0": _positive_or_default(
                float(sgd_learning_rate_initial),
                DEFAULT_SGD_LEARNING_RATE_INITIAL,
            ),
            "random_state": 42,
        }
        if backend == "sgd_huber":
            kwargs["epsilon"] = max(float(epsilon_pct), 0.0)
        model = SGDRegressor(**kwargs)
    return MLOnlineModelBundle(model=model, scaler=StandardScaler())


@dataclass
class MLOnlineModelBundle:
    """Pair an online sklearn model with the scaler state used to train it."""

    model: Any
    scaler: Any
    scaler_schema_version: str = ML_STANDARD_SCALER_SCHEMA_VERSION
    scaler_initialized: bool = False

    def _scaled(self, X: Iterable[Iterable[float]]) -> list[list[float]]:
        rows = _as_2d_float_list(X)
        if not self.scaler_initialized:
            return rows
        transformed = self.scaler.transform(rows)
        return _as_2d_float_list(transformed)

    def partial_fit(
        self,
        X: Iterable[Iterable[float]],
        y: Iterable[float],
        classes=None,  # noqa: ANN001
    ) -> "MLOnlineModelBundle":
        rows = _as_2d_float_list(X)
        y_values = list(y)
        self.scaler.partial_fit(rows)
        self.scaler_initialized = True
        scaled = _as_2d_float_list(self.scaler.transform(rows))
        if classes is not None:
            self.model.partial_fit(scaled, y_values, classes=classes)
        else:
            self.model.partial_fit(scaled, y_values)
        return self

    def predict(self, X: Iterable[Iterable[float]]) -> Any:
        return self.model.predict(self._scaled(X))

    def decision_function(self, X: Iterable[Iterable[float]]) -> Any:
        if not hasattr(self.model, "decision_function"):
            raise AttributeError(
                "decision_function is only available for classifier-like models"
            )
        return self.model.decision_function(self._scaled(X))

    def _delegated_attr(self, name: str) -> Any:
        if not hasattr(self.model, name):
            raise AttributeError(name)
        return getattr(self.model, name)

    @property
    def coef_(self) -> Any:
        return self._delegated_attr("coef_")

    @property
    def intercept_(self) -> Any:
        return self._delegated_attr("intercept_")

    @property
    def n_iter_(self) -> Any:
        return self._delegated_attr("n_iter_")

    @property
    def t_(self) -> Any:
        return self._delegated_attr("t_")

    @property
    def classes_(self) -> Any:
        return self._delegated_attr("classes_")


def unwrap_online_model(model: object) -> object:
    if isinstance(model, MLOnlineModelBundle):
        return model.model
    return model


def is_passive_aggressive_classifier_model(model: object) -> bool:
    return isinstance(unwrap_online_model(model), PassiveAggressiveClassifier)


def is_passive_aggressive_regressor_model(model: object) -> bool:
    return isinstance(unwrap_online_model(model), PassiveAggressiveRegressor)


def is_sgd_regressor_model(model: object) -> bool:
    return isinstance(unwrap_online_model(model), SGDRegressor)


def is_regression_model_for_backend(model: object, model_backend: object) -> bool:
    backend = regression_model_backend(model_backend)
    if backend == "pa":
        return is_passive_aggressive_regressor_model(model)
    return is_sgd_regressor_model(model)


def supports_partial_fit_sample_weight(model: object) -> bool:
    partial_fit = getattr(model, "partial_fit", None)
    if partial_fit is None:
        return False
    try:
        signature = inspect.signature(partial_fit)
    except (TypeError, ValueError):
        return False
    return "sample_weight" in signature.parameters


__all__ = [
    "DEFAULT_REGRESSION_MODEL_BACKEND",
    "DEFAULT_REGRESSION_EPSILON_PCT",
    "DEFAULT_SGD_L2_ALPHA",
    "DEFAULT_SGD_LEARNING_RATE_INITIAL",
    "MLOnlineModelBundle",
    "ML_STANDARD_SCALER_SCHEMA_VERSION",
    "PassiveAggressiveClassifier",
    "PassiveAggressiveRegressor",
    "REGRESSION_MODEL_BACKENDS",
    "SGDRegressor",
    "StandardScaler",
    "classifier_model_config_key",
    "create_regression_model_bundle",
    "is_regression_model_for_backend",
    "is_passive_aggressive_classifier_model",
    "is_passive_aggressive_regressor_model",
    "is_sgd_regressor_model",
    "regression_model_backend",
    "regression_model_config_key",
    "regression_model_framework",
    "supports_partial_fit_sample_weight",
    "unwrap_online_model",
]
