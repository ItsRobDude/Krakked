from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

from krakked.backtest import runner
from krakked.config import AppConfig, load_config
from krakked.execution.models import ExecutionResult, LocalOrder
from krakked.market_data.metadata_store import PairMetadataStore
from krakked.market_data.models import PairMetadata
from krakked.strategy.models import ExecutionPlan, RiskAdjustedAction


def _build_backtest_config(tmp_path: Path) -> AppConfig:
    config = load_config(config_path=Path("config_examples/config.yaml"), env="paper")
    config.market_data.ohlc_store = {"root_dir": str(tmp_path / "ohlc")}
    config.market_data.metadata_path = str(tmp_path / "pair_metadata.json")
    config.universe.include_pairs = ["BTC/USD"]
    config.market_data.backfill_timeframes = ["1h"]
    config.risk.market_regime_throttle.enabled = False
    config.strategies.enabled = ["majors_mean_rev"]
    config.strategies.configs["majors_mean_rev"].params = {
        "pairs": ["BTC/USD"],
        "timeframe": "1h",
        "lookback_bars": 20,
        "band_width_bps": 150.0,
        "max_positions": 1,
    }
    return config


def test_backtest_reason_counts_separate_blocked_and_clamped_actions() -> None:
    generated_at = datetime(2026, 5, 1, tzinfo=UTC)
    plan = ExecutionPlan(
        plan_id="plan-1",
        generated_at=generated_at,
        actions=[
            RiskAdjustedAction(
                pair="BTC/USD",
                strategy_id="rs_rotation",
                action_type="open",
                target_base_size=0.01,
                target_notional_usd=500.0,
                current_base_size=0.0,
                reason="Clamped: Max per asset limit (1000.00 > 500.00)",
                blocked=False,
                blocked_reasons=["Max per asset limit (1000.00 > 500.00)"],
                clamped=True,
            ),
            RiskAdjustedAction(
                pair="ETH/USD",
                strategy_id="rs_rotation",
                action_type="none",
                target_base_size=0.0,
                target_notional_usd=0.0,
                current_base_size=0.0,
                reason=(
                    "Blocked: Strategy rs_rotation budget exceeded "
                    "(1500.00 > 500.00)"
                ),
                blocked=True,
                blocked_reasons=[
                    "Strategy rs_rotation budget exceeded (1500.00 > 500.00)"
                ],
                clamped=False,
            ),
        ],
    )

    assert runner._build_blocked_reason_counts([plan]) == {
        "Strategy rs_rotation budget exceeded (1500.00 > 500.00)": 1
    }
    assert runner._build_clamped_reason_counts([plan]) == {
        "Max per asset limit (1000.00 > 500.00)": 1
    }


def test_default_backtest_inputs_include_enabled_market_regime_throttle(
    tmp_path: Path,
) -> None:
    config = _build_backtest_config(tmp_path)
    config.risk.market_regime_throttle.enabled = True
    config.risk.market_regime_throttle.timeframe = "4h"
    config.risk.market_regime_throttle.benchmark_pair = "ETH/USD"
    config.risk.market_regime_throttle.pairs = ["SOL/USD"]

    assert runner._default_backtest_timeframes(config) == ["1h", "4h"]
    assert runner._configured_backtest_pairs(config) == [
        "BTC/USD",
        "ETH/USD",
        "SOL/USD",
    ]


def _seed_pair_metadata(config: AppConfig) -> None:
    metadata_path = config.market_data.metadata_path
    assert metadata_path is not None
    PairMetadataStore(Path(metadata_path)).save(
        [
            PairMetadata(
                canonical="XBTUSD",
                base="XXBT",
                quote="USD",
                rest_symbol="XBT/USD",
                ws_symbol="BTC/USD",
                raw_name="XBTUSD",
                price_decimals=2,
                volume_decimals=8,
                lot_size=1.0,
                min_order_size=0.0001,
                status="online",
                liquidity_24h_usd=1_000_000.0,
            ),
            PairMetadata(
                canonical="ETHUSD",
                base="XETH",
                quote="USD",
                rest_symbol="ETH/USD",
                ws_symbol="ETH/USD",
                raw_name="ETHUSD",
                price_decimals=2,
                volume_decimals=8,
                lot_size=1.0,
                min_order_size=0.0001,
                status="online",
                liquidity_24h_usd=1_000_000.0,
            ),
        ]
    )


def _write_ohlc_series(
    tmp_path: Path,
    *,
    timestamps: list[int],
    closes: list[float],
    timeframe: str = "1h",
    canonical: str = "XBTUSD",
) -> None:
    bars_path = tmp_path / "ohlc" / timeframe
    bars_path.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(
        [
            {
                "timestamp": ts,
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": 1000.0,
            }
            for ts, close in zip(timestamps, closes)
        ]
    ).set_index("timestamp")
    frame.to_parquet(bars_path / f"{canonical}.parquet")


def _mean_reverting_breakdown_closes() -> list[float]:
    return [101.0 if idx % 2 == 0 else 99.0 for idx in range(39)] + [85.0]


def test_run_backtest_wires_risk_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class _DummyMarketData:
        def __init__(
            self,
            config: AppConfig,
            pairs: list[str],
            frames: list[str],
            start: datetime,
            end: datetime,
        ) -> None:  # noqa: ARG002
            self._timeline = [int(start.timestamp())]

        def iter_timestamps(self) -> list[int]:
            return self._timeline

        def set_time(self, now: datetime) -> None:  # noqa: ARG002
            return None

    class _DummyPortfolioService:
        def __init__(
            self, config: AppConfig, *args: Any, **kwargs: Any
        ) -> None:  # noqa: ARG002
            self.store = SimpleNamespace(close=lambda: None)
            self.app_config = config
            self.portfolio = SimpleNamespace(
                ingest_trades=lambda trades, persist=True: None
            )
            self.realized_pnl_history: list[Any] = []

        def initialize(self) -> None:
            return None

        def get_equity(self) -> Any:
            return SimpleNamespace(
                equity_base=10_000.0,
                realized_pnl_base_total=0.0,
                unrealized_pnl_base_total=0.0,
            )

        def get_snapshots(self) -> list[Any]:
            return []

        def get_realized_pnl_by_strategy(
            self, include_manual: bool | None = None
        ) -> dict[str, float]:  # noqa: ARG002
            return {}

    class _DummyStrategyEngine:
        def __init__(self, config: AppConfig, market_data: Any, portfolio: Any) -> None:
            self.config = config
            self.market_data = market_data
            self.portfolio = portfolio

        def initialize(self) -> None:
            return None

        def get_risk_status(self) -> Any:
            return SimpleNamespace(kill_switch_active=False)

        def run_cycle(
            self, now: datetime | None = None
        ) -> ExecutionPlan:  # noqa: ARG002
            return ExecutionPlan(
                plan_id="plan-1",
                generated_at=datetime.now(UTC),
                actions=[],
            )

    class _DummyExecutionService:
        def __init__(self, *args: Any, **kwargs: Any) -> None:  # noqa: ARG002
            captured["risk_status_provider"] = kwargs.get("risk_status_provider")

        def execute_plan(self, plan: ExecutionPlan) -> ExecutionResult:  # noqa: ARG002
            return ExecutionResult(
                plan_id=plan.plan_id, started_at=datetime.now(UTC), success=True
            )

    monkeypatch.setattr(runner, "BacktestMarketData", _DummyMarketData)
    monkeypatch.setattr(runner, "BacktestPortfolioService", _DummyPortfolioService)
    monkeypatch.setattr(runner, "StrategyEngine", _DummyStrategyEngine)
    monkeypatch.setattr(runner, "ExecutionService", _DummyExecutionService)

    config = load_config(config_path=Path("config_examples/config.yaml"), env="paper")
    config.universe.include_pairs = ["XBT/USD"]

    now = datetime.now(UTC)
    result = runner.run_backtest(config, start=now, end=now + timedelta(seconds=1))

    assert result.plans
    assert result.executions
    assert captured["risk_status_provider"] is not None


def test_run_backtest_replays_cached_ohlc_with_starting_cash(tmp_path: Path) -> None:
    config = _build_backtest_config(tmp_path)
    _seed_pair_metadata(config)
    timestamps = [1_700_000_000 + (idx * 3600) for idx in range(40)]
    closes = _mean_reverting_breakdown_closes()
    _write_ohlc_series(tmp_path, timestamps=timestamps, closes=closes)

    start = datetime.fromtimestamp(timestamps[0], tz=UTC)
    end = datetime.fromtimestamp(timestamps[-1], tz=UTC)

    result = runner.run_backtest(
        config,
        start=start,
        end=end,
        timeframes=["1h"],
        starting_cash_usd=10_000.0,
    )

    assert result.summary is not None
    assert result.summary.total_cycles == len(timestamps)
    assert result.summary.total_orders >= 1
    assert result.summary.filled_orders >= 1
    assert result.summary.pairs == ["BTC/USD"]
    assert result.summary.starting_cash_usd == pytest.approx(10_000.0)
    assert result.summary.ending_equity_usd > 0
    assert result.summary.missing_series == []
    assert result.summary.partial_series == []
    assert result.summary.fee_bps == pytest.approx(25.0)
    assert result.summary.slippage_bps == pytest.approx(
        config.execution.max_slippage_bps
    )
    assert result.summary.usable_series_count == 1
    assert result.summary.coverage[0].status == "ok"
    assert result.summary.trust_level == "decision_helpful"
    assert "filled trades" in result.summary.trust_note
    assert result.summary.notable_warnings == []
    assert "config_path" not in result.summary.replay_inputs
    strategy_summary = result.summary.per_strategy["majors_mean_rev"]
    assert strategy_summary["cycles_evaluated"] == len(timestamps)
    assert strategy_summary["contexts_evaluated"] == len(timestamps)
    assert strategy_summary["timeframes_evaluated"] == ["1h"]
    assert strategy_summary["intents_emitted"] >= 1
    assert strategy_summary["actions_after_scoring"] >= 1
    assert "filtered_by_score" in strategy_summary
    assert "filtered_no_position_exits" in strategy_summary
    assert "filtered_position_exits" in strategy_summary
    assert "filtered_low_score_entries" in strategy_summary
    assert strategy_summary["min_score"] is not None
    assert strategy_summary["max_score"] >= strategy_summary["min_score"]


def test_resolve_simulated_fill_price_applies_slippage_by_side() -> None:
    config = load_config(config_path=Path("config_examples/config.yaml"), env="paper")
    config.execution.max_slippage_bps = 50
    metadata = PairMetadata(
        canonical="XBTUSD",
        base="XXBT",
        quote="USD",
        rest_symbol="XBT/USD",
        ws_symbol="BTC/USD",
        raw_name="XBTUSD",
        price_decimals=2,
        volume_decimals=8,
        lot_size=1.0,
        min_order_size=0.0001,
        status="online",
    )
    buy_order = LocalOrder(
        local_id="buy-1",
        plan_id="plan-1",
        strategy_id="majors_mean_rev",
        pair="BTC/USD",
        side="buy",
        order_type="limit",
        requested_base_size=1.0,
        requested_price=100.0,
    )
    sell_order = LocalOrder(
        local_id="sell-1",
        plan_id="plan-1",
        strategy_id="majors_mean_rev",
        pair="BTC/USD",
        side="sell",
        order_type="limit",
        requested_base_size=1.0,
        requested_price=100.0,
    )

    assert runner._resolve_simulated_fill_price(  # noqa: SLF001
        buy_order, metadata, latest_price=100.0, config=config
    ) == pytest.approx(100.5)
    assert runner._resolve_simulated_fill_price(  # noqa: SLF001
        sell_order, metadata, latest_price=100.0, config=config
    ) == pytest.approx(99.5)


def test_trade_from_order_with_costs_injects_fee_and_tags() -> None:
    order = LocalOrder(
        local_id="order-1",
        plan_id="plan-1",
        strategy_id="majors_mean_rev",
        pair="BTC/USD",
        side="buy",
        order_type="limit",
        userref=42,
        requested_base_size=2.0,
        requested_price=100.0,
        cumulative_base_filled=2.0,
        avg_fill_price=100.0,
        status="filled",
    )

    trade = runner._trade_from_order_with_costs(order, fee_bps=25.0)  # noqa: SLF001

    assert trade is not None
    assert trade["fee"] == pytest.approx(0.5)
    assert trade["cost"] == pytest.approx(200.0)
    assert trade["strategy_tag"] == "majors_mean_rev"
    assert trade["userref"] == 42


def test_run_backtest_marks_partial_series_and_strict_data_fails(
    tmp_path: Path,
) -> None:
    config = _build_backtest_config(tmp_path)
    _seed_pair_metadata(config)
    timestamps = [1_700_000_000 + (idx * 3600) for idx in range(1, 9)]
    closes = [100.0] * len(timestamps)
    _write_ohlc_series(tmp_path, timestamps=timestamps, closes=closes)

    start = datetime.fromtimestamp(timestamps[0] - 3600, tz=UTC)
    end = datetime.fromtimestamp(timestamps[-1] + 3600, tz=UTC)

    result = runner.run_backtest(
        config,
        start=start,
        end=end,
        timeframes=["1h"],
        starting_cash_usd=10_000.0,
    )

    assert result.summary is not None
    assert result.summary.partial_series == ["BTC/USD@1h"]
    assert result.summary.trust_level == "weak_signal"
    assert "emitted no intents" in result.summary.trust_note
    strategy_summary = result.summary.per_strategy["majors_mean_rev"]
    assert strategy_summary["cycles_evaluated"] == len(timestamps)
    assert strategy_summary["contexts_evaluated"] == len(timestamps)
    assert strategy_summary["timeframes_evaluated"] == ["1h"]
    assert strategy_summary["intents_emitted"] == 0
    assert strategy_summary["actions_after_scoring"] == 0
    assert strategy_summary["filtered_by_score"] == 0
    assert strategy_summary["min_score"] is None
    assert strategy_summary["max_score"] is None
    assert result.preflight is not None
    assert result.preflight.partial_series == ["BTC/USD@1h"]
    expected_strategy_gaps = [
        {
            "strategy_id": "majors_mean_rev",
            "pair": "BTC/USD",
            "timeframe": "1h",
            "series_key": "BTC/USD@1h",
            "coverage_status": "partial_window",
        }
    ]
    assert result.preflight.strategy_coverage_gaps == expected_strategy_gaps
    assert result.summary.strategy_coverage_gaps == expected_strategy_gaps
    assert result.preflight.status == "limited"
    assert "partially cover" in result.preflight.warnings[0]

    with pytest.raises(ValueError, match="strict mode"):
        runner.run_backtest(
            config,
            start=start,
            end=end,
            timeframes=["1h"],
            starting_cash_usd=10_000.0,
            strict_data=True,
        )


def test_run_backtest_cost_model_reduces_end_equity_and_report_shape(
    tmp_path: Path,
) -> None:
    config = _build_backtest_config(tmp_path)
    config.execution.max_slippage_bps = 0
    _seed_pair_metadata(config)
    timestamps = [1_700_000_000 + (idx * 3600) for idx in range(40)]
    closes = _mean_reverting_breakdown_closes()
    _write_ohlc_series(tmp_path, timestamps=timestamps, closes=closes)

    start = datetime.fromtimestamp(timestamps[0], tz=UTC)
    end = datetime.fromtimestamp(timestamps[-1], tz=UTC)

    zero_cost = runner.run_backtest(
        config,
        start=start,
        end=end,
        timeframes=["1h"],
        starting_cash_usd=10_000.0,
        fee_bps=0.0,
    )

    config.execution.max_slippage_bps = 50
    with_costs = runner.run_backtest(
        config,
        start=start,
        end=end,
        timeframes=["1h"],
        starting_cash_usd=10_000.0,
        fee_bps=25.0,
    )

    assert zero_cost.summary is not None
    assert with_costs.summary is not None
    assert zero_cost.summary.filled_orders >= 1
    assert with_costs.summary.filled_orders >= 1
    assert with_costs.summary.ending_equity_usd < zero_cost.summary.ending_equity_usd
    assert with_costs.summary.fee_bps == pytest.approx(25.0)
    assert with_costs.summary.slippage_bps == pytest.approx(50.0)

    report = with_costs.to_report_dict()

    assert report["report_version"] == 1
    assert "generated_at" in report
    assert report["summary"]["coverage"][0]["status"] == "ok"
    assert "per_strategy" in report["summary"]
    assert report["summary"]["per_strategy"]["majors_mean_rev"][
        "cycles_evaluated"
    ] == len(timestamps)
    assert "filtered_by_score" in report["summary"]["per_strategy"]["majors_mean_rev"]
    assert "strategy_coverage_gaps" in report["summary"]
    assert "strategy_coverage_gaps" in report["preflight"]
    assert report["summary"]["replay_inputs"]["fee_bps"] == pytest.approx(25.0)
    assert "enabled_strategies" in report["summary"]["replay_inputs"]
    assert "strategy_inputs" in report["summary"]["replay_inputs"]
    assert json.loads(json.dumps(report))["summary"]["usable_series_count"] == 1


def test_build_backtest_preflight_reports_readiness(tmp_path: Path) -> None:
    config = _build_backtest_config(tmp_path)
    _seed_pair_metadata(config)
    timestamps = [1_700_000_000 + (idx * 3600) for idx in range(6)]
    closes = [100.0] * len(timestamps)
    _write_ohlc_series(tmp_path, timestamps=timestamps, closes=closes)

    start = datetime.fromtimestamp(timestamps[0], tz=UTC)
    end = datetime.fromtimestamp(timestamps[-1], tz=UTC)

    result = runner.build_backtest_preflight(
        config,
        start=start,
        end=end,
        timeframes=["1h"],
    )

    assert result.preflight.status == "ready"
    assert "complete" in result.preflight.summary_note
    assert result.pairs == ["BTC/USD"]


def test_backtest_reports_resolved_strategy_inputs_for_mixed_params(
    tmp_path: Path,
) -> None:
    config = _build_backtest_config(tmp_path)
    config.universe.include_pairs = ["BTC/USD", "ETH/USD"]
    config.strategies.enabled = ["majors_mean_rev", "rs_rotation"]
    config.strategies.configs["majors_mean_rev"].enabled = True
    config.strategies.configs["majors_mean_rev"].params = {
        "pairs": ["BTC/USD"],
        "timeframe": "1h",
        "lookback_bars": 20,
        "band_width_bps": 150.0,
    }
    config.strategies.configs["rs_rotation"].enabled = True
    config.strategies.configs["rs_rotation"].params = {}
    _seed_pair_metadata(config)
    timestamps = [1_700_000_000 + (idx * 3600) for idx in range(6)]
    closes = [100.0] * len(timestamps)
    _write_ohlc_series(tmp_path, timestamps=timestamps, closes=closes)
    _write_ohlc_series(
        tmp_path, timestamps=timestamps, closes=closes, canonical="ETHUSD"
    )

    start = datetime.fromtimestamp(timestamps[0], tz=UTC)
    end = datetime.fromtimestamp(timestamps[-1], tz=UTC)

    preflight = runner.build_backtest_preflight(
        config,
        start=start,
        end=end,
        config_source="provided_config",
    )
    preflight_payload = preflight.to_dict()
    strategy_inputs = preflight_payload["strategy_inputs"]
    constructor_default_warning = (
        "Enabled strategies using constructor defaults instead of explicit config "
        "params: rs_rotation. Review strategy_inputs before treating replay inputs "
        "as configured operator intent."
    )

    assert strategy_inputs["config_source"] == "provided_config"
    assert strategy_inputs["resolved_config_path"] is None
    assert strategy_inputs["config_arg_supplied"] is False
    assert strategy_inputs["enabled_strategies"] == [
        "majors_mean_rev",
        "rs_rotation",
    ]
    assert (
        strategy_inputs["strategies"]["majors_mean_rev"]["params_source"]
        == "config_params"
    )
    assert (
        strategy_inputs["strategies"]["rs_rotation"]["params_source"]
        == "strategy_constructor_defaults"
    )
    assert strategy_inputs["strategies"]["rs_rotation"]["params"] == {}
    assert strategy_inputs["strategies"]["rs_rotation"]["configured_pairs"] == []
    assert strategy_inputs["strategies"]["rs_rotation"]["configured_timeframes"] == []
    assert strategy_inputs["strategies"]["rs_rotation"]["constructor_pairs"] == [
        "BTC/USD",
        "ETH/USD",
    ]
    assert strategy_inputs["strategies"]["rs_rotation"]["constructor_timeframes"] == [
        "4h"
    ]
    assert strategy_inputs["strategies"]["rs_rotation"]["evaluation_timeframes"] == [
        "1h"
    ]
    assert (
        strategy_inputs["strategies"]["rs_rotation"]["pair_normalization_applied"]
        is True
    )
    assert strategy_inputs["strategies"]["rs_rotation"]["resolved_pairs"] == [
        "BTC/USD",
        "ETH/USD",
    ]
    assert strategy_inputs["strategies"]["rs_rotation"]["resolved_timeframes"] == ["1h"]
    assert strategy_inputs["strategies"]["rs_rotation"]["requested_ohlc_series"] == [
        {"pair": "BTC/USD", "timeframe": "1h"},
        {"pair": "ETH/USD", "timeframe": "1h"},
    ]
    assert preflight.preflight.status == "ready"
    assert constructor_default_warning in preflight.preflight.warnings

    result = runner.run_backtest(
        config,
        start=start,
        end=end,
        starting_cash_usd=10_000.0,
    )
    assert result.summary is not None
    report_strategy_inputs = result.to_report_dict()["summary"]["replay_inputs"][
        "strategy_inputs"
    ]
    assert report_strategy_inputs == strategy_inputs
    report = result.to_report_dict()
    assert constructor_default_warning in report["preflight"]["warnings"]
    assert constructor_default_warning in report["summary"]["notable_warnings"]
    assert report["summary"]["trust_level"] == "weak_signal"


def test_backtest_strategy_inputs_exposes_constructor_default_pair_gap(
    tmp_path: Path,
) -> None:
    config = _build_backtest_config(tmp_path)
    config.universe.include_pairs = ["BTC/USD", "ETH/USD"]
    config.strategies.enabled = ["rs_rotation"]
    config.strategies.configs["rs_rotation"].enabled = True
    config.strategies.configs["rs_rotation"].params = {}
    _seed_pair_metadata(config)

    start = datetime(2026, 5, 1, tzinfo=UTC)
    end = datetime(2026, 5, 2, tzinfo=UTC)

    result = runner.build_backtest_preflight(config, start=start, end=end)
    strategy_input = result.to_dict()["strategy_inputs"]["strategies"]["rs_rotation"]

    assert strategy_input["params_source"] == "strategy_constructor_defaults"
    assert strategy_input["configured_pairs"] == []
    assert strategy_input["constructor_pairs"] == ["BTC/USD", "ETH/USD"]
    assert strategy_input["resolved_pairs"] == ["BTC/USD", "ETH/USD"]
    assert strategy_input["configured_timeframes"] == []
    assert strategy_input["constructor_timeframes"] == ["4h"]
    assert strategy_input["evaluation_timeframes"] == ["1h"]
    assert strategy_input["resolved_timeframes"] == ["1h"]
    assert strategy_input["requested_ohlc_series"] == [
        {"pair": "BTC/USD", "timeframe": "1h"},
        {"pair": "ETH/USD", "timeframe": "1h"},
    ]


def test_backtest_warns_when_strategy_allocation_exceeds_risk_envelope(
    tmp_path: Path,
) -> None:
    config = _build_backtest_config(tmp_path)
    config.universe.include_pairs = ["BTC/USD", "ETH/USD"]
    config.strategies.enabled = ["rs_rotation"]
    config.strategies.configs["rs_rotation"].enabled = True
    config.strategies.configs["rs_rotation"].params = {
        "pairs": ["BTC/USD", "ETH/USD"],
        "timeframe": "1h",
        "lookback_bars": 2,
        "rebalance_interval_hours": 24,
        "top_n": 2,
        "total_allocation_pct": 20.0,
        "confidence_return_bps": 250.0,
    }
    config.risk.max_per_strategy_pct["rs_rotation"] = 5.0
    config.risk.max_portfolio_risk_pct = 10.0
    config.risk.max_per_asset_pct = 5.0
    _seed_pair_metadata(config)
    timestamps = [1_700_000_000 + (idx * 3600) for idx in range(6)]
    btc_closes = [100.0, 101.0, 102.0, 103.0, 104.0, 105.0]
    eth_closes = [100.0, 100.5, 101.0, 101.5, 102.0, 102.5]
    _write_ohlc_series(tmp_path, timestamps=timestamps, closes=btc_closes)
    _write_ohlc_series(
        tmp_path, timestamps=timestamps, closes=eth_closes, canonical="ETHUSD"
    )

    start = datetime.fromtimestamp(timestamps[0], tz=UTC)
    end = datetime.fromtimestamp(timestamps[-1], tz=UTC)
    expected_warning = (
        "Strategy rs_rotation requested allocation may be impossible under the "
        "active risk envelope: strategy cap 5% < requested total 20%; "
        "portfolio cap 10% < requested total 20%; per-asset cap 5% < "
        "requested per-asset 10%. Replay actions may be cap-constrained rather "
        "than expressing configured strategy intent."
    )

    preflight = runner.build_backtest_preflight(config, start=start, end=end)

    assert preflight.preflight.status == "ready"
    assert expected_warning in preflight.preflight.warnings

    result = runner.run_backtest(
        config,
        start=start,
        end=end,
        starting_cash_usd=10_000.0,
    )
    report = result.to_report_dict()

    assert expected_warning in report["preflight"]["warnings"]
    assert expected_warning in report["summary"]["notable_warnings"]


def test_backtest_does_not_warn_when_strategy_allocation_fits_risk_envelope(
    tmp_path: Path,
) -> None:
    config = _build_backtest_config(tmp_path)
    config.strategies.enabled = ["rs_rotation"]
    config.strategies.configs["rs_rotation"].enabled = True
    config.strategies.configs["rs_rotation"].params = {
        "pairs": ["BTC/USD"],
        "timeframe": "1h",
        "lookback_bars": 2,
        "rebalance_interval_hours": 24,
        "top_n": 1,
        "total_allocation_pct": 5.0,
        "confidence_return_bps": 250.0,
    }
    config.risk.max_per_strategy_pct["rs_rotation"] = 5.0
    config.risk.max_portfolio_risk_pct = 10.0
    config.risk.max_per_asset_pct = 5.0
    _seed_pair_metadata(config)
    timestamps = [1_700_000_000 + (idx * 3600) for idx in range(6)]
    closes = [100.0] * len(timestamps)
    _write_ohlc_series(tmp_path, timestamps=timestamps, closes=closes)

    start = datetime.fromtimestamp(timestamps[0], tz=UTC)
    end = datetime.fromtimestamp(timestamps[-1], tz=UTC)

    result = runner.build_backtest_preflight(config, start=start, end=end)

    assert not [
        warning
        for warning in result.preflight.warnings
        if "requested allocation may be impossible" in warning
    ]


def test_build_backtest_preflight_warns_on_strategy_timeframe_gap(
    tmp_path: Path,
) -> None:
    config = _build_backtest_config(tmp_path)
    config.strategies.enabled = ["vol_breakout"]
    config.strategies.configs["vol_breakout"].enabled = True
    config.strategies.configs["vol_breakout"].params = {
        "pairs": ["BTC/USD"],
        "timeframes": ["15m", "1h"],
        "lookback_bars": 20,
    }
    config.market_data.backfill_timeframes = ["1h"]
    _seed_pair_metadata(config)
    timestamps = [1_700_000_000 + (idx * 3600) for idx in range(6)]
    closes = [100.0] * len(timestamps)
    _write_ohlc_series(tmp_path, timestamps=timestamps, closes=closes)

    start = datetime.fromtimestamp(timestamps[0], tz=UTC)
    end = datetime.fromtimestamp(timestamps[-1], tz=UTC)

    result = runner.build_backtest_preflight(config, start=start, end=end)

    assert result.timeframes == ["15m", "1h"]
    assert result.preflight.status == "limited"
    assert result.preflight.missing_series == ["BTC/USD@15m"]
    assert result.preflight.strategy_coverage_gaps == [
        {
            "strategy_id": "vol_breakout",
            "pair": "BTC/USD",
            "timeframe": "15m",
            "series_key": "BTC/USD@15m",
            "coverage_status": "missing",
        }
    ]
    assert result.to_dict()["strategy_inputs"]["strategies"]["vol_breakout"][
        "requested_ohlc_series"
    ] == [
        {"pair": "BTC/USD", "timeframe": "15m"},
        {"pair": "BTC/USD", "timeframe": "1h"},
    ]
    assert (
        "Strategy vol_breakout requested incomplete OHLC series: BTC/USD@15m."
        in result.preflight.warnings
    )


def test_build_backtest_preflight_reports_strategy_series_not_checked_when_limited_by_cli(
    tmp_path: Path,
) -> None:
    config = _build_backtest_config(tmp_path)
    config.strategies.enabled = ["vol_breakout"]
    config.strategies.configs["vol_breakout"].enabled = True
    config.strategies.configs["vol_breakout"].params = {
        "pairs": ["BTC/USD"],
        "timeframes": ["15m", "1h"],
        "lookback_bars": 20,
    }
    _seed_pair_metadata(config)
    timestamps = [1_700_000_000 + (idx * 3600) for idx in range(6)]
    closes = [100.0] * len(timestamps)
    _write_ohlc_series(tmp_path, timestamps=timestamps, closes=closes)

    start = datetime.fromtimestamp(timestamps[0], tz=UTC)
    end = datetime.fromtimestamp(timestamps[-1], tz=UTC)

    result = runner.build_backtest_preflight(
        config,
        start=start,
        end=end,
        timeframes=["1h"],
    )

    assert result.preflight.status == "ready"
    assert result.preflight.missing_series == []
    assert result.preflight.strategy_coverage_gaps == [
        {
            "strategy_id": "vol_breakout",
            "pair": "BTC/USD",
            "timeframe": "15m",
            "series_key": "BTC/USD@15m",
            "coverage_status": "not_checked",
        }
    ]


def test_build_backtest_preflight_accepts_closed_daily_boundary(
    tmp_path: Path,
) -> None:
    config = _build_backtest_config(tmp_path)
    _seed_pair_metadata(config)
    start = datetime(2026, 4, 20, tzinfo=UTC)
    timestamps = [int((start + timedelta(days=idx)).timestamp()) for idx in range(19)]
    closes = [100.0] * len(timestamps)
    _write_ohlc_series(
        tmp_path,
        timestamps=timestamps,
        closes=closes,
        timeframe="1d",
    )

    end = datetime(2026, 5, 9, tzinfo=UTC)

    result = runner.build_backtest_preflight(
        config,
        start=start,
        end=end,
        timeframes=["1d"],
    )

    assert result.preflight.status == "ready"
    assert result.preflight.partial_series == []
    assert result.preflight.coverage[0].status == "ok"


def test_replay_diagnostics_reports_score_filtered_intents() -> None:
    preflight = runner.BacktestPreflight(
        coverage=[],
        usable_series_count=1,
        missing_series=[],
        partial_series=[],
        status="ready",
        summary_note="Coverage looks complete for the requested replay window.",
        warnings=[],
    )
    per_strategy = {
        "trend_core": {
            "contexts_evaluated": 10,
            "intents_emitted": 3,
            "actions_after_scoring": 0,
            "filtered_by_score": 3,
            "filtered_no_position_exits": 2,
            "filtered_low_score_entries": 1,
            "min_score": 0.0,
            "max_score": 0.02,
        }
    }

    trust_level, trust_note, warnings = (
        runner._build_replay_diagnostics(  # noqa: SLF001
            total_actions=0,
            blocked_actions=0,
            total_orders=0,
            filled_orders=0,
            execution_errors=0,
            preflight=preflight,
            per_strategy=per_strategy,
        )
    )

    assert trust_level == "weak_signal"
    assert trust_note == (
        "Weak signal: only no-position exits and low-score entries reached the score gate."
    )
    assert (
        "Strategy intents were emitted but all were filtered before risk checks "
        "(2 no-position exits, 1 low-score entry)." in warnings
    )


def test_build_trend_core_warmup_warnings_for_short_daily_window(
    tmp_path: Path,
) -> None:
    config = _build_backtest_config(tmp_path)
    config.universe.include_pairs = ["BTC/USD"]
    config.strategies.enabled = ["trend_core"]
    config.strategies.configs["trend_core"].enabled = True
    preflight = runner.BacktestPreflight(
        coverage=[
            runner.BacktestCoverageItem(
                pair="BTC/USD",
                timeframe="1h",
                bar_count=200,
                first_bar_at=datetime(2026, 4, 20, tzinfo=UTC),
                last_bar_at=datetime(2026, 5, 9, tzinfo=UTC),
                status="ok",
            ),
            runner.BacktestCoverageItem(
                pair="BTC/USD",
                timeframe="1d",
                bar_count=19,
                first_bar_at=datetime(2026, 4, 20, tzinfo=UTC),
                last_bar_at=datetime(2026, 5, 8, tzinfo=UTC),
                status="ok",
            ),
        ],
        usable_series_count=2,
        missing_series=[],
        partial_series=[],
        status="ready",
        summary_note="Coverage looks complete for the requested replay window.",
        warnings=[],
    )

    warnings = runner._build_trend_core_warmup_warnings(
        config, preflight
    )  # noqa: SLF001

    assert warnings == [
        "Strategy trend_core may be under-warmed on 1d: requires 20 closed bars, but BTC/USD only have 19 inside the requested window."
    ]
