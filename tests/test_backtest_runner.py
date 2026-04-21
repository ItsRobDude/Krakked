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
from krakked.strategy.models import ExecutionPlan


def _build_backtest_config(tmp_path: Path) -> AppConfig:
    config = load_config(config_path=Path("config_examples/config.yaml"), env="paper")
    config.market_data.ohlc_store = {"root_dir": str(tmp_path / "ohlc")}
    config.market_data.metadata_path = str(tmp_path / "pair_metadata.json")
    config.universe.include_pairs = ["BTC/USD"]
    config.market_data.backfill_timeframes = ["1h"]
    config.strategies.enabled = ["majors_mean_rev"]
    config.strategies.configs["majors_mean_rev"].params = {
        "pairs": ["BTC/USD"],
        "timeframe": "1h",
        "lookback_bars": 20,
        "band_width_bps": 150.0,
        "max_positions": 1,
    }
    return config


def _seed_pair_metadata(config: AppConfig) -> None:
    PairMetadataStore(Path(config.market_data.metadata_path)).save(
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
            )
        ]
    )


def _write_ohlc_series(
    tmp_path: Path,
    *,
    timestamps: list[int],
    closes: list[float],
) -> None:
    bars_path = tmp_path / "ohlc" / "1h"
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
    frame.to_parquet(bars_path / "XBTUSD.parquet")


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
        def __init__(self, config: AppConfig, *args: Any, **kwargs: Any) -> None:  # noqa: ARG002
            self.store = SimpleNamespace(close=lambda: None)
            self.app_config = config
            self.portfolio = SimpleNamespace(
                ingest_trades=lambda trades, persist=True: None
            )
            self.realized_pnl_history = []

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
    closes = [100.0] * 39 + [80.0]
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
    assert "no strategy actions" in result.summary.trust_note
    assert result.preflight is not None
    assert result.preflight.partial_series == ["BTC/USD@1h"]
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
    closes = [100.0] * 39 + [80.0]
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
    assert report["summary"]["replay_inputs"]["fee_bps"] == pytest.approx(25.0)
    assert "enabled_strategies" in report["summary"]["replay_inputs"]
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
