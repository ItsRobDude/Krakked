# src/kraken_bot/connection/rest_client.py

import base64
import hashlib
import hmac
import urllib.parse
from typing import Any, Dict, Optional

import requests
from requests import HTTPError, RequestException, Timeout

from .exceptions import (
    AuthError,
    KrakenAPIError,
    RateLimitError,
    ServiceUnavailableError,
)
from .nonce import NonceGenerator
from .rate_limiter import RateLimiter

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
        request_timeout: float = 10.0,
    ):
        self.api_url = api_url
        # Use the same rate limiter for both public and private calls for simplicity and safety
        self.rate_limiter = rate_limiter or RateLimiter(calls_per_second)

        self.api_key = api_key
        self.api_secret = api_secret
        self.request_timeout = request_timeout

        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "KrakenTradingBot/0.1.0"})

        self.nonce_generator = NonceGenerator()

    def _get_url(self, endpoint: str, private: bool = False) -> str:
        access_type = "private" if private else "public"
        return f"{self.api_url}/{API_VERSION}/{access_type}/{endpoint}"

    def _generate_signature(
        self, urlpath: str, data: Dict[str, Any], nonce: int
    ) -> str:
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

    def _handle_api_error(self, error_messages: list[str]) -> None:
        """Categorizes and raises exceptions based on Kraken API error messages."""
        error_msg = "; ".join(error_messages)

        # Map error substrings to Exception types
        error_mapping = {
            "EAPI:Rate limit exceeded": RateLimitError,
            "EAPI:Invalid key": AuthError,
            "EAPI:Invalid signature": AuthError,
            "EAPI:Invalid nonce": AuthError,
            "EService:Unavailable": ServiceUnavailableError,
            "EService:Busy": ServiceUnavailableError,
        }

        for substring, exception_cls in error_mapping.items():
            if substring in error_msg:
                raise exception_cls(error_msg)

        raise KrakenAPIError(error_msg)

    def _request(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        private: bool = False,
    ) -> Dict[str, Any]:
        """
        Internal request handler that manages rate limiting, authentication, and error parsing.
        """
        self.rate_limiter.wait()

        method = "POST" if private else "GET"
        url = self._get_url(endpoint, private)
        headers = {}
        data: Dict[str, Any] = dict(params or {})

        if private:
            if not self.api_key or not self.api_secret:
                raise AuthError(
                    "API key and secret are required for private endpoints."
                )

            nonce = self.nonce_generator.generate()
            data["nonce"] = nonce

            # The path used for signature is usually /0/private/Endpoint
            urlpath = f"/{API_VERSION}/private/{endpoint}"
            signature = self._generate_signature(urlpath, data, nonce)

            headers["API-Key"] = self.api_key
            headers["API-Sign"] = signature

        try:
            response = self.session.request(
                method,
                url,
                params=data if method == "GET" else None,
                data=data if method == "POST" else None,
                headers=headers,
                timeout=self.request_timeout,
            )

            # 1. Attempt to parse JSON first to catch API-specific errors
            # even if the status code implies a generic failure (e.g. 500 or 520)
            try:
                response_json = response.json()
                if error_messages := response_json.get("error"):
                    self._handle_api_error(error_messages)
            except ValueError:
                # Not JSON, proceed to status checks
                response_json = {}

            # 2. Handle HTTP Errors if no API error was caught above
            if response.status_code == 429:
                raise RateLimitError("Rate limit exceeded")

            if 500 <= response.status_code < 600:
                # Include the first 200 chars of body for debugging context
                body_preview = response.text[:200] if response.text else "No body"
                raise ServiceUnavailableError(
                    f"Kraken API Service Error: HTTP {response.status_code} - {body_preview}"
                )

            response.raise_for_status()

            # 3. Return successful result
            return response_json.get("result", {})

        except HTTPError as e:
            # Re-check status codes in case raise_for_status() triggered this
            status_code = e.response.status_code if e.response else None
            if status_code == 429:
                raise RateLimitError("Rate limit exceeded") from e
            if status_code and 500 <= status_code < 600:
                raise ServiceUnavailableError(f"Kraken API Service Error: {e}") from e
            raise KrakenAPIError(f"HTTP Error: {e}") from e
        except Timeout as e:
            raise ServiceUnavailableError(f"Request timed out: {e}") from e
        except RequestException as e:
            raise ServiceUnavailableError(f"Network Error: {e}") from e

    def get_public(
        self, endpoint: str, params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Makes a GET request to a public Kraken API endpoint."""
        return self._request(endpoint, params=params, private=False)

    def get_private(
        self, endpoint: str, params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Makes a POST request to a private Kraken API endpoint (most private endpoints use POST).
        """
        return self._request(endpoint, params=params, private=True)

    def add_order(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Place a new order on the exchange.

        Endpoint: AddOrder

        Args:
            params: A dictionary of order parameters. Common keys include:
                - pair (str): Asset pair ID (e.g., 'XXBTZUSD').
                - type (str): Type of order (buy/sell).
                - ordertype (str): Order type (market/limit/stop-loss/etc.).
                - price (str/float): Price (optional, dependent on ordertype).
                - volume (str/float): Order volume in terms of the base asset.
                - userref (int): User reference id.
                - validate (bool): Validate inputs only. Do not submit order.

        Returns:
            Dict[str, Any]: The API response result (e.g., {'txid': ['...'], 'descr': '...'}).
        """
        return self.get_private("AddOrder", params=params)

    def cancel_order(self, txid: str) -> Dict[str, Any]:
        """
        Cancel a specific order.

        Endpoint: CancelOrder

        Args:
            txid: The transaction ID of the order to cancel.

        Returns:
            Dict[str, Any]: The API response result (e.g., {'count': 1}).
        """
        return self.get_private("CancelOrder", {"txid": txid})

    def cancel_all_orders(self) -> Dict[str, Any]:
        """
        Cancel all open orders.

        Endpoint: CancelAll

        Returns:
            Dict[str, Any]: The API response result (e.g., {'count': 2}).
        """
        return self.get_private("CancelAll")

    def cancel_all_orders_after(self, timeout_seconds: int) -> Dict[str, Any]:
        """
        Set a "Dead Man's Switch" to cancel all orders after a timeout.

        Endpoint: CancelAllOrdersAfter

        Args:
            timeout_seconds: Timeout in seconds. Set to 0 to disable.

        Returns:
            Dict[str, Any]: The API response result (e.g., {'currentTime': ..., 'triggerTime': ...}).
        """
        return self.get_private("CancelAllOrdersAfter", {"timeout": timeout_seconds})

    def get_ledgers(self, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Retrieve information about ledger entries.

        Endpoint: Ledgers

        Args:
            params: Optional dictionary of query parameters (e.g., 'asset', 'type', 'start', 'end').

        Returns:
            Dict[str, Any]: The ledger entries data.
        """
        return self.get_private("Ledgers", params=params)

    def get_open_orders(
        self, params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Retrieve currently open orders.

        Endpoint: OpenOrders

        Args:
            params: Optional dictionary of query parameters (e.g., 'trades', 'userref').

        Returns:
            Dict[str, Any]: The open orders data, keyed by txid.
        """
        return self.get_private("OpenOrders", params=params)

    def get_closed_orders(
        self, params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Retrieve information about closed orders.

        Endpoint: ClosedOrders

        Args:
            params: Optional dictionary of query parameters (e.g., 'trades', 'userref', 'start', 'end').

        Returns:
            Dict[str, Any]: The closed orders data, including count and list of orders.
        """
        return self.get_private("ClosedOrders", params=params)
