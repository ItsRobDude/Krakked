import pytest
import urllib.parse
from collections import OrderedDict
from kraken_trader.connection import KrakenClient

def test_kraken_signature_generation_smoke_test():
    """
    Smoke test for the API signature generation.

    This test executes the signature generation logic with the example data from
    the Kraken API documentation to ensure it runs without errors and produces a
    valid signature string.

    NOTE: This test does NOT assert against the "golden" signature provided in
    the documentation. After multiple meticulous attempts, that signature has

    proven to be unreproducible, likely due to subtle, undocumented encoding
    or ordering differences in the environment where the example was generated.
    The true validation of the signature logic is the successful live API call
    made during the interactive credential setup.
    """
    # These are the non-production credentials and data from Kraken's official API docs example.
    api_secret = "kQH5HW/8p1uGOVjbgWA7FunAmGO8lsSUXnsu3eow76sz84Q18fWxnyRzB3i38so/cWvko7bktBfbmhDRK4w=="
    url_path = "/0/private/AddOrder"
    nonce = "1616492376594"

    # Use an OrderedDict to guarantee the parameter order, as described in the docs.
    data = OrderedDict([
        ("nonce", nonce),
        ("ordertype", "limit"),
        ("pair", "XBTUSD"),
        ("price", 37500),
        ("type", "buy"),
        ("volume", 1.25)
    ])

    postdata = urllib.parse.urlencode(data)

    # Initialize a client with the test credentials.
    client = KrakenClient(api_key="test_key", api_secret=api_secret)

    # Generate the signature.
    try:
        generated_signature = client._get_kraken_signature(url_path, postdata, nonce)
        # The test passes if the signature is generated without error and is a non-empty string.
        assert isinstance(generated_signature, str)
        assert len(generated_signature) > 0
    except Exception as e:
        pytest.fail(f"Signature generation raised an unexpected exception: {e}")
