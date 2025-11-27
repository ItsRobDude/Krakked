# src/kraken_bot/connection/exceptions.py

class KrakenAPIError(Exception):
    """Base exception for all Kraken API related errors."""
    pass

class AuthError(KrakenAPIError):
    """Raised when authentication fails (invalid API key, signature, or nonce)."""
    pass

class RateLimitError(KrakenAPIError):
    """Raised when API rate limits are exceeded."""
    pass

class ServiceUnavailableError(KrakenAPIError):
    """Raised when Kraken API is down or in maintenance."""
    pass
