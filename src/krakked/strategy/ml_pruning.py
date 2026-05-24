"""Helpers for identifying stale persisted ML artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Iterable, Optional

from krakked.config import AppConfig, StrategyConfig
from krakked.portfolio.store import MLArtifactGroup
from krakked.strategy.features import ML_FEATURE_SCHEMA_VERSION
from krakked.strategy.ml_labels import label_config_from_context

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


def _strategy_timeframe(strat_cfg: StrategyConfig) -> str:
    return str((strat_cfg.params or {}).get("timeframe") or "1h")


def _feature_key() -> str:
    return f"features_{ML_FEATURE_SCHEMA_VERSION}"


def _label_suffix(config: AppConfig, strat_cfg: StrategyConfig) -> str:
    return label_config_from_context(
        strat_cfg.params or {},
        _label_context(config),
    ).model_key_suffix()


def _classifier_key(config: AppConfig, strat_cfg: StrategyConfig) -> str:
    return (
        f"global|{_strategy_timeframe(strat_cfg)}|{_feature_key()}|"
        f"{_label_suffix(config, strat_cfg)}"
    )


def _regression_key(strat_cfg: StrategyConfig) -> str:
    return f"global|{_strategy_timeframe(strat_cfg)}|{_feature_key()}"


def _classify_global_key(
    group: MLArtifactGroup,
    *,
    expected_key: str,
    expected_timeframe: str,
    expected_label_suffix: Optional[str] = None,
) -> Optional[str]:
    if group.model_key == expected_key:
        return None

    parts = group.model_key.split("|")
    expected_parts = 4 if expected_label_suffix is not None else 3
    if len(parts) != expected_parts:
        return "model_key_format_mismatch"
    if parts[0] != "global":
        return "model_scope_mismatch"
    if parts[1] != expected_timeframe:
        return "timeframe_mismatch"
    if parts[2] != _feature_key():
        return "feature_schema_mismatch"
    if expected_label_suffix is not None and parts[3] != expected_label_suffix:
        return "label_config_mismatch"
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
    if len(parts) != 4:
        return "model_key_format_mismatch"

    pair, timeframe, feature_key, label_suffix = parts
    if timeframe != _strategy_timeframe(strat_cfg):
        return "timeframe_mismatch"
    if feature_key != _feature_key():
        return "feature_schema_mismatch"
    if label_suffix != _label_suffix(config, strat_cfg):
        return "label_config_mismatch"

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
            expected_key=_classifier_key(config, strat_cfg),
            expected_timeframe=_strategy_timeframe(strat_cfg),
            expected_label_suffix=_label_suffix(config, strat_cfg),
        )
    if strat_cfg.type == "machine_learning_regression":
        return _classify_global_key(
            group,
            expected_key=_regression_key(strat_cfg),
            expected_timeframe=_strategy_timeframe(strat_cfg),
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
