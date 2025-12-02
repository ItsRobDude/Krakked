"""API route registrations for the UI layer."""

from .execution import router as execution_router
from .portfolio import router as portfolio_router
from .risk import router as risk_router
from .strategies import router as strategies_router
from .system import router as system_router

__all__ = [
    "portfolio_router",
    "risk_router",
    "strategies_router",
    "execution_router",
    "system_router",
]
