# src/kraken_bot/execution/exceptions.py

class ExecutionError(Exception):
    """Base exception for execution and OMS related errors."""


class OrderValidationError(ExecutionError):
    """Raised when an order fails local validation before submission."""


class OrderRejectedError(ExecutionError):
    """Raised when Kraken rejects an order request."""


class OrderCancelError(ExecutionError):
    """Raised when canceling an order fails."""


class KrakenExecutionError(ExecutionError):
    """Raised when Kraken returns an unexpected or fatal execution error."""
