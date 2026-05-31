from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from krakked.backtest import market_regime_throttle
from krakked.config import load_config
from krakked.strategy.models import ExecutionPlan


class _FakeSummary:
    def __init__(self, **values: Any) -> None:
        self._values = values

    def to_dict(self) -> dict[str, Any]:
        return dict(self._values)


def _summary_payload(**overrides: Any) -> dict[str, Any]:
    payload = {
        "ending_equity_usd": 10_000.0,
        "return_pct": 0.0,
        "max_drawdown_pct": 0.0,
        "total_actions": 1,
        "blocked_actions": 0,
        "clamped_actions": 0,
        "total_orders": 1,
        "filled_orders": 1,
        "execution_errors": 0,
        "trust_level": "decision_helpful",
        "trust_note": "Decision-helpful",
        "missing_series": [],
        "partial_series": [],
    }
    payload.update(overrides)
    return payload


def _fake_result(
    *,
    summary: dict[str, Any] | None = None,
    plans: list[ExecutionPlan] | None = None,
) -> Any:
    summary_payload = summary or _summary_payload()
    return SimpleNamespace(
        summary=_FakeSummary(**summary_payload),
        plans=plans or [],
        to_report_dict=lambda: {"summary": summary_payload},
    )


def test_runtime_throttle_backtest_compares_disabled_and_enabled_configs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, Any]] = []
    config = load_config(config_path=Path("config_examples/config.yaml"), env="paper")
    config.universe.include_pairs = ["BTC/USD", "ETH/USD"]
    config.market_data.backfill_timeframes = ["1h"]
    config.risk.market_regime_throttle.enabled = False
    config.risk.market_regime_throttle.timeframe = "4h"
    config.risk.market_regime_throttle.benchmark_pair = "BTC/USD"
    config.risk.market_regime_throttle.momentum_lookback_bars = 63
    config.risk.market_regime_throttle.basket_momentum_lookback_bars = 63
    config.risk.market_regime_throttle.volatility_lookback_bars = 63
    config.risk.market_regime_throttle.drawdown_lookback_bars = 63
    config.risk.market_regime_throttle.neutral_allocation_multiplier = 0.75
    config.risk.market_regime_throttle.risk_off_allocation_multiplier = 0.25

    def _fake_run_backtest(config_arg: Any, **kwargs: Any) -> Any:
        throttle_config = config_arg.risk.market_regime_throttle
        captured.append(
            {
                "enabled": throttle_config.enabled,
                "timeframe": throttle_config.timeframe,
                "pairs": list(throttle_config.pairs),
                "neutral_multiplier": throttle_config.neutral_allocation_multiplier,
                "risk_off_multiplier": throttle_config.risk_off_allocation_multiplier,
                "unavailable_policy": throttle_config.unavailable_policy,
                "timeframes": list(kwargs["timeframes"]),
                "strict_data": kwargs["strict_data"],
            }
        )
        return _fake_result()

    monkeypatch.setattr(market_regime_throttle, "run_backtest", _fake_run_backtest)

    result = market_regime_throttle.run_market_regime_throttle_backtest(
        config,
        start=datetime(2026, 5, 1, tzinfo=UTC),
        end=datetime(2026, 5, 2, tzinfo=UTC),
        pairs=["ETH/USD"],
        timeframes=["1h"],
        strict_data=True,
    )

    assert [call["enabled"] for call in captured] == [False, True]
    assert all(call["timeframe"] == "4h" for call in captured)
    assert all(call["timeframes"] == ["1h", "4h"] for call in captured)
    assert all(call["pairs"] == ["BTC/USD", "ETH/USD"] for call in captured)
    assert all(call["neutral_multiplier"] == pytest.approx(0.75) for call in captured)
    assert all(call["risk_off_multiplier"] == pytest.approx(0.25) for call in captured)
    assert all(call["unavailable_policy"] == "block_new_risk" for call in captured)
    assert all(call["strict_data"] is True for call in captured)
    assert result.summary["research_only"] is True
    assert result.summary["promotion_checks"]["passed"] is True


def test_summarize_market_regime_throttle_plans_counts_runtime_metadata() -> None:
    plans = [
        ExecutionPlan(
            plan_id="plan-1",
            generated_at=datetime(2026, 5, 1, tzinfo=UTC),
            actions=[],
            metadata={
                "market_regime_throttle": {
                    "available": True,
                    "regime": "neutral",
                    "reason_codes": ["btc_momentum_soft"],
                    "throttled_actions": 1,
                    "blocked_actions": 0,
                    "clamped_actions": 1,
                }
            },
        ),
        ExecutionPlan(
            plan_id="plan-2",
            generated_at=datetime(2026, 5, 1, tzinfo=UTC),
            actions=[],
            metadata={
                "market_regime_throttle": {
                    "available": False,
                    "regime": "unavailable",
                    "reason_codes": ["market_regime_unavailable"],
                    "throttled_actions": 2,
                    "blocked_actions": 2,
                    "clamped_actions": 0,
                }
            },
        ),
        ExecutionPlan(
            plan_id="plan-3",
            generated_at=datetime(2026, 5, 1, tzinfo=UTC),
            actions=[],
            metadata={},
        ),
    ]

    summary = market_regime_throttle.summarize_market_regime_throttle_plans(plans)

    assert summary["cycles_with_metadata"] == 2
    assert summary["state_counts"] == {"neutral": 1, "unavailable": 1}
    assert summary["reason_counts"] == {
        "btc_momentum_soft": 1,
        "market_regime_unavailable": 1,
    }
    assert summary["unavailable_cycles"] == 1
    assert summary["throttled_actions"] == 3
    assert summary["blocked_actions"] == 2
    assert summary["clamped_actions"] == 1
    assert summary["intervention_cycles"] == 2


def test_runtime_throttle_promotion_checks_require_actual_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_config(config_path=Path("config_examples/config.yaml"), env="paper")

    monkeypatch.setattr(
        market_regime_throttle,
        "run_backtest",
        lambda *_args, **_kwargs: _fake_result(
            summary=_summary_payload(total_actions=0, total_orders=0, filled_orders=0)
        ),
    )

    result = market_regime_throttle.run_market_regime_throttle_backtest(
        config,
        start=datetime(2026, 5, 1, tzinfo=UTC),
        end=datetime(2026, 5, 2, tzinfo=UTC),
    )

    checks = result.summary["promotion_checks"]
    assert checks["passed"] is False
    assert checks["actual_strategy_actions"]["passed"] is False
    assert checks["actual_filled_orders"]["passed"] is False
