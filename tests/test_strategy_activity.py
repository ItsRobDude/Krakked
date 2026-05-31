from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from krakked.backtest import strategy_activity
from krakked.config import load_config


class _FakeSummary:
    def __init__(self, **values: Any) -> None:
        self._values = values

    def to_dict(self) -> dict[str, Any]:
        return dict(self._values)


def _summary_payload(**overrides: Any) -> dict[str, Any]:
    payload = {
        "trust_level": "decision_helpful",
        "trust_note": "Decision-helpful",
        "total_cycles": 1,
        "total_actions": 1,
        "blocked_actions": 0,
        "clamped_actions": 0,
        "total_orders": 1,
        "filled_orders": 1,
        "execution_errors": 0,
        "return_pct": 0.0,
        "max_drawdown_pct": 0.0,
        "missing_series": [],
        "partial_series": [],
        "blocked_reason_counts": {},
        "clamped_reason_counts": {},
        "per_strategy": {
            "trend_core": {
                "contexts_evaluated": 1,
                "intents_emitted": 1,
                "actions_after_scoring": 1,
                "filtered_by_score": 0,
            }
        },
    }
    payload.update(overrides)
    return payload


def _fake_result(summary: dict[str, Any] | None = None) -> Any:
    return SimpleNamespace(summary=_FakeSummary(**(summary or _summary_payload())))


def test_build_strategy_activity_groups_includes_configured_and_starters() -> None:
    config = load_config(config_path=Path("config_examples/config.yaml"), env="paper")
    config.strategies.enabled = ["trend_core", "majors_mean_rev"]

    groups = strategy_activity.build_strategy_activity_groups(config)

    by_id = {group.group_id: group.strategies for group in groups}
    assert by_id["configured"] == ("trend_core", "majors_mean_rev")
    assert by_id["starter_all"] == (
        "trend_core",
        "vol_breakout",
        "majors_mean_rev",
    )
    assert by_id["vol_breakout"] == ("vol_breakout",)


def test_strategy_activity_sweep_summarizes_gate2_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_config(config_path=Path("config_examples/config.yaml"), env="paper")
    captured_enabled: list[list[str]] = []

    def _fake_run_backtest(config_arg: Any, **_kwargs: Any) -> Any:
        captured_enabled.append(list(config_arg.strategies.enabled))
        return _fake_result()

    monkeypatch.setattr(strategy_activity, "run_backtest", _fake_run_backtest)
    groups = [
        strategy_activity.StrategyActivityGroup(
            group_id="trend_core",
            strategies=("trend_core",),
        )
    ]

    result = strategy_activity.run_strategy_activity_sweep(
        config,
        window_sets={"tiny": [("w1", "2026-05-01T00:00:00Z", "2026-05-02T00:00:00Z")]},
        groups=groups,
        strict_data=True,
    )

    assert captured_enabled == [["trend_core"]]
    assert result.summary["ready_for_gate2"] is True
    assert result.summary["best_gate2_candidate_group"] == "trend_core"
    assert result.runs[0]["stage"] == "filled"


def test_strategy_activity_sweep_records_strict_data_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_config(config_path=Path("config_examples/config.yaml"), env="paper")

    def _raise(*_args: Any, **_kwargs: Any) -> Any:
        raise ValueError("Historical data coverage failed in strict mode: missing")

    monkeypatch.setattr(strategy_activity, "run_backtest", _raise)
    groups = [
        strategy_activity.StrategyActivityGroup(
            group_id="vol_breakout",
            strategies=("vol_breakout",),
        )
    ]

    result = strategy_activity.run_strategy_activity_sweep(
        config,
        window_sets={"tiny": [("w1", "2026-05-01T00:00:00Z", "2026-05-02T00:00:00Z")]},
        groups=groups,
        strict_data=True,
    )

    assert result.summary["ready_for_gate2"] is False
    assert result.runs[0]["stage"] == "data_not_ready"
    assert "strict mode" in result.runs[0]["error"]


def test_activity_stage_identifies_score_filtered_intents() -> None:
    stage = strategy_activity._activity_stage(  # noqa: SLF001
        {
            "missing_series": [],
            "partial_series": [],
            "execution_errors": 0,
            "total_actions": 0,
            "blocked_actions": 0,
            "total_orders": 0,
            "filled_orders": 0,
        },
        {
            "trend_core": {
                "contexts_evaluated": 10,
                "intents_emitted": 3,
                "actions_after_scoring": 0,
            }
        },
    )

    assert stage == "score_filtered"
