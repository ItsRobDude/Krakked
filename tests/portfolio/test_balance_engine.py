
import pytest
from decimal import Decimal
from kraken_bot.portfolio.balance_engine import BalanceEngine
from kraken_bot.portfolio.models import LedgerEntry, AssetBalance

def test_balance_engine_quantization():
    """Test that balances are quantized to 1e-8 to prevent float drift."""
    engine = BalanceEngine()

    # 1. Apply entry that results in a drifting float if not quantized
    # 0.1 + 0.2 = 0.30000000000000004 in float

    # Initialize with 0.1
    entry1 = LedgerEntry(
        id="1", refid="r1", time=100, type="deposit",
        asset="XBT", amount=Decimal("0.1"), fee=Decimal("0.0"), balance=None,
        subtype="", aclass="currency", misc="", raw={}
    )
    engine.apply_entry(entry1)

    # Add 0.2
    entry2 = LedgerEntry(
        id="2", refid="r2", time=101, type="deposit",
        asset="XBT", amount=Decimal("0.2"), fee=Decimal("0.0"), balance=None,
        subtype="", aclass="currency", misc="", raw={}
    )
    engine.apply_entry(entry2)

    # Check total
    balance = engine.balances["XBT"]
    # Should be exactly 0.3
    assert balance.total == 0.3
    # Float representation should be clean
    assert str(balance.total) == "0.3"

def test_balance_engine_negative_zero_clamp():
    """Test that tiny negative residuals are clamped to zero."""
    engine = BalanceEngine()

    # Initialize with 1.0
    engine.apply_entry(LedgerEntry(
        id="1", refid="r1", time=100, type="deposit",
        asset="XBT", amount=Decimal("1.0"), fee=Decimal("0.0"), balance=None,
        subtype="", aclass="currency", misc="", raw={}
    ))

    # Remove 1.0 + tiny bit due to potential drift source (simulated)
    # Actually, Decimal handles this well, but let's simulate a case where a very small negative remains
    # e.g. selling 1.000000001 when you have 1.0

    # Apply a withdrawal that leaves -1e-9
    engine.apply_entry(LedgerEntry(
        id="2", refid="r2", time=101, type="withdrawal",
        asset="XBT", amount=Decimal("-1.000000001"), fee=Decimal("0.0"), balance=None,
        subtype="", aclass="currency", misc="", raw={}
    ))

    balance = engine.balances["XBT"]
    # Should be clamped to 0.0 because -1e-9 is smaller than 1e-8 quantum
    assert balance.total == 0.0
    assert balance.free == 0.0

def test_balance_engine_quantization_accumulated_drift():
    """Test repeated small operations don't drift."""
    engine = BalanceEngine()

    # Add 0.00000001 (1 sat) 10 times
    for i in range(10):
        engine.apply_entry(LedgerEntry(
            id=str(i), refid="r", time=100+i, type="deposit",
            asset="XBT", amount=Decimal("0.00000001"), fee=Decimal("0.0"), balance=None,
            subtype="", aclass="currency", misc="", raw={}
        ))

    balance = engine.balances["XBT"]
    assert balance.total == 0.00000010
