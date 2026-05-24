"""Helpers for identifying stale persisted ML artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import math
from types import SimpleNamespace
from typing import Iterable, Optional

from krakked.config import AppConfig, StrategyConfig
from krakked.portfolio.store import MLArtifactGroup
from krakked.strategy.features import ML_FEATURE_SCHEMA_VERSION
from krakked.strategy.ml_labels import label_config_from_context
from krakked.strategy.ml_models import (
    DEFAULT_REGRESSION_EPSILON_PCT,
    classifier_model_config_key,
    regression_model_config_key,
)

ML_PRUNABLE_STRATEGY_TYPES = {
    "machine_learning",
    "machine_learning_alt",
    "machine_learning_regression",
}


@dataclass(frozen=True)
class MLArtifactPruneCandidate:
    group: MLArtifactGroup
    stale_reason: str

    def to_dict(self) -> dict[str, object]:
        payload = self.group.to_dict()
        payload["stale_reason"] = self.stale_reason
        return payload


def _label_context(config: AppConfig) -> SimpleNamespace:
    return SimpleNamespace(portfolio=SimpleNamespace(app_config=config))


def _coerce_timeframes(value: object) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(timeframe) for timeframe in value if timeframe]
    if value:
        return [str(value)]
    return []


def _strategy_timeframes(strat_cfg: StrategyConfig) -> list[str]:
    params = strat_cfg.params or {}
    timeframes = _coerce_timeframes(params.get("timeframes"))
    timeframes.extend(_coerce_timeframes(params.get("timeframe")))
    if not timeframes:
        timeframes = ["1h"]
    return list(dict.fromkeys(timeframes))


def _feature_key() -> str:
    return f"features_{ML_FEATURE_SCHEMA_VERSION}"


def _label_suffix(config: AppConfig, strat_cfg: StrategyConfig) -> str:
    return label_config_from_context(
        strat_cfg.params or {},
        _label_context(config),
    ).model_key_suffix()


def _nonnegative_float(value: object, default: float) -> float:
    if not isinstance(value, (int, float, str)):
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(parsed):
        return default
    return max(parsed, 0.0)


def _regression_model_suffix(strat_cfg: StrategyConfig) -> str:
    params = strat_cfg.params or {}
    epsilon_pct = _nonnegative_float(
        params.get("regression_epsilon_pct"),
        DEFAULT_REGRESSION_EPSILON_PCT,
    )
    return regression_model_config_key(epsilon_pct)


def _classifier_keys(config: AppConfig, strat_cfg: StrategyConfig) -> set[str]:
    return {
        f"global|{timeframe}|{_feature_key()}|{_label_suffix(config, strat_cfg)}|"
        f"{classifier_model_config_key()}"
        for timeframe in _strategy_timeframes(strat_cfg)
    }


def _regression_keys(strat_cfg: StrategyConfig) -> set[str]:
    return {
        f"global|{timeframe}|{_feature_key()}|{_regression_model_suffix(strat_cfg)}"
        for timeframe in _strategy_timeframes(strat_cfg)
    }


def _classify_global_key(
    group: MLArtifactGroup,
    *,
    expected_keys: set[str],
    expected_timeframes: set[str],
    expected_label_suffix: Optional[str] = None,
    expected_model_suffix: Optional[str] = None,
) -> Optional[str]:
    if group.model_key in expected_keys:
        return None

    parts = group.model_key.split("|")
    expected_parts = 5 if expected_label_suffix is not None else 4
    if len(parts) != expected_parts:
        return "model_key_format_mismatch"
    if parts[0] != "global":
        return "model_scope_mismatch"
    if parts[1] not in expected_timeframes:
        return "timeframe_mismatch"
    if parts[2] != _feature_key():
        return "feature_schema_mismatch"
    if expected_label_suffix is not None and parts[3] != expected_label_suffix:
        return "label_config_mismatch"
    model_suffix = parts[4] if expected_label_suffix is not None else parts[3]
    if expected_model_suffix is not None and model_suffix != expected_model_suffix:
        return "model_config_mismatch"
    return "model_key_mismatch"


def _configured_pairs(strat_cfg: StrategyConfig) -> list[str]:
    pairs = (strat_cfg.params or {}).get("pairs") or []
    if not isinstance(pairs, (list, tuple)):
        return []
    return [str(pair) for pair in pairs if pair]


def _classify_alt_key(
    config: AppConfig,
    group: MLArtifactGroup,
    strat_cfg: StrategyConfig,
) -> Optional[str]:
    parts = group.model_key.split("|")
    if len(parts) != 5:
        return "model_key_format_mismatch"

    pair, timeframe, feature_key, label_suffix, model_suffix = parts
    if timeframe not in set(_strategy_timeframes(strat_cfg)):
        return "timeframe_mismatch"
    if feature_key != _feature_key():
        return "feature_schema_mismatch"
    if label_suffix != _label_suffix(config, strat_cfg):
        return "label_config_mismatch"
    if model_suffix != classifier_model_config_key():
        return "model_config_mismatch"

    configured_pairs = _configured_pairs(strat_cfg)
    if configured_pairs and pair not in configured_pairs:
        return "pair_not_configured"
    return None


def _stale_reason(
    config: AppConfig,
    group: MLArtifactGroup,
) -> Optional[str]:
    strat_cfg = config.strategies.configs.get(group.strategy_id)
    if strat_cfg is None:
        return "strategy_missing"
    if strat_cfg.type not in ML_PRUNABLE_STRATEGY_TYPES:
        return "strategy_not_ml"

    if strat_cfg.type == "machine_learning":
        return _classify_global_key(
            group,
            expected_keys=_classifier_keys(config, strat_cfg),
            expected_timeframes=set(_strategy_timeframes(strat_cfg)),
            expected_label_suffix=_label_suffix(config, strat_cfg),
            expected_model_suffix=classifier_model_config_key(),
        )
    if strat_cfg.type == "machine_learning_regression":
        return _classify_global_key(
            group,
            expected_keys=_regression_keys(strat_cfg),
            expected_timeframes=set(_strategy_timeframes(strat_cfg)),
            expected_model_suffix=_regression_model_suffix(strat_cfg),
        )
    return _classify_alt_key(config, group, strat_cfg)


def find_stale_ml_artifact_groups(
    config: AppConfig,
    groups: Iterable[MLArtifactGroup],
    *,
    strategy_id: Optional[str] = None,
    older_than_days: Optional[int] = None,
    now: Optional[datetime] = None,
) -> list[MLArtifactPruneCandidate]:
    """Return stale ML artifact groups eligible for manual pruning."""

    cutoff: Optional[datetime] = None
    if older_than_days is not None:
        reference_now = now or datetime.now(UTC)
        if reference_now.tzinfo is None:
            reference_now = reference_now.replace(tzinfo=UTC)
        cutoff = reference_now.astimezone(UTC) - timedelta(days=older_than_days)

    candidates: list[MLArtifactPruneCandidate] = []
    for group in groups:
        if strategy_id is not None and group.strategy_id != strategy_id:
            continue

        reason = _stale_reason(config, group)
        if reason is None:
            continue

        if cutoff is not None:
            last_updated_at = group.last_updated_at
            if last_updated_at is None or last_updated_at >= cutoff:
                continue

        candidates.append(MLArtifactPruneCandidate(group=group, stale_reason=reason))
    return candidates


__all__ = [
    "MLArtifactPruneCandidate",
    "find_stale_ml_artifact_groups",
]
