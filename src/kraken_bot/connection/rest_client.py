# src/kraken_bot/connection/rest_client.py

import requests
import time
from typing import Any
from .rate_limiter import RateLimiter

KRAKEN_API_URL = "https://api.kraken.com"
API_VERSION = "0"

class KrakenRESTClient:
    def __init__(self, api_url: str = KRAKEN_API_URL, calls_per_second: float = 0.5):
        self.api_url = api_url
        self.rate_limiter = RateLimiter(calls_per_second)
        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": "KrakenTradingBot/0.1.0"}
        )

    def _get_public_url(self, endpoint: str) -> str:
        return f"{self.api_url}/{API_VERSION}/public/{endpoint}"

    def get_public(self, endpoint: str, params: dict = None) -> dict[str, Any]:
        """
        Makes a GET request to a public Kraken API endpoint, respecting rate limits.
        """
        self.rate_limiter.wait()
        url = self._get_public_url(endpoint)
        try:
            response = self.session.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            if data.get("error"):
                # EAPI:Rate limit exceeded
                if "EAPI:Rate limit exceeded" in str(data["error"]):
                    # In case of a rate limit error, we can add a small penalty
                    # to the rate limiter to be more conservative.
                    time.sleep(1)
                raise Exception(f"Kraken API error: {data['error']}")
            return data.get("result", {})
        except requests.exceptions.RequestException as e:
            # Handle network errors
            raise ConnectionError(f"Failed to connect to Kraken API: {e}") from e
