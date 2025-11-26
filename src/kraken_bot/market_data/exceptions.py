# src/kraken_bot/market_data/exceptions.py

class MarketDataError(Exception):
    """Base exception for the market_data module."""
    pass

class DataStaleError(MarketDataError):
    """Raised when requested real-time data is older than the configured tolerance."""
    def __init__(self, pair: str, last_update: float, tolerance: float):
        self.pair = pair
        self.last_update = last_update
        self.tolerance = tolerance
        message = f"Data for pair '{pair}' is stale. Last update was {last_update:.2f}s ago (tolerance: {tolerance}s)."
        super().__init__(message)

class PairNotFoundError(MarketDataError):
    """Raised when data is requested for a pair not in the current universe."""
    def __init__(self, pair: str):
        self.pair = pair
        message = f"Pair '{pair}' not found in the universe."
        super().__init__(message)
