# tests/test_rest_client.py

import pytest
import requests
import time
from unittest.mock import MagicMock, patch
from kraken_bot.connection.rest_client import KrakenRESTClient
from kraken_bot.connection.exceptions import AuthError, RateLimitError, KrakenAPIError, ServiceUnavailableError

@pytest.fixture
def client():
    return KrakenRESTClient(calls_per_second=100) # High limit for tests

def test_public_request_success(client):
    with patch.object(client.session, 'get') as mock_get:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"error": [], "result": {"server_time": 123456}}
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        result = client.get_public("Time")
        assert result == {"server_time": 123456}
        mock_get.assert_called_once()

def test_private_request_missing_credentials(client):
    # Client initialized without keys
    with pytest.raises(AuthError, match="API key and secret are required"):
        client.get_private("Balance")

def test_private_request_signature_generation(client):
    client.api_key = "test_key"
    # Provide a valid base64 string. "Secret" -> base64 encoded
    client.api_secret = "U2VjcmV0"

    with patch.object(client.session, 'post') as mock_post:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"error": [], "result": {"balance": 100}}
        mock_post.return_value = mock_response

        client.get_private("Balance")

        args, kwargs = mock_post.call_args
        headers = kwargs['headers']

        assert "API-Key" in headers
        assert "API-Sign" in headers
        assert headers["API-Key"] == "test_key"
        assert len(headers["API-Sign"]) > 0

def test_api_error_handling(client):
    with patch.object(client.session, 'get') as mock_get:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"error": ["EGeneral:Invalid arguments"]}
        mock_get.return_value = mock_response

        with pytest.raises(KrakenAPIError, match="EGeneral:Invalid arguments"):
            client.get_public("AssetPairs")

def test_rate_limit_error_handling(client):
    with patch.object(client.session, 'get') as mock_get:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"error": ["EAPI:Rate limit exceeded"]}
        mock_get.return_value = mock_response

        start_time = time.monotonic()
        with pytest.raises(RateLimitError):
            client.get_public("Time")
        duration = time.monotonic() - start_time
        assert duration >= 1.0 # Verify backoff sleep

def test_service_unavailable_error_handling(client):
    with patch.object(client.session, 'get') as mock_get:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"error": ["EService:Unavailable"]}
        mock_get.return_value = mock_response

        with pytest.raises(ServiceUnavailableError):
            client.get_public("Time")


def test_timeout_maps_to_service_unavailable(client):
    with patch.object(client.session, 'get', side_effect=requests.exceptions.Timeout("timeout")):
        with pytest.raises(ServiceUnavailableError):
            client.get_public("Time")


def test_http_rate_limit_status_maps_to_rate_limit_error(client):
    with patch.object(client.session, 'get') as mock_get:
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError(response=mock_response)
        mock_response.json.return_value = {"error": []}
        mock_get.return_value = mock_response

        with pytest.raises(RateLimitError):
            client.get_public("Time")
