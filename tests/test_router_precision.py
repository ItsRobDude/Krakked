from unittest.mock import MagicMock

from kraken_bot.execution.router import round_order_price, round_order_size
from kraken_bot.market_data.models import PairMetadata


def test_router_price_rounding_half_up():
    """
    Test that price rounding uses ROUND_HALF_UP (standard financial rounding),
    specifically verifying it behaves differently than Python 3's default
    'Banker's Rounding' (ROUND_HALF_EVEN).
    """
    meta = MagicMock(spec=PairMetadata)
    meta.price_decimals = 2

    # Case 1: 2.545
    # Banker's Rounding (Python default): Rounds to nearest EVEN -> 2.54
    # HALF_UP (Financial): Rounds up -> 2.55
    raw_val = 2.545
    assert round(raw_val, 2) == 2.54, "Sanity check: Python default is Banker's"
    assert round_order_price(meta, raw_val) == 2.55

    # Case 2: 100.005
    # Banker's Rounding: Rounds to 100.00 (0 is even)
    # HALF_UP: Rounds to 100.01
    raw_val_2 = 100.005
    assert round(raw_val_2, 2) == 100.00, "Sanity check: Python default is Banker's"
    assert round_order_price(meta, raw_val_2) == 100.01


def test_router_size_rounding_floor():
    """
    Test that size rounding uses ROUND_FLOOR to strictly truncate volume
    and avoid over-allocating or floating point drift.
    """
    meta = MagicMock(spec=PairMetadata)
    meta.volume_decimals = 8

    # Case 1: Simple truncation
    # 1.000000019 -> 1.00000001
    val = 1.000000019
    assert round_order_size(meta, val) == 1.00000001

    # Case 2: Floating point artifact (The '9' at the end should be floored out)
    # 0.000000019 -> 0.00000001
    val_dust = 0.000000019
    assert round_order_size(meta, val_dust) == 0.00000001

    # Case 3: Exact value should remain exact
    val_exact = 1.50000000
    assert round_order_size(meta, val_exact) == 1.5
