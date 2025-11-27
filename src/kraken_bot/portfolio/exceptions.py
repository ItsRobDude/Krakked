# src/kraken_bot/portfolio/exceptions.py

class PortfolioError(Exception):
    """Base exception for portfolio related errors."""
    pass

class PositionNotFoundError(PortfolioError):
    """Raised when a position for a pair is not found."""
    def __init__(self, pair: str):
        super().__init__(f"Position not found for pair: {pair}")
        self.pair = pair

class ReconciliationError(PortfolioError):
    """Raised when reconciliation fails or drift is detected beyond tolerance."""
    def __init__(self, message: str, discrepancies: dict):
        super().__init__(message)
        self.discrepancies = discrepancies

class ValuationError(PortfolioError):
    """Raised when an asset cannot be valued."""
    pass
