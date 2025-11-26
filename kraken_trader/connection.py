import time
import hmac
import hashlib
import base64
import urllib.parse
from typing import Optional, Any

import requests

from .nonce import NonceGenerator

class KrakenAPIError(Exception):
    """Custom exception for Kraken API errors."""
    def __init__(self, message: str, response: Optional[requests.Response] = None):
        super().__init__(message)
        self.response = response

class KrakenClient:
    """
    A client for interacting with the Kraken REST API.

    Handles both public and private endpoints, including authentication
    and signature generation.
    """

    BASE_URL = "https://api.kraken.com"

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        session: Optional[requests.Session] = None
    ):
        self.api_key = api_key
        self.api_secret = api_secret
        self.session = session or requests.Session()
        self.nonce_generator = NonceGenerator()

    def _handle_response(self, response: requests.Response) -> dict[str, Any]:
        """Processes the HTTP response from the Kraken API."""
        response.raise_for_status()
        result = response.json()

        if result.get('error'):
            error_messages = ", ".join(result['error'])
            raise KrakenAPIError(f"Kraken API error: {error_messages}", response=response)

        return result.get('result', {})

    def _public_get(self, path: str, data: Optional[dict] = None) -> dict:
        """Makes a GET request to a public Kraken endpoint."""
        url = self.BASE_URL + path
        response = self.session.get(url, params=data)
        return self._handle_response(response)

    def _private_post(self, path: str, data: Optional[dict] = None) -> dict:
        """Makes an authenticated POST request to a private Kraken endpoint."""
        if data is None:
            data = {}

        data['nonce'] = self.nonce_generator.generate_nonce()

        postdata = urllib.parse.urlencode(data)

        headers = {
            'API-Key': self.api_key,
            'API-Sign': self._get_kraken_signature(path, postdata, data['nonce'])
        }

        url = self.BASE_URL + path
        response = self.session.post(url, headers=headers, data=data)
        return self._handle_response(response)

    def _get_kraken_signature(self, url_path: str, postdata: str, nonce: str) -> str:
        """Generates the API-Sign header as required by Kraken."""
        encoded = (nonce + postdata).encode()
        message = url_path.encode() + hashlib.sha256(encoded).digest()

        mac = hmac.new(base64.b64decode(self.api_secret), message, hashlib.sha512)
        sigdigest = base64.b64encode(mac.digest())
        return sigdigest.decode()

    # --- Public Endpoints ---
    def get_server_time(self) -> dict:
        """Gets the server's time."""
        return self._public_get("/0/public/Time")

    def get_system_status(self) -> dict:
        """Gets the system's status."""
        return self._public_get("/0/public/SystemStatus")

    # --- Private Endpoints ---
    def get_balance(self) -> dict:
        """Fetches account balance."""
        return self._private_post("/0/private/Balance")

    def get_trade_balance(self, asset: str = "ZUSD") -> dict:
        """Fetches trade balance."""
        return self._private_post("/0/private/TradeBalance", data={"asset": asset})

    def get_trades_history(self) -> dict:
        """Fetches trades history."""
        return self._private_post("/0/private/TradesHistory")

    def get_ledgers(self) -> dict:
        """Fetches ledgers information."""
        return self._private_post("/0/private/Ledgers")
