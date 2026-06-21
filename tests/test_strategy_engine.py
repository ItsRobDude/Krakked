# tests/test_strategy_engine.py

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from krakked.config import (
    AppConfig,
    MarketRegimeThrottleConfig,
    RiskConfig,
    StrategiesConfig,
    StrategyConfig,
)
from krakked.market_data.api import MarketDataAPI
from krakked.market_data.exceptions import DataStaleError
from krakked.market_data.models import OHLCBar
from krakked.portfolio.models import SpotPosition
from krakked.portfolio.sync_status import (
    LIVE_SYNC_COLD_START_REASON,
    LIVE_SYNC_DEGRADED_REASON,
    live_sync_stale_reason,
)
from krakked.strategy.base import Strategy
from krakked.strategy.engine import StrategyRiskEngine
from krakked.strategy.evaluation import StrategyEvaluationResult
from krakked.strategy.models import (
    DecisionRecord,
    RiskAdjustedAction,
    RiskStatus,
    StrategyIntent,
    StrategyState,
)
from krakked.strategy.regime import MarketRegime, RegimeSnapshot
from krakked.strategy.strategies.dca_rebalance import DcaRebalanceStrategy
from krakked.strategy.strategies.demo_strategy import TrendFollowingStrategy
from krakked.strategy.strategies.mean_reversion import MeanReversionStrategy
from tests.runtime_mocks import make_portfolio_service_mock


def _ohlc_from_closes(closes, *, start_ts: int = 1_700_000_000, step: int = 3600):
    return [
        OHLCBar(
            timestamp=start_ts + (index * step),
            open=float(close),
            high=float(close) + 1.0,
            low=max(float(close) - 1.0, 0.0),
            close=float(close),
            volume=1.0,
        )
        for index, close in enumerate(closes)
    ]


def _engine_for_strategy(
    *,
    strat_config: StrategyConfig,
    strategy: Strategy,
    market: MagicMock | None = None,
    portfolio: MagicMock | None = None,
) -> StrategyRiskEngine:
    strategies_cfg = StrategiesConfig(
        enabled=[strat_config.name],
        configs={strat_config.name: strat_config},
    )

    app_config = MagicMock(spec=AppConfig)
    app_config.strategies = strategies_cfg
    app_config.risk = RiskConfig()
    app_config.universe = SimpleNamespace(include_pairs=["XBTUSD"], exclude_pairs=[])

    market = market or MagicMock(spec=MarketDataAPI)
    market.get_data_status.return_value = MagicMock(
        rest_api_reachable=True,
        websocket_connected=True,
        stale_pairs=0,
    )
    market.get_universe.return_value = ["XBTUSD"]
    market.get_display_pair.side_effect = lambda pair: pair
    market.get_ohlc.return_value = _ohlc_from_closes([100.0])

    portfolio = portfolio or make_portfolio_service_mock()
    portfolio.get_realized_pnl_by_strategy.return_value = {}

    engine = StrategyRiskEngine(app_config, market, portfolio)
    engine._data_ready = MagicMock(return_value=True)  # type: ignore[method-assign]
    engine.risk_engine = MagicMock()
    engine.risk_engine.process_intents.return_value = []
    engine.risk_engine.get_status.return_value = RiskStatus(
        kill_switch_active=False,
        daily_drawdown_pct=0.0,
        drift_flag=False,
        total_exposure_pct=0.0,
        manual_exposure_pct=0.0,
        per_asset_exposure_pct={},
        per_strategy_exposure_pct={},
    )
    engine.risk_engine.build_risk_context.return_value = SimpleNamespace(
        per_strategy_exposure_pct={}
    )
    engine.strategies = {strat_config.name: strategy}
    engine.strategy_states = {
        strat_config.name: StrategyState(
            strategy_id=strat_config.name,
            enabled=True,
            last_intents_at=None,
            last_actions_at=None,
            current_positions=[],
            pnl_summary={},
        )
    }
    return engine


def test_engine_cycle():
    # Setup Config
    strat_config = StrategyConfig(
        name="trend_core",
        type="trend_following",
        enabled=True,
        params={"timeframes": ["1h"], "ma_fast": 10, "ma_slow": 20},
    )
    strategies_cfg = StrategiesConfig(
        enabled=["trend_core"],
        configs={"trend_core": strat_config},
    )

    app_config = MagicMock(spec=AppConfig)
    app_config.strategies = strategies_cfg
    app_config.risk = RiskConfig()
    app_config.universe = MagicMock()
    app_config.universe.include_pairs = ["XBTUSD"]

    # Mock Services
    market = MagicMock(spec=MarketDataAPI)
    portfolio = make_portfolio_service_mock()

    market.get_data_status.return_value = MagicMock(
        rest_api_reachable=True,
        websocket_connected=True,
        stale_pairs=0,
    )
    market.get_pair_metadata.return_value = MagicMock(liquidity_24h_usd=200000.0)

    portfolio.get_positions.return_value = []
    portfolio.get_asset_exposure.return_value = []

    # Mock OHLC for strategy
    from dataclasses import dataclass

    @dataclass
    class MockBar:
        close: float
        timestamp: int
        high: float = 0
        low: float = 0

    # Provide enough data for MA(20)
    # Slow MA = 20. Need > 20 bars.
    # To trigger LONG: Fast > Slow.
    # Increasing price pattern.
    prices = [100 + i for i in range(30)]
    market.get_ohlc.return_value = [
        MockBar(close=p, timestamp=1_700_000_000 + i * 3600, high=p + 1, low=p - 1)
        for i, p in enumerate(prices)
    ]
    market.get_latest_price.return_value = 130.0

    engine = StrategyRiskEngine(app_config, market, portfolio)
    engine.initialize()

    assert len(engine.strategies) == 1

    # Run Cycle
    now = datetime.now(timezone.utc)
    plan = engine.run_cycle(now)

    assert plan is not None
    assert len(plan.actions) == 1
    assert plan.actions[0].pair == "XBTUSD"
    # Logic: Prices increasing -> Fast > Slow -> Long -> Enter
    assert plan.actions[0].action_type in ["open", "increase"]

    # Verify persistence call
    assert portfolio.record_decision.called
    args = portfolio.record_decision.call_args[0][0]
    assert isinstance(args, DecisionRecord)
    assert args.pair == "XBTUSD"

    # Execution plan should be persisted for downstream services
    assert portfolio.record_execution_plan.called


def test_run_cycle_passes_market_regime_throttle_snapshot():
    app_config = MagicMock(spec=AppConfig)
    app_config.strategies = StrategiesConfig(enabled=[], configs={})
    app_config.risk = RiskConfig(
        market_regime_throttle=MarketRegimeThrottleConfig(enabled=True)
    )
    app_config.universe = SimpleNamespace(include_pairs=["BTC/USD", "ETH/USD"])

    market = MagicMock(spec=MarketDataAPI)
    market.get_data_status.return_value = MagicMock(
        rest_api_reachable=True,
        websocket_connected=True,
        stale_pairs=0,
    )
    market.get_universe.return_value = ["BTC/USD", "ETH/USD"]
    market.get_ohlc.return_value = [
        OHLCBar(
            timestamp=1_700_000_000 + (i * 14_400),
            open=100.0 + i,
            high=101.0 + i,
            low=99.0 + i,
            close=100.0 + i,
            volume=1.0,
        )
        for i in range(80)
    ]
    portfolio = make_portfolio_service_mock()

    engine = StrategyRiskEngine(app_config, market, portfolio)
    plan = engine.run_cycle(datetime.now(timezone.utc))

    payload = plan.metadata["market_regime_throttle"]
    assert payload["enabled"] is True
    assert payload["available"] is True
    assert payload["mode"] == "target_scale"
    assert payload["timeframe"] == "4h"


def test_engine_stale_data():
    app_config = MagicMock(spec=AppConfig)
    app_config.strategies = StrategiesConfig()
    app_config.risk = RiskConfig()

    market = MagicMock(spec=MarketDataAPI)
    portfolio = make_portfolio_service_mock()

    market.get_data_status.return_value = MagicMock(rest_api_reachable=False)

    engine = StrategyRiskEngine(app_config, market, portfolio)
    plan = engine.run_cycle()

    assert len(plan.actions) == 0
    assert "error" in plan.metadata


def test_invalid_strategy_config_does_not_block_engine_startup():
    bad_dca = StrategyConfig(
        name="dca_overlay",
        type="dca_rebalance",
        enabled=True,
        params={"dca_interval_minutes": 240, "dca_notional_usd": 100.0},
    )
    trend = StrategyConfig(
        name="trend_core",
        type="trend_following",
        enabled=True,
        params={"timeframes": ["1h"], "ma_fast": 10, "ma_slow": 20},
    )
    strategies_cfg = StrategiesConfig(
        enabled=["dca_overlay", "trend_core"],
        configs={"dca_overlay": bad_dca, "trend_core": trend},
    )

    app_config = MagicMock(spec=AppConfig)
    app_config.strategies = strategies_cfg
    app_config.risk = RiskConfig()
    app_config.universe = MagicMock()
    app_config.universe.include_pairs = ["XBTUSD"]

    market = MagicMock(spec=MarketDataAPI)
    portfolio = make_portfolio_service_mock()

    engine = StrategyRiskEngine(app_config, market, portfolio)

    engine.initialize()

    assert "dca_overlay" not in engine.strategies
    assert engine.strategy_states["dca_overlay"].enabled is False
    assert engine.strategy_states["trend_core"].enabled is True


def test_data_stale_error_skips_timeframe():
    class FakeStrategy(Strategy):
        def warmup(self, market_data, portfolio):
            return None

        def generate_intents(self, ctx):
            if ctx.timeframe == "1h":
                raise DataStaleError("XBTUSD", 120.0, 60.0)
            return [
                StrategyIntent(
                    strategy_id=self.id,
                    pair="XBTUSD",
                    side="long",
                    intent_type="enter",
                    desired_exposure_usd=1000.0,
                    confidence=0.8,
                    timeframe=ctx.timeframe,
                    generated_at=ctx.now,
                )
            ]

    strat_config = StrategyConfig(
        name="fake", type="fake", enabled=True, params={"timeframes": ["1h", "4h"]}
    )
    strategies_cfg = StrategiesConfig(enabled=["fake"], configs={"fake": strat_config})

    app_config = MagicMock(spec=AppConfig)
    app_config.strategies = strategies_cfg
    app_config.risk = RiskConfig()
    app_config.universe = MagicMock()
    app_config.universe.include_pairs = ["XBTUSD"]

    market = MagicMock(spec=MarketDataAPI)
    market.get_data_status.return_value = MagicMock(
        rest_api_reachable=True,
        websocket_connected=True,
        stale_pairs=0,
    )

    portfolio = make_portfolio_service_mock()

    engine = StrategyRiskEngine(app_config, market, portfolio)
    engine._data_ready = MagicMock(return_value=True)
    engine.risk_engine = MagicMock()
    engine.risk_engine.process_intents.return_value = []
    engine.risk_engine.get_status.return_value = RiskStatus(
        kill_switch_active=False,
        daily_drawdown_pct=0.0,
        drift_flag=False,
        total_exposure_pct=0.0,
        manual_exposure_pct=0.0,
        per_asset_exposure_pct={},
        per_strategy_exposure_pct={},
    )
    engine.risk_engine.build_risk_context.return_value = SimpleNamespace(
        per_strategy_exposure_pct={}
    )
    portfolio.get_realized_pnl_by_strategy.return_value = {}

    fake_strategy = FakeStrategy(strat_config)
    engine.strategies = {"fake": fake_strategy}
    engine.strategy_states = {
        "fake": StrategyState(
            strategy_id="fake",
            enabled=True,
            last_intents_at=None,
            last_actions_at=None,
            current_positions=[],
            pnl_summary={},
        )
    }

    now = datetime.now(timezone.utc)
    plan = engine.run_cycle(now)

    engine.risk_engine.process_intents.assert_called_once()
    processed_intents = engine.risk_engine.process_intents.call_args[0][0]
    assert len(processed_intents) == 1
    assert processed_intents[0].timeframe == "4h"

    assert plan.actions == []
    assert "risk_status" in plan.metadata
    state = engine.strategy_states["fake"]
    assert state.last_evaluated_at == now
    assert state.last_intents and state.last_intents[0]["timeframe"] == "4h"
    assert state.pnl_summary["exposure_pct"] == 0.0
    assert state.pnl_summary["realized_pnl_usd"] == 0.0


def test_strategy_timeframe_contexts_wait_for_fresh_bar():
    class TimestampStrategy(Strategy):
        def warmup(self, market_data, portfolio):
            return None

        def generate_intents(self, ctx):
            return [
                StrategyIntent(
                    strategy_id=self.id,
                    pair="XBTUSD",
                    side="long",
                    intent_type="enter",
                    desired_exposure_usd=1000.0,
                    confidence=0.8,
                    timeframe=ctx.timeframe,
                    generated_at=ctx.now,
                )
            ]

    strat_config = StrategyConfig(
        name="timestamp",
        type="timestamp",
        enabled=True,
        params={"timeframes": ["1h", "4h"]},
    )
    strategies_cfg = StrategiesConfig(
        enabled=["timestamp"], configs={"timestamp": strat_config}
    )

    app_config = MagicMock(spec=AppConfig)
    app_config.strategies = strategies_cfg
    app_config.risk = RiskConfig()
    app_config.universe = MagicMock()
    app_config.universe.include_pairs = ["XBTUSD"]

    market = MagicMock(spec=MarketDataAPI)
    market.get_data_status.return_value = MagicMock(
        rest_api_reachable=True,
        websocket_connected=True,
        stale_pairs=0,
    )

    def _bar(timestamp: int) -> OHLCBar:
        return OHLCBar(
            timestamp=timestamp,
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.0,
            volume=1.0,
        )

    def _get_ohlc(pair, timeframe, lookback):  # noqa: ARG001
        now_ts = int(engine_now.timestamp())
        if timeframe == "1h":
            return [_bar(now_ts)]
        if timeframe == "4h":
            return [_bar((now_ts // 14_400) * 14_400)]
        return []

    market.get_ohlc.side_effect = _get_ohlc

    portfolio = make_portfolio_service_mock()
    portfolio.get_realized_pnl_by_strategy.return_value = {}

    engine = StrategyRiskEngine(app_config, market, portfolio)
    engine._data_ready = MagicMock(return_value=True)
    engine.risk_engine = MagicMock()
    engine.risk_engine.process_intents.return_value = []
    engine.risk_engine.get_status.return_value = RiskStatus(
        kill_switch_active=False,
        daily_drawdown_pct=0.0,
        drift_flag=False,
        total_exposure_pct=0.0,
        manual_exposure_pct=0.0,
        per_asset_exposure_pct={},
        per_strategy_exposure_pct={},
    )
    engine.risk_engine.build_risk_context.return_value = SimpleNamespace(
        per_strategy_exposure_pct={}
    )
    engine.strategies = {"timestamp": TimestampStrategy(strat_config)}
    engine.strategy_states = {
        "timestamp": StrategyState(
            strategy_id="timestamp",
            enabled=True,
            last_intents_at=None,
            last_actions_at=None,
            current_positions=[],
            pnl_summary={},
        )
    }

    engine_now = datetime(2026, 5, 10, 0, 0, tzinfo=timezone.utc)
    first_plan = engine.run_cycle(engine_now)
    first_intents = engine.risk_engine.process_intents.call_args[0][0]
    assert [intent.timeframe for intent in first_intents] == ["1h", "4h"]
    assert (
        first_plan.metadata["strategy_evaluation"]["timestamp"][
            "deferred_no_new_bar_contexts"
        ]
        == 0
    )
    assert (
        first_plan.metadata["strategy_evaluation"]["timestamp"][
            "skipped_stale_timeframe_contexts"
        ]
        == 0
    )

    engine_now = datetime(2026, 5, 10, 1, 0, tzinfo=timezone.utc)
    second_plan = engine.run_cycle(engine_now)
    second_intents = engine.risk_engine.process_intents.call_args[0][0]
    assert [intent.timeframe for intent in second_intents] == ["1h"]
    assert (
        second_plan.metadata["strategy_evaluation"]["timestamp"][
            "deferred_no_new_bar_contexts"
        ]
        == 1
    )
    assert (
        second_plan.metadata["strategy_evaluation"]["timestamp"][
            "skipped_stale_timeframe_contexts"
        ]
        == 1
    )

    engine_now = datetime(2026, 5, 10, 4, 0, tzinfo=timezone.utc)
    third_plan = engine.run_cycle(engine_now)
    third_intents = engine.risk_engine.process_intents.call_args[0][0]
    assert [intent.timeframe for intent in third_intents] == ["1h", "4h"]
    assert (
        third_plan.metadata["strategy_evaluation"]["timestamp"][
            "deferred_no_new_bar_contexts"
        ]
        == 0
    )
    assert (
        third_plan.metadata["strategy_evaluation"]["timestamp"][
            "skipped_stale_timeframe_contexts"
        ]
        == 0
    )


def test_strategy_evaluation_heartbeat_updates_when_no_intents_generated():
    class QuietStrategy(Strategy):
        def warmup(self, market_data, portfolio):
            return None

        def generate_intents(self, ctx):
            return []

    strat_config = StrategyConfig(
        name="quiet", type="quiet", enabled=True, params={"timeframes": ["1h"]}
    )
    strategies_cfg = StrategiesConfig(
        enabled=["quiet"], configs={"quiet": strat_config}
    )

    app_config = MagicMock(spec=AppConfig)
    app_config.strategies = strategies_cfg
    app_config.risk = RiskConfig()
    app_config.universe = MagicMock()
    app_config.universe.include_pairs = ["XBTUSD"]

    market = MagicMock(spec=MarketDataAPI)
    market.get_data_status.return_value = MagicMock(
        rest_api_reachable=True,
        websocket_connected=True,
        stale_pairs=0,
    )
    portfolio = make_portfolio_service_mock()

    engine = StrategyRiskEngine(app_config, market, portfolio)
    engine._data_ready = MagicMock(return_value=True)
    engine.risk_engine = MagicMock()
    engine.risk_engine.process_intents.return_value = []
    engine.risk_engine.get_status.return_value = RiskStatus(
        kill_switch_active=False,
        daily_drawdown_pct=0.0,
        drift_flag=False,
        total_exposure_pct=0.0,
        manual_exposure_pct=0.0,
        per_asset_exposure_pct={},
        per_strategy_exposure_pct={},
    )
    engine.risk_engine.build_risk_context.return_value = SimpleNamespace(
        per_strategy_exposure_pct={}
    )
    portfolio.get_realized_pnl_by_strategy.return_value = {}

    engine.strategies = {"quiet": QuietStrategy(strat_config)}
    engine.strategy_states = {
        "quiet": StrategyState(
            strategy_id="quiet",
            enabled=True,
            last_intents_at=None,
            last_actions_at=None,
            current_positions=[],
            pnl_summary={},
        )
    }

    now = datetime.now(timezone.utc)
    plan = engine.run_cycle(now)

    assert plan.actions == []
    state = engine.strategy_states["quiet"]
    assert state.last_evaluated_at == now
    assert state.last_intents_at == now
    assert state.last_intents == []
    assert state.last_evaluation_summary is not None
    assert state.last_evaluation_summary["status"] == "no_signal"


def test_strategy_evaluation_classifies_no_closed_bars_as_no_data():
    class QuietStrategy(Strategy):
        def warmup(self, market_data, portfolio):
            return None

        def generate_intents(self, ctx):  # pragma: no cover - should not evaluate
            raise AssertionError("strategy should not evaluate without closed bars")

    strat_config = StrategyConfig(
        name="quiet", type="quiet", enabled=True, params={"timeframes": ["1h"]}
    )
    strategies_cfg = StrategiesConfig(
        enabled=["quiet"], configs={"quiet": strat_config}
    )

    app_config = MagicMock(spec=AppConfig)
    app_config.strategies = strategies_cfg
    app_config.risk = RiskConfig()
    app_config.universe = MagicMock()
    app_config.universe.include_pairs = ["XBTUSD"]

    market = MagicMock(spec=MarketDataAPI)
    market.get_data_status.return_value = MagicMock(
        rest_api_reachable=True,
        websocket_connected=True,
        stale_pairs=0,
    )
    market.get_universe.return_value = ["XBTUSD"]
    market.get_ohlc.return_value = []

    portfolio = make_portfolio_service_mock()
    portfolio.get_realized_pnl_by_strategy.return_value = {}

    engine = StrategyRiskEngine(app_config, market, portfolio)
    engine._data_ready = MagicMock(return_value=True)
    engine.risk_engine = MagicMock()
    engine.risk_engine.process_intents.return_value = []
    engine.risk_engine.get_status.return_value = RiskStatus(
        kill_switch_active=False,
        daily_drawdown_pct=0.0,
        drift_flag=False,
        total_exposure_pct=0.0,
        manual_exposure_pct=0.0,
        per_asset_exposure_pct={},
        per_strategy_exposure_pct={},
    )
    engine.risk_engine.build_risk_context.return_value = SimpleNamespace(
        per_strategy_exposure_pct={}
    )
    engine.strategies = {"quiet": QuietStrategy(strat_config)}
    engine.strategy_states = {
        "quiet": StrategyState(
            strategy_id="quiet",
            enabled=True,
            last_intents_at=None,
            last_actions_at=None,
            current_positions=[],
            pnl_summary={},
        )
    }

    now = datetime.now(timezone.utc)
    plan = engine.run_cycle(now)

    evaluation = plan.metadata["strategy_evaluation"]["quiet"]
    assert evaluation["no_data_contexts"] == 1
    assert evaluation["fresh_contexts_evaluated"] == 0
    assert evaluation["deferred_no_new_bar_contexts"] == 0
    assert evaluation["skipped_stale_timeframe_contexts"] == 0
    state = engine.strategy_states["quiet"]
    assert state.last_evaluated_at is None
    assert state.last_evaluation_summary is not None
    assert state.last_evaluation_summary["status"] == "no_data"


def test_dca_evaluates_with_empty_ohlc_store_when_price_is_fresh():
    strat_config = StrategyConfig(
        name="dca_overlay",
        type="dca_rebalance",
        enabled=True,
        params={
            "pairs": ["XBTUSD"],
            "target_weights": {"XBTUSD": 0.5, "ETHUSD": 0.5},
            "rebalance_threshold_pct": 1.0,
            "dca_interval_minutes": 60,
            "dca_notional_usd": 100.0,
        },
    )
    strategy = DcaRebalanceStrategy(strat_config)

    market = MagicMock(spec=MarketDataAPI)
    market.get_data_status.return_value = MagicMock(
        rest_api_reachable=True,
        websocket_connected=True,
        stale_pairs=0,
    )
    market.get_universe.return_value = ["XBTUSD"]
    market.get_ohlc.return_value = []
    market.get_latest_price.return_value = 100.0
    market.get_display_pair.side_effect = lambda pair: pair

    portfolio = make_portfolio_service_mock(equity_base=10000.0, cash_base=10000.0)
    portfolio.get_positions.return_value = []
    engine = _engine_for_strategy(
        strat_config=strat_config,
        strategy=strategy,
        market=market,
        portfolio=portfolio,
    )

    plan = engine.run_cycle(datetime.now(timezone.utc))

    submitted_intents = engine.risk_engine.process_intents.call_args.args[0]
    assert len(submitted_intents) == 1
    assert submitted_intents[0].strategy_id == "dca_overlay"
    evaluation = plan.metadata["strategy_evaluation"]["dca_overlay"]
    assert evaluation["no_data_contexts"] == 0
    assert evaluation["fresh_contexts_evaluated"] == 1
    assert evaluation["last_evaluation_summary"]["status"] == "intents_emitted"


def test_dca_interval_not_elapsed_reports_no_signal_reason():
    strat_config = StrategyConfig(
        name="dca_overlay",
        type="dca_rebalance",
        enabled=True,
        params={
            "pairs": ["XBTUSD"],
            "target_weights": {"XBTUSD": 1.0},
            "rebalance_threshold_pct": 1.0,
            "dca_interval_minutes": 60,
            "dca_notional_usd": 100.0,
        },
    )
    strategy = DcaRebalanceStrategy(strat_config)
    now = datetime.now(timezone.utc)
    strategy._last_dca = now
    ctx = SimpleNamespace(
        timeframe="1h",
        universe=["XBTUSD"],
        market_data=MagicMock(spec=MarketDataAPI),
        portfolio=make_portfolio_service_mock(),
        regime=None,
        now=now,
    )

    result = strategy.evaluate(ctx)

    assert result.intents == []
    assert result.no_signal_reasons[0]["reason"] == "rebalance_interval_not_elapsed"


def test_dca_within_drift_threshold_reports_no_signal_reason():
    strat_config = StrategyConfig(
        name="dca_overlay",
        type="dca_rebalance",
        enabled=True,
        params={
            "pairs": ["XBTUSD"],
            "target_weights": {"XBTUSD": 0.5, "ETHUSD": 0.5},
            "rebalance_threshold_pct": 1.0,
            "dca_interval_minutes": 60,
            "dca_notional_usd": 100.0,
        },
    )
    strategy = DcaRebalanceStrategy(strat_config)

    market = MagicMock(spec=MarketDataAPI)
    market.normalize_pair.side_effect = lambda pair: pair
    market.get_latest_price.return_value = 100.0
    portfolio = make_portfolio_service_mock(equity_base=10000.0, cash_base=5000.0)
    portfolio.get_positions.return_value = [
        SpotPosition(
            pair="XBTUSD",
            base_asset="XBT",
            quote_asset="USD",
            base_size=50.0,
            avg_entry_price=100.0,
            realized_pnl_base=0.0,
            fees_paid_base=0.0,
            strategy_tag="dca_overlay",
        )
    ]
    ctx = SimpleNamespace(
        timeframe="1h",
        universe=["XBTUSD"],
        market_data=market,
        portfolio=portfolio,
        regime=None,
        now=datetime.now(timezone.utc),
    )

    result = strategy.evaluate(ctx)

    assert result.intents == []
    assert result.no_signal_reasons[0]["reason"] == "within_rebalance_threshold"


def test_strategy_crash_produces_strategy_error_headline():
    class CrashingStrategy(Strategy):
        def warmup(self, market_data, portfolio):
            return None

        def generate_intents(self, ctx):
            raise RuntimeError("boom")

    strat_config = StrategyConfig(
        name="crasher", type="crasher", enabled=True, params={"timeframes": ["1h"]}
    )
    market = MagicMock(spec=MarketDataAPI)
    market.get_data_status.return_value = MagicMock(
        rest_api_reachable=True,
        websocket_connected=True,
        stale_pairs=0,
    )
    market.get_universe.return_value = ["XBTUSD"]
    market.get_ohlc.return_value = _ohlc_from_closes([100.0])
    market.get_display_pair.side_effect = lambda pair: pair
    engine = _engine_for_strategy(
        strat_config=strat_config,
        strategy=CrashingStrategy(strat_config),
        market=market,
    )

    plan = engine.run_cycle(datetime.now(timezone.utc))

    evaluation = plan.metadata["strategy_evaluation"]["crasher"]
    assert evaluation["strategy_error_contexts"] == 1
    assert evaluation["last_evaluation_summary"]["status"] == "strategy_error"
    assert (
        evaluation["last_evaluation_summary"]["message"] == "Strategy evaluation failed"
    )


def test_intent_summary_clears_top_level_reasons_but_keeps_context_detail():
    class MixedStrategy(Strategy):
        def warmup(self, market_data, portfolio):
            return None

        def generate_intents(self, ctx):
            return []

        def evaluate(self, ctx):
            if ctx.timeframe == "1h":
                return StrategyEvaluationResult(
                    intents=[
                        StrategyIntent(
                            strategy_id=self.id,
                            pair="XBTUSD",
                            side="long",
                            intent_type="enter",
                            desired_exposure_usd=1000.0,
                            confidence=1.0,
                            timeframe=ctx.timeframe,
                            generated_at=ctx.now,
                        )
                    ],
                    context_summaries=[
                        {
                            "status": "intents_emitted",
                            "reason": "entry_signal",
                            "timeframe": ctx.timeframe,
                        }
                    ],
                )
            return StrategyEvaluationResult(
                no_signal_reasons=[
                    {
                        "reason": "regime_not_mean_reverting",
                        "message": "Higher timeframe did not confirm",
                        "timeframe": ctx.timeframe,
                    }
                ],
                context_summaries=[
                    {
                        "status": "no_signal",
                        "reason": "regime_not_mean_reverting",
                        "message": "Higher timeframe did not confirm",
                        "timeframe": ctx.timeframe,
                    }
                ],
            )

    strat_config = StrategyConfig(
        name="mixed",
        type="mixed",
        enabled=True,
        params={"timeframes": ["4h", "1h"]},
    )
    market = MagicMock(spec=MarketDataAPI)
    market.get_data_status.return_value = MagicMock(
        rest_api_reachable=True,
        websocket_connected=True,
        stale_pairs=0,
    )
    market.get_universe.return_value = ["XBTUSD"]
    market.get_ohlc.return_value = _ohlc_from_closes([100.0])
    market.get_display_pair.side_effect = lambda pair: pair
    engine = _engine_for_strategy(
        strat_config=strat_config,
        strategy=MixedStrategy(strat_config),
        market=market,
    )

    plan = engine.run_cycle(datetime.now(timezone.utc))

    summary = plan.metadata["strategy_evaluation"]["mixed"]["last_evaluation_summary"]
    assert summary["status"] == "intents_emitted"
    assert summary["reasons"] == []
    assert any(
        context.get("reason") == "regime_not_mean_reverting"
        for context in summary["context_summaries"]
    )


def test_multitimeframe_stale_context_wins_headline_when_no_intents():
    class PartiallyStaleStrategy(Strategy):
        def warmup(self, market_data, portfolio):
            return None

        def generate_intents(self, ctx):
            if ctx.timeframe == "4h":
                raise DataStaleError("XBTUSD", 120.0, 60.0)
            return []

        def explain_no_signal(self, ctx):
            return [
                {
                    "reason": "test_no_signal",
                    "message": "Fresh context had no signal",
                    "timeframe": ctx.timeframe,
                }
            ]

    strat_config = StrategyConfig(
        name="partial_stale",
        type="partial_stale",
        enabled=True,
        params={"timeframes": ["1h", "4h"]},
    )
    engine = _engine_for_strategy(
        strat_config=strat_config,
        strategy=PartiallyStaleStrategy(strat_config),
    )

    plan = engine.run_cycle(datetime.now(timezone.utc))

    evaluation = plan.metadata["strategy_evaluation"]["partial_stale"]
    summary = evaluation["last_evaluation_summary"]
    assert evaluation["fresh_contexts_evaluated"] == 1
    assert evaluation["data_stale_contexts"] == 1
    assert summary["status"] == "data_stale"
    assert summary["message"] == "Market data was stale for this strategy context"


def test_disabling_strategy_replaces_stale_generated_intent_summary():
    strat_config = StrategyConfig(
        name="quiet", type="quiet", enabled=True, params={"timeframes": ["1h"]}
    )

    class QuietStrategy(Strategy):
        def warmup(self, market_data, portfolio):
            return None

        def generate_intents(self, ctx):
            return []

    engine = _engine_for_strategy(
        strat_config=strat_config,
        strategy=QuietStrategy(strat_config),
    )
    engine.strategy_states["quiet"].last_evaluation_summary = {
        "status": "intents_emitted",
        "message": "Generated 1 intent",
    }

    engine.set_strategy_enabled("quiet", False)

    assert engine.strategy_states["quiet"].enabled is False
    assert (
        engine.strategy_states["quiet"].last_evaluation_summary["status"] == "disabled"
    )
    assert engine.strategy_states["quiet"].last_evaluation_summary["message"] == (
        "Strategy is paused"
    )


def test_reenabling_strategy_replaces_disabled_summary_with_awaiting_evaluation():
    strat_config = StrategyConfig(
        name="quiet", type="quiet", enabled=True, params={"timeframes": ["1h"]}
    )

    class QuietStrategy(Strategy):
        def warmup(self, market_data, portfolio):
            return None

        def generate_intents(self, ctx):
            return []

    engine = _engine_for_strategy(
        strat_config=strat_config,
        strategy=QuietStrategy(strat_config),
    )
    engine.set_strategy_enabled("quiet", False)
    assert engine.strategy_states["quiet"].last_evaluation_summary["status"] == (
        "disabled"
    )

    def _activate_strategy(config):
        engine.strategies[config.name] = QuietStrategy(config)
        return True

    engine._activate_strategy = MagicMock(side_effect=_activate_strategy)

    engine.set_strategy_enabled("quiet", True)

    assert engine.strategy_states["quiet"].enabled is True
    assert engine.strategy_states["quiet"].last_evaluation_summary["status"] == (
        "awaiting_evaluation"
    )
    assert engine.strategy_states["quiet"].last_evaluation_summary["message"] == (
        "Awaiting first strategy evaluation"
    )


def test_strategy_evaluation_summary_records_no_signal_reason():
    class DiagnosticQuietStrategy(Strategy):
        def warmup(self, market_data, portfolio):
            return None

        def generate_intents(self, ctx):
            return []

        def explain_no_signal(self, ctx):
            return [
                {
                    "pair": "XBTUSD",
                    "timeframe": ctx.timeframe,
                    "reason": "test_reason",
                    "message": "Test reason for no action",
                }
            ]

    strat_config = StrategyConfig(
        name="quiet", type="quiet", enabled=True, params={"timeframes": ["1h"]}
    )
    strategies_cfg = StrategiesConfig(
        enabled=["quiet"], configs={"quiet": strat_config}
    )

    app_config = MagicMock(spec=AppConfig)
    app_config.strategies = strategies_cfg
    app_config.risk = RiskConfig()
    app_config.universe = MagicMock()
    app_config.universe.include_pairs = ["XBTUSD"]

    market = MagicMock(spec=MarketDataAPI)
    market.get_data_status.return_value = MagicMock(
        rest_api_reachable=True,
        websocket_connected=True,
        stale_pairs=0,
    )
    market.get_universe.return_value = ["XBTUSD"]
    market.get_ohlc.return_value = _ohlc_from_closes([100.0])

    portfolio = make_portfolio_service_mock()
    portfolio.get_realized_pnl_by_strategy.return_value = {}

    engine = StrategyRiskEngine(app_config, market, portfolio)
    engine._data_ready = MagicMock(return_value=True)
    engine.risk_engine = MagicMock()
    engine.risk_engine.process_intents.return_value = []
    engine.risk_engine.get_status.return_value = RiskStatus(
        kill_switch_active=False,
        daily_drawdown_pct=0.0,
        drift_flag=False,
        total_exposure_pct=0.0,
        manual_exposure_pct=0.0,
        per_asset_exposure_pct={},
        per_strategy_exposure_pct={},
    )
    engine.risk_engine.build_risk_context.return_value = SimpleNamespace(
        per_strategy_exposure_pct={}
    )
    engine.strategies = {"quiet": DiagnosticQuietStrategy(strat_config)}
    engine.strategy_states = {
        "quiet": StrategyState(
            strategy_id="quiet",
            enabled=True,
            last_intents_at=None,
            last_actions_at=None,
            current_positions=[],
            pnl_summary={},
        )
    }

    plan = engine.run_cycle(datetime.now(timezone.utc))

    evaluation = plan.metadata["strategy_evaluation"]["quiet"]
    assert evaluation["fresh_contexts_evaluated"] == 1
    assert evaluation["no_signal_reasons"][0]["reason"] == "test_reason"
    state = engine.strategy_states["quiet"]
    assert state.last_evaluation_summary is not None
    assert state.last_evaluation_summary["status"] == "no_signal"
    assert state.last_evaluation_summary["message"] == "Test reason for no action"


def test_trend_following_no_signal_explains_regime_not_uptrend():
    strat_config = StrategyConfig(
        name="trend_core",
        type="trend_following",
        enabled=True,
        params={
            "pairs": ["XBTUSD"],
            "timeframes": ["1h"],
            "ma_fast": 3,
            "ma_slow": 5,
            "regime_timeframe": "1d",
        },
    )
    strategy = TrendFollowingStrategy(strat_config)

    market = MagicMock(spec=MarketDataAPI)
    market.get_pair_metadata.return_value = MagicMock(liquidity_24h_usd=1_000_000.0)
    market.get_ohlc.side_effect = [
        _ohlc_from_closes([100, 101, 102, 103, 104, 105], step=3600),
        _ohlc_from_closes([105, 104, 103, 102, 101, 100], step=86_400),
    ]

    portfolio = make_portfolio_service_mock()
    portfolio.app_config = MagicMock()
    portfolio.app_config.risk = RiskConfig(min_liquidity_24h_usd=100000.0)
    portfolio.get_positions.return_value = []

    ctx = SimpleNamespace(
        timeframe="1h",
        universe=["XBTUSD"],
        market_data=market,
        portfolio=portfolio,
        regime=None,
        now=datetime.now(timezone.utc),
    )

    reasons = strategy.explain_no_signal(ctx)

    assert reasons[0]["reason"] == "daily_regime_not_uptrend"


def test_mean_reversion_no_signal_explains_regime_gate():
    strat_config = StrategyConfig(
        name="majors_mean_rev",
        type="mean_reversion",
        enabled=True,
        params={
            "pairs": ["ETHUSD"],
            "timeframe": "1h",
            "lookback_bars": 5,
            "band_width_bps": 150,
        },
    )
    strategy = MeanReversionStrategy(strat_config)

    market = MagicMock(spec=MarketDataAPI)
    market.get_ohlc.return_value = _ohlc_from_closes([100, 100, 100, 100, 90])
    portfolio = make_portfolio_service_mock()
    portfolio.get_positions.return_value = []
    ctx = SimpleNamespace(
        timeframe="1h",
        universe=["ETHUSD"],
        market_data=market,
        portfolio=portfolio,
        regime=RegimeSnapshot(
            per_pair={"ETHUSD": MarketRegime.TRENDING},
            as_of=datetime.now(timezone.utc).isoformat(),
        ),
        now=datetime.now(timezone.utc),
    )

    reasons = strategy.explain_no_signal(ctx)

    assert reasons[0]["reason"] == "regime_not_mean_reverting"


def test_strategy_evaluation_classifies_score_filtered_intents():
    class FilteredStrategy(Strategy):
        def warmup(self, market_data, portfolio):
            return None

        def generate_intents(self, ctx):
            return [
                StrategyIntent(
                    strategy_id=self.id,
                    pair="XBTUSD",
                    side="flat",
                    intent_type="exit",
                    desired_exposure_usd=0.0,
                    confidence=0.0,
                    timeframe=ctx.timeframe,
                    generated_at=ctx.now,
                ),
                StrategyIntent(
                    strategy_id=self.id,
                    pair="SOLUSD",
                    side="flat",
                    intent_type="exit",
                    desired_exposure_usd=0.0,
                    confidence=0.0,
                    timeframe=ctx.timeframe,
                    generated_at=ctx.now,
                ),
                StrategyIntent(
                    strategy_id=self.id,
                    pair="ETHUSD",
                    side="long",
                    intent_type="enter",
                    desired_exposure_usd=100.0,
                    confidence=0.01,
                    timeframe=ctx.timeframe,
                    generated_at=ctx.now,
                ),
            ]

    strat_config = StrategyConfig(
        name="fake", type="fake", enabled=True, params={"timeframes": ["1h"]}
    )
    strategies_cfg = StrategiesConfig(enabled=["fake"], configs={"fake": strat_config})

    app_config = MagicMock(spec=AppConfig)
    app_config.strategies = strategies_cfg
    app_config.risk = RiskConfig()
    app_config.universe = MagicMock()
    app_config.universe.include_pairs = ["XBTUSD", "ETHUSD", "SOLUSD"]

    market = MagicMock(spec=MarketDataAPI)
    market.get_data_status.return_value = MagicMock(
        rest_api_reachable=True,
        websocket_connected=True,
        stale_pairs=0,
    )
    market.get_universe.return_value = ["XBTUSD", "ETHUSD", "SOLUSD"]
    market.get_display_pair.side_effect = lambda pair: pair

    portfolio = make_portfolio_service_mock()
    portfolio.get_positions.return_value = [
        SpotPosition(
            pair="XBTUSD",
            base_asset="XBT",
            quote_asset="USD",
            base_size=1.0,
            avg_entry_price=100.0,
            realized_pnl_base=0.0,
            fees_paid_base=0.0,
            strategy_tag="fake",
        )
    ]
    portfolio.get_realized_pnl_by_strategy.return_value = {}

    engine = StrategyRiskEngine(app_config, market, portfolio)
    engine._data_ready = MagicMock(return_value=True)
    engine.risk_engine = MagicMock()
    engine.risk_engine.process_intents.return_value = []
    engine.risk_engine.get_status.return_value = RiskStatus(
        kill_switch_active=False,
        daily_drawdown_pct=0.0,
        drift_flag=False,
        total_exposure_pct=0.0,
        manual_exposure_pct=0.0,
        per_asset_exposure_pct={},
        per_strategy_exposure_pct={},
    )
    engine.risk_engine.build_risk_context.return_value = SimpleNamespace(
        per_strategy_exposure_pct={}
    )

    engine.strategies = {"fake": FilteredStrategy(strat_config)}
    engine.strategy_states = {
        "fake": StrategyState(
            strategy_id="fake",
            enabled=True,
            last_intents_at=None,
            last_actions_at=None,
            current_positions=[],
            pnl_summary={},
        )
    }

    plan = engine.run_cycle(datetime.now(timezone.utc))

    assert plan.actions == []
    engine.risk_engine.process_intents.assert_called_once()
    assert engine.risk_engine.process_intents.call_args.args[0] == []
    evaluation = plan.metadata["strategy_evaluation"]["fake"]
    assert evaluation["intents_emitted"] == 3
    assert evaluation["actions_after_scoring"] == 0
    assert evaluation["filtered_by_score"] == 3
    assert evaluation["filtered_position_exits"] == 1
    assert evaluation["filtered_no_position_exits"] == 1
    assert evaluation["filtered_low_score_entries"] == 1


def test_strategy_evaluation_classifies_display_pair_exit_against_canonical_position():
    class DisplayExitStrategy(Strategy):
        def warmup(self, market_data, portfolio):
            return None

        def generate_intents(self, ctx):
            return [
                StrategyIntent(
                    strategy_id=self.id,
                    pair="BTC/USD",
                    side="flat",
                    intent_type="exit",
                    desired_exposure_usd=0.0,
                    confidence=0.0,
                    timeframe=ctx.timeframe,
                    generated_at=ctx.now,
                )
            ]

    strat_config = StrategyConfig(
        name="fake", type="fake", enabled=True, params={"timeframes": ["1h"]}
    )
    strategies_cfg = StrategiesConfig(enabled=["fake"], configs={"fake": strat_config})

    app_config = MagicMock(spec=AppConfig)
    app_config.strategies = strategies_cfg
    app_config.risk = RiskConfig()
    app_config.universe = MagicMock()
    app_config.universe.include_pairs = ["BTC/USD"]

    market = MagicMock(spec=MarketDataAPI)
    market.normalize_pair.side_effect = lambda pair: {
        "BTC/USD": "XBTUSD",
        "XBTUSD": "XBTUSD",
    }.get(pair, str(pair).replace("/", "").upper())
    market.get_data_status.return_value = MagicMock(
        rest_api_reachable=True,
        websocket_connected=True,
        stale_pairs=0,
    )
    market.get_universe.return_value = ["XBTUSD"]
    market.get_display_pair.side_effect = lambda pair: {
        "XBTUSD": "BTC/USD",
    }.get(pair, pair)

    portfolio = make_portfolio_service_mock()
    portfolio.get_positions.return_value = [
        SpotPosition(
            pair="XBTUSD",
            base_asset="XBT",
            quote_asset="USD",
            base_size=1.0,
            avg_entry_price=100.0,
            realized_pnl_base=0.0,
            fees_paid_base=0.0,
            strategy_tag="fake",
        )
    ]
    portfolio.get_realized_pnl_by_strategy.return_value = {}

    engine = StrategyRiskEngine(app_config, market, portfolio)
    engine._data_ready = MagicMock(return_value=True)
    engine.risk_engine = MagicMock()
    engine.risk_engine.process_intents.return_value = []
    engine.risk_engine.get_status.return_value = RiskStatus(
        kill_switch_active=False,
        daily_drawdown_pct=0.0,
        drift_flag=False,
        total_exposure_pct=0.0,
        manual_exposure_pct=0.0,
        per_asset_exposure_pct={},
        per_strategy_exposure_pct={},
    )
    engine.risk_engine.build_risk_context.return_value = SimpleNamespace(
        per_strategy_exposure_pct={}
    )
    engine.strategies = {"fake": DisplayExitStrategy(strat_config)}
    engine.strategy_states = {
        "fake": StrategyState(
            strategy_id="fake",
            enabled=True,
            last_intents_at=None,
            last_actions_at=None,
            current_positions=[],
            pnl_summary={},
        )
    }

    plan = engine.run_cycle(datetime.now(timezone.utc))

    assert plan.actions == []
    engine.risk_engine.process_intents.assert_called_once()
    assert engine.risk_engine.process_intents.call_args.args[0] == []
    evaluation = plan.metadata["strategy_evaluation"]["fake"]
    assert evaluation["intents_emitted"] == 1
    assert evaluation["actions_after_scoring"] == 0
    assert evaluation["filtered_by_score"] == 1
    assert evaluation["filtered_position_exits"] == 1
    assert evaluation["filtered_no_position_exits"] == 0
    assert evaluation["filtered_low_score_entries"] == 0


def test_trend_following_ignores_missing_liquidity_metadata():
    strat_config = StrategyConfig(
        name="trend_core",
        type="trend_following",
        enabled=True,
        params={"timeframes": ["1h"], "ma_fast": 5, "ma_slow": 10},
    )
    strategy = TrendFollowingStrategy(strat_config)

    market = MagicMock(spec=MarketDataAPI)
    market.get_pair_metadata.return_value = MagicMock(liquidity_24h_usd=None)

    from dataclasses import dataclass

    @dataclass
    class MockBar:
        close: float

    prices = [100 + i for i in range(20)]
    market.get_ohlc.side_effect = [
        [MockBar(close=p) for p in prices],
        [MockBar(close=p) for p in prices],
    ]

    portfolio = make_portfolio_service_mock()
    portfolio.app_config = MagicMock()
    portfolio.app_config.risk = RiskConfig(min_liquidity_24h_usd=100000.0)
    portfolio.get_positions.return_value = []

    ctx = SimpleNamespace(
        timeframe="1h",
        universe=["XBTUSD"],
        market_data=market,
        portfolio=portfolio,
        regime=None,
        now=datetime.now(timezone.utc),
    )

    intents = strategy.generate_intents(ctx)

    assert len(intents) == 1
    assert intents[0].pair == "XBTUSD"


def test_trend_following_does_not_exit_without_owned_position():
    strat_config = StrategyConfig(
        name="trend_core",
        type="trend_following",
        enabled=True,
        params={"timeframes": ["1h"], "ma_fast": 5, "ma_slow": 10},
    )
    strategy = TrendFollowingStrategy(strat_config)

    market = MagicMock(spec=MarketDataAPI)
    market.get_pair_metadata.return_value = MagicMock(liquidity_24h_usd=1_000_000.0)

    from dataclasses import dataclass

    @dataclass
    class MockBar:
        close: float

    prices = [100.0 for _ in range(20)]
    market.get_ohlc.side_effect = [
        [MockBar(close=p) for p in prices],
        [MockBar(close=p) for p in prices],
    ]

    portfolio = make_portfolio_service_mock()
    portfolio.app_config = MagicMock()
    portfolio.app_config.risk = RiskConfig(min_liquidity_24h_usd=100000.0)
    portfolio.get_positions.return_value = [
        SpotPosition(
            pair="XBTUSD",
            base_asset="XBT",
            quote_asset="USD",
            base_size=1.0,
            avg_entry_price=100.0,
            realized_pnl_base=0.0,
            fees_paid_base=0.0,
            strategy_tag="other_strategy",
        )
    ]

    ctx = SimpleNamespace(
        timeframe="1h",
        universe=["XBTUSD"],
        market_data=market,
        portfolio=portfolio,
        regime=None,
        now=datetime.now(timezone.utc),
    )

    assert strategy.generate_intents(ctx) == []


def test_trend_following_reduces_owned_position_when_trend_is_flat():
    strat_config = StrategyConfig(
        name="trend_core",
        type="trend_following",
        enabled=True,
        params={"timeframes": ["1h"], "ma_fast": 5, "ma_slow": 10},
    )
    strategy = TrendFollowingStrategy(strat_config)

    market = MagicMock(spec=MarketDataAPI)
    market.get_pair_metadata.return_value = MagicMock(liquidity_24h_usd=1_000_000.0)

    from dataclasses import dataclass

    @dataclass
    class MockBar:
        close: float

    prices = [100.0 for _ in range(20)]
    market.get_ohlc.side_effect = [
        [MockBar(close=p) for p in prices],
        [MockBar(close=p) for p in prices],
    ]

    portfolio = make_portfolio_service_mock()
    portfolio.app_config = MagicMock()
    portfolio.app_config.risk = RiskConfig(min_liquidity_24h_usd=100000.0)
    portfolio.get_positions.return_value = [
        SpotPosition(
            pair="XBTUSD",
            base_asset="XBT",
            quote_asset="USD",
            base_size=1.0,
            avg_entry_price=100.0,
            realized_pnl_base=0.0,
            fees_paid_base=0.0,
            strategy_tag="trend_core",
        )
    ]

    ctx = SimpleNamespace(
        timeframe="1h",
        universe=["XBTUSD"],
        market_data=market,
        portfolio=portfolio,
        regime=None,
        now=datetime.now(timezone.utc),
    )

    intents = strategy.generate_intents(ctx)

    assert len(intents) == 1
    assert intents[0].side == "flat"
    assert intents[0].intent_type == "reduce"


def test_trend_following_matches_display_pair_to_canonical_owned_position_for_reduce():
    strat_config = StrategyConfig(
        name="trend_core",
        type="trend_following",
        enabled=True,
        params={
            "pairs": ["BTC/USD"],
            "timeframes": ["1h"],
            "ma_fast": 5,
            "ma_slow": 10,
        },
    )
    strategy = TrendFollowingStrategy(strat_config)

    market = MagicMock(spec=MarketDataAPI)
    market.normalize_pair.side_effect = lambda pair: {
        "BTC/USD": "XBTUSD",
        "XBTUSD": "XBTUSD",
    }.get(pair, str(pair).replace("/", "").upper())
    market.get_pair_metadata.return_value = MagicMock(liquidity_24h_usd=1_000_000.0)

    from dataclasses import dataclass

    @dataclass
    class MockBar:
        close: float

    prices = [100.0 for _ in range(20)]
    market.get_ohlc.side_effect = [
        [MockBar(close=p) for p in prices],
        [MockBar(close=p) for p in prices],
    ]

    portfolio = make_portfolio_service_mock()
    portfolio.app_config = MagicMock()
    portfolio.app_config.risk = RiskConfig(min_liquidity_24h_usd=100000.0)
    portfolio.get_positions.return_value = [
        SpotPosition(
            pair="XBTUSD",
            base_asset="XBT",
            quote_asset="USD",
            base_size=1.0,
            avg_entry_price=100.0,
            realized_pnl_base=0.0,
            fees_paid_base=0.0,
            strategy_tag="trend_core",
        )
    ]

    ctx = SimpleNamespace(
        timeframe="1h",
        universe=["BTC/USD"],
        market_data=market,
        portfolio=portfolio,
        regime=None,
        now=datetime.now(timezone.utc),
    )

    intents = strategy.generate_intents(ctx)

    assert len(intents) == 1
    assert intents[0].pair == "BTC/USD"
    assert intents[0].side == "flat"
    assert intents[0].intent_type == "reduce"


def test_dca_rebalance_matches_display_pair_to_canonical_position():
    strat_config = StrategyConfig(
        name="dca_overlay",
        type="dca_rebalance",
        enabled=True,
        params={
            "pairs": ["BTC/USD"],
            "target_weights": {"BTC/USD": 0.1},
            "rebalance_threshold_pct": 1.0,
            "dca_interval_minutes": 60,
            "dca_notional_usd": 100.0,
        },
    )
    strategy = DcaRebalanceStrategy(strat_config)

    market = MagicMock(spec=MarketDataAPI)
    market.normalize_pair.side_effect = lambda pair: {
        "BTC/USD": "XBTUSD",
        "XBTUSD": "XBTUSD",
    }.get(pair, str(pair).replace("/", "").upper())
    market.get_latest_price.return_value = 100.0

    portfolio = make_portfolio_service_mock(equity_base=10000.0, cash_base=9500.0)
    portfolio.get_positions.return_value = [
        SpotPosition(
            pair="XBTUSD",
            base_asset="XBT",
            quote_asset="USD",
            base_size=5.0,
            avg_entry_price=100.0,
            realized_pnl_base=0.0,
            fees_paid_base=0.0,
            strategy_tag="dca_overlay",
        )
    ]

    ctx = SimpleNamespace(
        timeframe="1h",
        universe=["BTC/USD"],
        market_data=market,
        portfolio=portfolio,
        regime=None,
        now=datetime.now(timezone.utc),
    )

    intents = strategy.generate_intents(ctx)

    assert len(intents) == 1
    assert intents[0].pair == "BTC/USD"
    assert intents[0].intent_type == "increase"
    assert intents[0].desired_exposure_usd == 600.0


def test_actions_inherit_userref_and_persist_in_execution_plan():
    class FakeStrategy(Strategy):
        def warmup(self, market_data, portfolio):
            return None

        def generate_intents(self, ctx):
            return [
                StrategyIntent(
                    strategy_id=self.id,
                    pair="XBTUSD",
                    side="long",
                    intent_type="enter",
                    desired_exposure_usd=1000.0,
                    confidence=0.9,
                    timeframe=ctx.timeframe,
                    generated_at=ctx.now,
                )
            ]

    strat_config = StrategyConfig(
        name="fake",
        type="fake",
        enabled=True,
        userref=4242,
        params={"timeframes": ["1h"]},
    )
    strategies_cfg = StrategiesConfig(enabled=["fake"], configs={"fake": strat_config})

    app_config = MagicMock(spec=AppConfig)
    app_config.strategies = strategies_cfg
    app_config.risk = RiskConfig()
    app_config.universe = MagicMock()
    app_config.universe.include_pairs = ["XBTUSD"]

    market = MagicMock(spec=MarketDataAPI)
    market.get_data_status.return_value = MagicMock(
        rest_api_reachable=True,
        websocket_connected=True,
        stale_pairs=0,
    )

    portfolio = make_portfolio_service_mock()

    engine = StrategyRiskEngine(app_config, market, portfolio)
    engine._data_ready = MagicMock(return_value=True)

    engine.risk_engine = MagicMock()
    engine.risk_engine._kill_switch_active = False
    engine.risk_engine.get_status.return_value = RiskStatus(
        kill_switch_active=False,
        daily_drawdown_pct=0.0,
        drift_flag=False,
        total_exposure_pct=0.0,
        manual_exposure_pct=0.0,
        per_asset_exposure_pct={},
        per_strategy_exposure_pct={},
    )
    engine.risk_engine.build_risk_context.return_value = SimpleNamespace(
        per_strategy_exposure_pct={}
    )

    fake_action = RiskAdjustedAction(
        pair="XBTUSD",
        strategy_id="fake",
        action_type="open",
        target_base_size=0.05,
        target_notional_usd=1000.0,
        current_base_size=0.0,
        reason="test",
        blocked=False,
        blocked_reasons=[],
        risk_limits_snapshot={},
    )
    engine.risk_engine.process_intents.return_value = [fake_action]

    fake_strategy = FakeStrategy(strat_config)
    engine.strategies = {"fake": fake_strategy}
    engine.strategy_states = {
        "fake": StrategyState(
            strategy_id="fake",
            enabled=True,
            last_intents_at=None,
            last_actions_at=None,
            current_positions=[],
            pnl_summary={},
        )
    }

    plan = engine.run_cycle(datetime.now(timezone.utc))

    assert plan.actions[0].strategy_id == "fake"
    assert plan.actions[0].userref == "4242"
    portfolio.record_execution_plan.assert_called_once()
    persisted_plan = portfolio.record_execution_plan.call_args[0][0]
    assert persisted_plan.actions[0].userref == "4242"
    decision_record = portfolio.record_decision.call_args_list[0][0][0]
    assert decision_record.strategy_name == "fake"


def test_build_emergency_flatten_plan():
    engine = StrategyRiskEngine.__new__(StrategyRiskEngine)
    engine.market_data = MagicMock()

    # Mock metadata returns a valid object so dust check passes
    mock_meta = MagicMock()
    mock_meta.min_order_size = 0.0001
    mock_meta.volume_decimals = 4
    mock_meta.canonical = "PAIR"
    engine.market_data.get_pair_metadata.return_value = mock_meta

    positions = [
        SpotPosition(
            pair="XBTUSD",
            base_asset="XBT",
            quote_asset="USD",
            base_size=1.5,
            avg_entry_price=10.0,
            realized_pnl_base=0.0,
            fees_paid_base=0.0,
            strategy_tag="trend",
        ),
        SpotPosition(
            pair="ETHUSD",
            base_asset="ETH",
            quote_asset="USD",
            base_size=-0.5,
            avg_entry_price=20.0,
            realized_pnl_base=0.0,
            fees_paid_base=0.0,
            strategy_tag=None,
        ),
        SpotPosition(
            pair="DOGEUSD",
            base_asset="DOGE",
            quote_asset="USD",
            base_size=0.0,
            avg_entry_price=0.0,
            realized_pnl_base=0.0,
            fees_paid_base=0.0,
            strategy_tag="ignore",
        ),
    ]

    plan = engine.build_emergency_flatten_plan(positions)

    assert plan.plan_id.startswith("flatten_")
    assert len(plan.actions) == 2
    assert all(action.action_type == "close" for action in plan.actions)
    assert all(action.target_base_size == 0.0 for action in plan.actions)
    assert all(action.target_notional_usd == 0.0 for action in plan.actions)
    assert {a.pair for a in plan.actions} == {"XBTUSD", "ETHUSD"}
    assert any(a.strategy_id == "trend" for a in plan.actions)
    assert any(a.strategy_id == "manual" for a in plan.actions)


def _data_ready_engine(portfolio, *, execution_mode: str | None = None):
    app_config = MagicMock(spec=AppConfig)
    app_config.strategies = StrategiesConfig(enabled=[], configs={})
    app_config.risk = RiskConfig()
    app_config.universe = SimpleNamespace(include_pairs=["XBTUSD"], exclude_pairs=[])
    if execution_mode is not None:
        app_config.execution = SimpleNamespace(mode=execution_mode)

    market = MagicMock(spec=MarketDataAPI)
    market.get_data_status.return_value = MagicMock(
        rest_api_reachable=True,
        websocket_connected=True,
        stale_pairs=0,
    )
    return StrategyRiskEngine(app_config, market, portfolio)


def test_data_ready_fails_closed_when_account_truth_unavailable():
    """_data_ready returns False when sync leaves last_sync_ok False without raising."""
    portfolio = make_portfolio_service_mock()
    portfolio.last_sync_ok = False
    portfolio.last_sync_reason = LIVE_SYNC_DEGRADED_REASON

    engine = _data_ready_engine(portfolio)

    assert engine._data_ready() is False
    portfolio.sync.assert_called_once()


def test_data_ready_true_when_sync_verified():
    portfolio = make_portfolio_service_mock()
    portfolio.last_sync_ok = True

    engine = _data_ready_engine(portfolio)

    assert engine._data_ready() is True
    portfolio.sync.assert_called_once()


def test_cached_risk_status_includes_live_degraded_portfolio_sync():
    portfolio = make_portfolio_service_mock()
    portfolio.last_sync_ok = False
    portfolio.last_sync_reason = LIVE_SYNC_DEGRADED_REASON
    portfolio.last_sync_at = datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)

    engine = _data_ready_engine(portfolio)
    engine.config.execution = SimpleNamespace(mode="live")
    engine.refresh_runtime_snapshots()

    status = engine.get_risk_status()

    assert status.portfolio_sync_ok is False
    assert status.portfolio_sync_reason == LIVE_SYNC_DEGRADED_REASON
    assert status.portfolio_last_sync_at == datetime(
        2026, 1, 2, 3, 4, tzinfo=timezone.utc
    )


def test_cached_risk_status_treats_live_cold_start_as_degraded():
    portfolio = make_portfolio_service_mock()
    portfolio.last_sync_ok = True
    portfolio.last_sync_reason = None
    portfolio.last_sync_at = None

    engine = _data_ready_engine(portfolio)
    engine.config.execution = SimpleNamespace(mode="live")
    engine.refresh_runtime_snapshots()

    status = engine.get_risk_status()

    assert status.portfolio_sync_ok is False
    assert status.portfolio_sync_reason == LIVE_SYNC_COLD_START_REASON
    assert status.portfolio_last_sync_at is None


def test_initial_cached_risk_status_treats_live_cold_start_as_degraded():
    portfolio = make_portfolio_service_mock()
    portfolio.last_sync_ok = True
    portfolio.last_sync_reason = None
    portfolio.last_sync_at = None

    engine = _data_ready_engine(portfolio, execution_mode="live")

    status = engine.get_risk_status()

    assert status.portfolio_sync_ok is False
    assert status.portfolio_sync_reason == LIVE_SYNC_COLD_START_REASON
    assert status.portfolio_last_sync_at is None


def test_initial_cached_risk_status_reports_stale_previous_sync_in_progress():
    synced_at = datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)
    portfolio = make_portfolio_service_mock()
    portfolio.last_sync_ok = True
    portfolio.last_sync_reason = None
    portfolio.last_sync_at = synced_at
    portfolio.sync_in_progress = True

    engine = _data_ready_engine(portfolio, execution_mode="live")

    status = engine.get_risk_status()

    assert status.portfolio_sync_ok is False
    assert status.portfolio_sync_reason == live_sync_stale_reason(600)
    assert status.portfolio_last_sync_at == synced_at
    assert status.portfolio_sync_in_progress is True


def test_get_risk_status_overlays_fresh_drift_over_cached_healthy_status():
    portfolio = make_portfolio_service_mock(drift_flag=False)
    portfolio.last_sync_ok = True
    portfolio.last_sync_reason = None
    portfolio.last_sync_at = datetime.now(timezone.utc)

    engine = _data_ready_engine(portfolio, execution_mode="live")
    assert engine._cached_risk_status.drift_flag is False

    portfolio.get_drift_status.return_value = SimpleNamespace(
        drift_flag=True,
        expected_position_value_base=100.0,
        actual_balance_value_base=95.0,
        tolerance_base=1.0,
        mismatched_assets=[],
    )

    status = engine.get_risk_status()

    assert status.drift_flag is True
    assert status.drift_info["expected_position_value_base"] == 100.0


def test_cached_risk_status_treats_live_stale_sync_as_degraded():
    synced_at = datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)
    portfolio = make_portfolio_service_mock()
    portfolio.last_sync_ok = True
    portfolio.last_sync_reason = None
    portfolio.last_sync_at = synced_at

    engine = _data_ready_engine(portfolio)
    engine.config.execution = SimpleNamespace(mode="live")
    engine.refresh_runtime_snapshots()

    status = engine.get_risk_status()

    assert status.portfolio_sync_ok is False
    assert status.portfolio_sync_reason == live_sync_stale_reason(600)
    assert status.portfolio_last_sync_at == synced_at
