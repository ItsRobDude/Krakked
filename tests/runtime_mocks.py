from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from krakked.portfolio.manager import PortfolioService
from krakked.portfolio.models import DriftStatus, EquityView


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
    portfolio.get_cached_equity.return_value = equity
    portfolio.get_cached_asset_exposure.return_value = []
    portfolio.get_cached_positions.return_value = []
    portfolio.get_cached_drift_status.return_value = DriftStatus(
        drift_flag=drift_flag,
        expected_position_value_base=0.0,
        actual_balance_value_base=0.0,
        tolerance_base=0.0,
        mismatched_assets=[],
    )
    portfolio.get_realized_pnl_by_strategy.return_value = {}
    portfolio.record_execution_plan = MagicMock()
    portfolio.record_decision = MagicMock()
    portfolio.store = MagicMock()
    portfolio.store.get_snapshots.return_value = []
    portfolio.config = SimpleNamespace(base_currency="USD")
    return portfolio
