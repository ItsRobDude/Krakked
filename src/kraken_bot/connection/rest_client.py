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
        api_secret: Optional[str] = None,
        rate_limiter: Optional[RateLimiter] = None,
    ):
        self.api_url = api_url
        # Use the same rate limiter for both public and private calls for simplicity and safety
        self.rate_limiter = rate_limiter or RateLimiter(calls_per_second)

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

            if response.status_code == 429:
                raise RateLimitError("Rate limit exceeded")

            if 500 <= response.status_code < 600:
                raise ServiceUnavailableError(f"Kraken API Service Error: HTTP {response.status_code}")

            response.raise_for_status()
            response_json = response.json()

            error_messages = response_json.get("error") or []
            if error_messages:
                error_msg = "; ".join(error_messages)

                # Categorize errors
                if "EAPI:Rate limit exceeded" in error_msg:
                    time.sleep(1) # Backoff slightly
                    raise RateLimitError(error_msg)
                elif (
                    "EAPI:Invalid key" in error_msg
                    or "EAPI:Invalid signature" in error_msg
                    or "EAPI:Invalid nonce" in error_msg
                ):
                    raise AuthError(error_msg)
                elif "EService:Unavailable" in error_msg or "EService:Busy" in error_msg:
                    raise ServiceUnavailableError(error_msg)
                else:
                    raise KrakenAPIError(error_msg)

            return response_json.get("result", {})

        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code if e.response else None
            if status_code == 429:
                raise RateLimitError("Rate limit exceeded") from e
            if status_code and 500 <= status_code < 600:
                raise ServiceUnavailableError(f"Kraken API Service Error: {e}") from e
            raise KrakenAPIError(f"HTTP Error: {e}") from e
        except requests.exceptions.Timeout as e:
            raise ServiceUnavailableError(f"Request timed out: {e}") from e
        except requests.exceptions.RequestException as e:
            raise ServiceUnavailableError(f"Network Error: {e}") from e

    def get_public(self, endpoint: str, params: dict = None) -> Dict[str, Any]:
        """Makes a GET request to a public Kraken API endpoint."""
        return self._request("get", endpoint, params=params, private=False)

    def get_private(self, endpoint: str, params: dict = None) -> Dict[str, Any]:
        """
        Makes a POST request to a private Kraken API endpoint (most private endpoints use POST).
        """
        return self._request("post", endpoint, params=params, private=True)

    def add_order(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return self.get_private("AddOrder", params=params)

    def cancel_order(self, txid: str) -> Dict[str, Any]:
        return self.get_private("CancelOrder", {"txid": txid})

    def cancel_all_orders(self) -> Dict[str, Any]:
        return self.get_private("CancelAll")

    def cancel_all_orders_after(self, timeout_seconds: int) -> Dict[str, Any]:
        return self.get_private("CancelAllOrdersAfter", {"timeout": timeout_seconds})

    def get_ledgers(self, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Retrieves information about ledger entries.
        Endpoint: Ledgers
        """
        return self.get_private("Ledgers", params=params)

    def get_open_orders(self, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Retrieves currently open orders. Endpoint: OpenOrders"""
        return self.get_private("OpenOrders", params=params)

    def get_closed_orders(self, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Retrieves information about closed orders.
        Endpoint: ClosedOrders
        """
        return self.get_private("ClosedOrders", params=params)
