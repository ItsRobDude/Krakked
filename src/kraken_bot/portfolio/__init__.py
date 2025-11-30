"""Portfolio utilities and state management.

This package exposes the high level :class:`Portfolio` tracker alongside the
existing service abstractions.  The tracker encapsulates balance and position
state, provides weighted-average cost PnL calculations, detects cashflows, and
creates periodic snapshots persisted via :class:`~kraken_bot.portfolio.store.PortfolioStore`.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .portfolio import Portfolio

__all__ = ["Portfolio"]


def __getattr__(name):  # pragma: no cover - lightweight lazy import helper
    if name == "Portfolio":
        from .portfolio import Portfolio

        return Portfolio
    raise AttributeError(name)
