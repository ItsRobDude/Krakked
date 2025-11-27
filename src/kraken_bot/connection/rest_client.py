# src/kraken_bot/connection/rest_client.py

import requests
import time
import urllib.parse
import hashlib
import hmac
import base64
from typing import Any, Dict, Optional
from .rate_limiter import RateLimiter
from .nonce import NonceGenerator
from .exceptions import (
    KrakenAPIError,
    AuthError,
    RateLimitError,
    ServiceUnavailableError,
)

KRAKEN_API_URL = "https://api.kraken.com"
API_VERSION = "0"

class KrakenRESTClient:
    def __init__(
        self,
        api_url: str = KRAKEN_API_URL,
        calls_per_second: float = 0.5,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None
    ):
        self.api_url = api_url
        # Use the same rate limiter for both public and private calls for simplicity and safety
        self.rate_limiter = RateLimiter(calls_per_second)

        self.api_key = api_key
        self.api_secret = api_secret

        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": "KrakenTradingBot/0.1.0"}
        )

        self.nonce_generator = NonceGenerator()

    def _get_url(self, endpoint: str, private: bool = False) -> str:
        access_type = "private" if private else "public"
        return f"{self.api_url}/{API_VERSION}/{access_type}/{endpoint}"

    def _generate_signature(self, urlpath: str, data: Dict[str, Any], nonce: int) -> str:
        """
        Generates the API signature for private requests.
        API-Sign = Message signature using HMAC-SHA512 of (URI path + SHA256(nonce + POST data)) and base64 decoded secret API key
        """
        if not self.api_secret:
            raise AuthError("API secret is required for signing requests.")

        postdata = urllib.parse.urlencode(data)
        encoded = (str(nonce) + postdata).encode()
        message = urlpath.encode() + hashlib.sha256(encoded).digest()

        mac = hmac.new(base64.b64decode(self.api_secret), message, hashlib.sha512)
        sigdigest = base64.b64encode(mac.digest())
        return sigdigest.decode()

    def _request(self, method: str, endpoint: str, params: dict = None, private: bool = False) -> Dict[str, Any]:
        """
        Internal request handler that manages rate limiting, authentication, and error parsing.
        """
        self.rate_limiter.wait()

        url = self._get_url(endpoint, private)
        headers = {}
        data = params or {}

        if private:
            if not self.api_key or not self.api_secret:
                raise AuthError("API key and secret are required for private endpoints.")

            nonce = self.nonce_generator.generate()
            data["nonce"] = nonce

            # The path used for signature is usually /0/private/Endpoint
            urlpath = f"/{API_VERSION}/private/{endpoint}"
            signature = self._generate_signature(urlpath, data, nonce)

            headers["API-Key"] = self.api_key
            headers["API-Sign"] = signature

        try:
            if method.lower() == "get":
                response = self.session.get(url, params=data, headers=headers)
            elif method.lower() == "post":
                response = self.session.post(url, data=data, headers=headers)
            else:
                raise ValueError(f"Unsupported method: {method}")

            response.raise_for_status()
            response_json = response.json()

            if response_json.get("error"):
                error_msg = str(response_json["error"])

                # Categorize errors
                if "EAPI:Rate limit exceeded" in error_msg:
                    time.sleep(1) # Backoff slightly
                    raise RateLimitError(error_msg)
                elif "EAPI:Invalid key" in error_msg or "EAPI:Invalid signature" in error_msg or "EAPI:Invalid nonce" in error_msg:
                    raise AuthError(error_msg)
                elif "EService:Unavailable" in error_msg or "EService:Busy" in error_msg:
                    raise ServiceUnavailableError(error_msg)
                else:
                    raise KrakenAPIError(error_msg)

            return response_json.get("result", {})

        except requests.exceptions.HTTPError as e:
             # Handle HTTP 5xx errors as service issues
            if 500 <= e.response.status_code < 600:
                raise ServiceUnavailableError(f"Kraken API Service Error: {e}") from e
            raise KrakenAPIError(f"HTTP Error: {e}") from e
        except requests.exceptions.RequestException as e:
            raise KrakenAPIError(f"Network Error: {e}") from e

    def get_public(self, endpoint: str, params: dict = None) -> Dict[str, Any]:
        """Makes a GET request to a public Kraken API endpoint."""
        return self._request("get", endpoint, params=params, private=False)

    def get_private(self, endpoint: str, params: dict = None) -> Dict[str, Any]:
        """
        Makes a POST request to a private Kraken API endpoint (most private endpoints use POST).
        """
        return self._request("post", endpoint, params=params, private=True)

    def get_ledgers(self, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Retrieves information about ledger entries.
        Endpoint: Ledgers
        """
        return self.get_private("Ledgers", params=params)

    def get_closed_orders(self, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Retrieves information about closed orders.
        Endpoint: ClosedOrders
        """
        return self.get_private("ClosedOrders", params=params)
