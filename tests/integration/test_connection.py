import os
import pytest
from kraken_trader.connection import KrakenClient

# Mark all tests in this file to be skipped unless the RUN_INTEGRATION_TESTS env var is set
pytestmark = pytest.mark.skipif(
    not os.getenv("RUN_INTEGRATION_TESTS"),
    reason="Integration tests require live API keys and network connection."
)

@pytest.fixture(scope="module")
def live_client() -> KrakenClient:
    """Provides a KrakenClient instance with live API keys from env vars."""
    api_key = os.getenv("KRAKEN_API_KEY")
    api_secret = os.getenv("KRAKEN_API_SECRET")

    if not api_key or not api_secret:
        pytest.fail("KRAKEN_API_KEY and KRAKEN_API_SECRET must be set for integration tests.")

    return KrakenClient(api_key=api_key, api_secret=api_secret)

# --- Public Endpoint Integration Tests ---

def test_integration_get_server_time(live_client: KrakenClient):
    """Tests that we can successfully call the public Time endpoint."""
    result = live_client.get_server_time()
    assert "unixtime" in result
    assert "rfc1123" in result
    assert isinstance(result["unixtime"], int)

def test_integration_get_system_status(live_client: KrakenClient):
    """Tests that we can successfully call the public SystemStatus endpoint."""
    result = live_client.get_system_status()
    assert "status" in result
    assert "timestamp" in result
    assert result["status"] in ["online", "maintenance", "cancel_only", "post_only"]

# --- Private Endpoint Integration Tests ---

def test_integration_get_balance(live_client: KrakenClient):
    """
    Tests that we can successfully call the private Balance endpoint.
    This confirms that our authentication and signature logic is correct.
    """
    result = live_client.get_balance()
    # A successful call to get_balance will return a dictionary.
    # The dictionary will be empty if the account has no funds, which is a valid result.
    assert isinstance(result, dict)

def test_integration_get_trade_balance(live_client: KrakenClient):
    """
    Tests that we can successfully call the private TradeBalance endpoint.
    """
    # Using ZUSD as the default asset for the test
    result = live_client.get_trade_balance(asset="ZUSD")
    assert isinstance(result, dict)
    # Check for a field that is expected in the trade balance response
    assert "eb" in result  # equivalent balance
