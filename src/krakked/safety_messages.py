"""Shared operator-facing safety messages."""

PORTFOLIO_DRIFT_BLOCKED_MESSAGE = (
    "Krakked detected a live portfolio mismatch. New live orders remain blocked "
    "until portfolio reconciliation is clean."
)
PORTFOLIO_DRIFT_ORDER_BLOCKED_MESSAGE = (
    "Krakked detected a live portfolio mismatch, so this order was blocked "
    "before it reached Kraken. Orders will resume automatically once portfolio "
    "reconciliation is clean."
)
