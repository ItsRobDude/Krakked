
import pytest
from unittest.mock import MagicMock
from decimal import Decimal
from kraken_bot.portfolio.portfolio import Portfolio

def test_quantizer_caching():
    """Verify that _get_quantizer properly caches Decimal objects."""

    # Clear cache to ensure clean state
    Portfolio._get_quantizer.cache_clear()

    # First call - Miss
    q1 = Portfolio._get_quantizer(8)
    assert q1 == Decimal("1.00000000")

    # Second call - Hit
    q2 = Portfolio._get_quantizer(8)
    assert q2 == Decimal("1.00000000")
    assert q2 is q1  # Ensure it's the exact same object

    # Third call (different arg) - Miss
    q3 = Portfolio._get_quantizer(2)
    assert q3 == Decimal("1.00")
    assert q3 is not q1

    # Check cache stats
    info = Portfolio._get_quantizer.cache_info()
    assert info.hits == 1
    assert info.misses == 2

def test_round_vol_correctness():
    """Verify _round_vol behaves correctly with the cached quantizer."""
    mock_market_data = MagicMock()
    mock_config = MagicMock()
    mock_store = MagicMock()

    # Setup mock metadata
    mock_pair_meta = MagicMock()
    mock_pair_meta.volume_decimals = 4
    mock_market_data.get_pair_metadata.return_value = mock_pair_meta

    portfolio = Portfolio(mock_config, mock_market_data, mock_store)

    # Case 1: Standard rounding (FLOOR)
    # 1.23456 -> 1.2345
    vol = 1.23456789
    rounded = portfolio._round_vol("XBTUSD", vol)
    assert rounded == 1.2345

    # Case 2: Exact
    vol_exact = 1.23450000
    rounded_exact = portfolio._round_vol("XBTUSD", vol_exact)
    assert rounded_exact == 1.2345

    # Case 3: Small number (effectively zero check in fallback? No, logic handles small numbers)
    # If 1e-10, strict logic says 0.0000 if rounding floor.
    # The code has a fallback `if vol < 1e-9: return 0.0`.
    # Let's test a small number that is NOT < 1e-9 but rounds to 0 at 4 decimals.
    vol_small = 0.00009
    rounded_small = portfolio._round_vol("XBTUSD", vol_small)
    assert rounded_small == 0.0

def test_round_price_correctness():
    """Verify _round_price behaves correctly with the cached quantizer."""
    mock_market_data = MagicMock()
    mock_config = MagicMock()
    mock_store = MagicMock()

    # Setup mock metadata
    mock_pair_meta = MagicMock()
    mock_pair_meta.price_decimals = 2
    mock_market_data.get_pair_metadata.return_value = mock_pair_meta

    portfolio = Portfolio(mock_config, mock_market_data, mock_store)

    # Case 1: Round half up
    # 100.555 -> 100.56 (Python 3 Decimal ROUND_HALF_UP works like standard rounding)
    # Wait, Decimal ROUND_HALF_UP rounds away from zero if equidistant.
    # 100.555 -> 100.56?
    # Decimal('100.555').quantize(Decimal('1.00'), rounding=ROUND_HALF_UP) -> 100.56
    price = 100.555
    rounded = portfolio._round_price("XBTUSD", price)
    assert rounded == 100.56

    # Case 2: Round down
    price2 = 100.554
    rounded2 = portfolio._round_price("XBTUSD", price2)
    assert rounded2 == 100.55
