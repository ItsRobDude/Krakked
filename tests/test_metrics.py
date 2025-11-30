import pytest

from kraken_bot.metrics import SystemMetrics
from kraken_bot.ui.models import SystemMetricsPayload


@pytest.fixture
def metrics():
    return SystemMetrics(max_errors=10)


def test_record_plan_updates_counters(metrics: SystemMetrics):
    metrics.record_plan(blocked_actions=2)
    metrics.record_plan(blocked_actions=-3)

    snapshot = metrics.snapshot()

    assert snapshot["plans_generated"] == 2
    assert snapshot["blocked_actions"] == 2


def test_record_plan_execution_tracks_errors(metrics: SystemMetrics):
    metrics.record_plan_execution(["first", "second"])

    snapshot = metrics.snapshot()

    assert snapshot["plans_executed"] == 1
    assert snapshot["execution_errors"] == 2
    assert [record["message"] for record in snapshot["recent_errors"]] == [
        "second",
        "first",
    ]


def test_record_blocked_actions_only_counts_blocked(metrics: SystemMetrics):
    metrics.record_blocked_actions(0)
    metrics.record_blocked_actions(3)

    snapshot = metrics.snapshot()

    assert snapshot["blocked_actions"] == 3
    assert snapshot["plans_generated"] == 0
    assert snapshot["plans_executed"] == 0


def test_record_error_variants(metrics: SystemMetrics):
    metrics.record_error("execution issue")
    metrics.record_market_data_error("market data issue")

    snapshot = metrics.snapshot()

    assert snapshot["execution_errors"] == 1
    assert snapshot["market_data_errors"] == 1
    assert snapshot["recent_errors"][0]["message"] == "market data issue"
    assert snapshot["recent_errors"][1]["message"] == "execution issue"


@pytest.mark.parametrize(
    "equity_usd, realized_pnl_usd, unrealized_pnl_usd, open_orders_count, open_positions_count",
    [
        (1000.0, 50.0, -10.0, 3, 1),
        (None, None, None, 0, 0),
    ],
)
def test_update_portfolio_state_updates_fields(
    metrics: SystemMetrics,
    equity_usd,
    realized_pnl_usd,
    unrealized_pnl_usd,
    open_orders_count,
    open_positions_count,
):
    metrics.update_portfolio_state(
        equity_usd=equity_usd,
        realized_pnl_usd=realized_pnl_usd,
        unrealized_pnl_usd=unrealized_pnl_usd,
        open_orders_count=open_orders_count,
        open_positions_count=open_positions_count,
    )

    snapshot = metrics.snapshot()

    if equity_usd is None:
        assert snapshot["last_equity_usd"] is None
    else:
        assert snapshot["last_equity_usd"] == pytest.approx(equity_usd)

    if realized_pnl_usd is None:
        assert snapshot["last_realized_pnl_usd"] is None
    else:
        assert snapshot["last_realized_pnl_usd"] == pytest.approx(realized_pnl_usd)

    if unrealized_pnl_usd is None:
        assert snapshot["last_unrealized_pnl_usd"] is None
    else:
        assert snapshot["last_unrealized_pnl_usd"] == pytest.approx(unrealized_pnl_usd)
    assert snapshot["open_orders_count"] == open_orders_count
    assert snapshot["open_positions_count"] == open_positions_count


def test_record_drift_sets_and_clears_reason(metrics: SystemMetrics):
    metrics.record_drift(True, "Drift detected")
    first_snapshot = metrics.snapshot()

    metrics.record_drift(False)
    second_snapshot = metrics.snapshot()

    assert first_snapshot["drift_detected"] is True
    assert first_snapshot["drift_reason"] == "Drift detected"
    assert first_snapshot["recent_errors"][0]["message"] == "Drift detected"

    assert second_snapshot["drift_detected"] is False
    assert second_snapshot["drift_reason"] is None


def test_update_market_data_status(metrics: SystemMetrics):
    metrics.update_market_data_status(
        ok=True,
        stale=False,
        reason="Fresh data",
        max_staleness=1.5,
    )

    snapshot = metrics.snapshot()

    assert snapshot["market_data_ok"] is True
    assert snapshot["market_data_stale"] is False
    assert snapshot["market_data_reason"] == "Fresh data"
    assert snapshot["market_data_max_staleness"] == 1.5


def test_snapshot_keys_match_payload(metrics: SystemMetrics):
    keys = set(metrics.snapshot().keys())
    expected = {
        "plans_generated",
        "plans_executed",
        "blocked_actions",
        "execution_errors",
        "market_data_errors",
        "recent_errors",
        "last_equity_usd",
        "last_realized_pnl_usd",
        "last_unrealized_pnl_usd",
        "open_orders_count",
        "open_positions_count",
        "drift_detected",
        "drift_reason",
        "market_data_ok",
        "market_data_stale",
        "market_data_reason",
        "market_data_max_staleness",
    }

    assert keys == expected


def test_system_metrics_payload_accepts_snapshot(metrics: SystemMetrics):
    metrics.record_plan(blocked_actions=1)
    metrics.record_plan_execution(["execution error"])
    metrics.record_error("adhoc error")
    metrics.record_market_data_error("stale_data")
    metrics.update_portfolio_state(
        equity_usd=1234.5,
        realized_pnl_usd=10.0,
        unrealized_pnl_usd=-2.5,
        open_orders_count=2,
        open_positions_count=1,
    )
    metrics.record_drift(True, "Portfolio drift detected")
    metrics.update_market_data_status(
        ok=False, stale=True, reason="stale_data", max_staleness=5.5
    )

    snapshot = metrics.snapshot()

    payload = SystemMetricsPayload(**snapshot)

    assert payload.plans_generated == 1
    assert payload.blocked_actions == 1
    assert payload.execution_errors == 2
    assert payload.market_data_errors == 1
    assert payload.last_equity_usd == 1234.5
    assert payload.drift_detected is True
    assert payload.market_data_stale is True
    assert payload.market_data_reason == "stale_data"
    assert payload.market_data_max_staleness == 5.5
    assert payload.recent_errors[0].message in {"stale_data", "Portfolio drift detected"}
