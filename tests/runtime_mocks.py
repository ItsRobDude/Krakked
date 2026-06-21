from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

from krakked.portfolio.manager import PortfolioService
from krakked.portfolio.models import DriftStatus, EquityView
from krakked.portfolio.sync_status import (
    AccountTruthSnapshot,
    read_portfolio_sync_status,
)


def make_portfolio_service_mock(
    *,
    equity_base: float = 10000.0,
    cash_base: float = 10000.0,
    drift_flag: bool = False,
):
    """Build a PortfolioService mock with concrete cached reads."""

    portfolio = MagicMock(spec=PortfolioService)
    equity = EquityView(
        equity_base=equity_base,
        cash_base=cash_base,
        realized_pnl_base_total=0.0,
        unrealized_pnl_base_total=0.0,
        drift_flag=drift_flag,
    )
    portfolio.get_equity.return_value = equity
    portfolio.get_positions.return_value = []
    portfolio.get_asset_exposure.return_value = []
    portfolio.get_cached_equity.return_value = equity
    portfolio.get_cached_asset_exposure.return_value = []
    portfolio.get_cached_positions.return_value = []
    drift_status = DriftStatus(
        drift_flag=drift_flag,
        expected_position_value_base=0.0,
        actual_balance_value_base=0.0,
        tolerance_base=0.0,
        mismatched_assets=[],
    )
    portfolio.get_drift_status.return_value = drift_status
    portfolio.get_cached_drift_status.return_value = drift_status
    portfolio.get_realized_pnl_by_strategy.return_value = {}
    portfolio.record_execution_plan = MagicMock()
    portfolio.record_decision = MagicMock()
    portfolio.store = MagicMock()
    portfolio.store.get_snapshots.return_value = []
    portfolio.config = SimpleNamespace(base_currency="USD")
    portfolio.last_sync_ok = True
    portfolio.last_sync_reason = None
    portfolio.last_sync_at = None
    portfolio.sync_in_progress = False

    def _snapshot(*, execution_mode=None, now=None):
        now_value = now if isinstance(now, datetime) else datetime.now(UTC)
        sync_status = read_portfolio_sync_status(
            portfolio,
            execution_mode=execution_mode,
            now=now_value,
        )
        current_drift_status = portfolio.get_drift_status()
        return AccountTruthSnapshot(
            portfolio_sync_ok=sync_status.ok,
            portfolio_sync_reason=sync_status.reason,
            portfolio_last_sync_at=sync_status.last_sync_at,
            portfolio_sync_in_progress=sync_status.in_progress,
            drift_flag=bool(getattr(current_drift_status, "drift_flag", False)),
            drift_info={
                "expected_position_value_base": getattr(
                    current_drift_status, "expected_position_value_base", None
                ),
                "actual_balance_value_base": getattr(
                    current_drift_status, "actual_balance_value_base", None
                ),
                "tolerance_base": getattr(current_drift_status, "tolerance_base", None),
                "mismatched_assets": [],
            },
            generated_at=now_value,
            max_age_seconds=sync_status.max_age_seconds,
            age_seconds=sync_status.age_seconds,
        )

    portfolio.get_account_truth_snapshot.side_effect = _snapshot
    return portfolio
