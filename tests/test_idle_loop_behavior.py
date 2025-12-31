from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from kraken_bot.main import _run_loop_iteration


def test_inactive_session_does_zero_work():
    """Test A: Inactive session does zero work."""
    now = datetime(2024, 1, 1, tzinfo=timezone.utc)
    # Intervals are small, and last_* timestamps are old -> this forces work if not stopped
    portfolio_interval = 1
    strategy_interval = 1
    last_portfolio_sync = now - timedelta(seconds=2)
    last_strategy_cycle = now - timedelta(seconds=2)

    # Mocks
    portfolio = MagicMock()
    portfolio.last_sync_ok = True
    market_data = MagicMock()
    strategy_engine = MagicMock()
    execution_service = MagicMock()
    metrics = MagicMock()
    refresh_metrics_state = MagicMock()

    # Set side_effect=AssertionError to catch calls immediately if not swallowed
    error = AssertionError("should not be called")
    portfolio.sync.side_effect = error
    market_data.get_health_status.side_effect = error
    refresh_metrics_state.side_effect = error
    strategy_engine.run_cycle.side_effect = error
    execution_service.execute_plan.side_effect = error
    execution_service.refresh_open_orders.side_effect = error
    execution_service.reconcile_orders.side_effect = error

    session = SimpleNamespace(emergency_flatten=False)

    updated_sync, updated_cycle = _run_loop_iteration(
        now=now,
        strategy_interval=strategy_interval,
        portfolio_interval=portfolio_interval,
        last_strategy_cycle=last_strategy_cycle,
        last_portfolio_sync=last_portfolio_sync,
        portfolio=portfolio,
        market_data=market_data,
        strategy_engine=strategy_engine,
        execution_service=execution_service,
        metrics=metrics,
        refresh_metrics_state=refresh_metrics_state,
        session_active=False,
        session=session,
    )

    # Assert timestamps are unchanged
    assert updated_sync == last_portfolio_sync
    assert updated_cycle == last_strategy_cycle

    # Assert no work was done (call_count checks cover swallowed exceptions)
    assert portfolio.sync.call_count == 0
    assert execution_service.refresh_open_orders.call_count == 0
    assert execution_service.reconcile_orders.call_count == 0
    assert market_data.get_health_status.call_count == 0
    assert refresh_metrics_state.call_count == 0
    assert strategy_engine.run_cycle.call_count == 0
    assert execution_service.execute_plan.call_count == 0


def test_emergency_flatten_runs_while_inactive():
    """Test B: Emergency flatten runs while inactive."""
    now = datetime(2024, 1, 1, tzinfo=timezone.utc)
    portfolio_interval = 1
    strategy_interval = 1
    last_portfolio_sync = now - timedelta(seconds=2)
    last_strategy_cycle = now - timedelta(seconds=2)

    # Mocks
    portfolio = MagicMock()
    portfolio.last_sync_ok = True
    portfolio.get_positions.return_value = ["dummy_pos"]

    execution_service = MagicMock()
    execution_service.get_open_orders.return_value = []  # Empty list = safe to flatten

    strategy_engine = MagicMock()
    strategy_engine.build_emergency_flatten_plan.return_value = MagicMock(
        plan_id="flatten_plan"
    )

    metrics = MagicMock()
    refresh_metrics_state = MagicMock()
    market_data = MagicMock()

    session = SimpleNamespace(emergency_flatten=True)

    updated_sync, updated_cycle = _run_loop_iteration(
        now=now,
        strategy_interval=strategy_interval,
        portfolio_interval=portfolio_interval,
        last_strategy_cycle=last_strategy_cycle,
        last_portfolio_sync=last_portfolio_sync,
        portfolio=portfolio,
        market_data=market_data,
        strategy_engine=strategy_engine,
        execution_service=execution_service,
        metrics=metrics,
        refresh_metrics_state=refresh_metrics_state,
        session_active=False,
        session=session,
    )

    # Assert flatten path executed
    execution_service.cancel_all.assert_called()
    strategy_engine.build_emergency_flatten_plan.assert_called()
    execution_service.execute_plan.assert_called()
    refresh_metrics_state.assert_called()

    # Assert strategy cycle timestamp was updated to NOW
    assert updated_cycle == now


def test_emergency_flatten_clears_on_dust_only():
    """Test C: Emergency flatten clears when plan actions are empty (dust only)."""
    now = datetime(2024, 1, 1, tzinfo=timezone.utc)
    portfolio_interval = 1
    strategy_interval = 1
    last_portfolio_sync = now - timedelta(seconds=2)
    last_strategy_cycle = now - timedelta(seconds=2)

    # Mocks
    portfolio = MagicMock()
    portfolio.last_sync_ok = True
    portfolio.get_positions.return_value = ["dummy"]

    execution_service = MagicMock()
    execution_service.get_open_orders.return_value = []  # Safe to flatten

    strategy_engine = MagicMock()
    # Plan with empty actions
    strategy_engine.build_emergency_flatten_plan.return_value = MagicMock(
        plan_id="flatten_dust",
        actions=[],
        metadata={"dust_count_total": 1, "untradeable_count_total": 0},
    )
    # Config structure for dump_runtime_overrides
    strategy_engine.config = SimpleNamespace(
        session=SimpleNamespace(emergency_flatten=True)
    )

    metrics = MagicMock()
    refresh_metrics_state = MagicMock()
    market_data = MagicMock()

    session = SimpleNamespace(emergency_flatten=True)

    updated_sync, updated_cycle = _run_loop_iteration(
        now=now,
        strategy_interval=strategy_interval,
        portfolio_interval=portfolio_interval,
        last_strategy_cycle=last_strategy_cycle,
        last_portfolio_sync=last_portfolio_sync,
        portfolio=portfolio,
        market_data=market_data,
        strategy_engine=strategy_engine,
        execution_service=execution_service,
        metrics=metrics,
        refresh_metrics_state=refresh_metrics_state,
        session_active=False,
        session=session,
    )

    # Assertions
    # 1. Flatten logic called
    strategy_engine.build_emergency_flatten_plan.assert_called()

    # 2. Execution NOT called (empty actions)
    execution_service.execute_plan.assert_not_called()

    # 3. Emergency flag cleared
    assert session.emergency_flatten is False
    assert strategy_engine.config.session.emergency_flatten is False

    # 4. Metrics refreshed
    refresh_metrics_state.assert_called()
