
from decimal import Decimal
from kraken_bot.portfolio.balance_engine import BalanceEngine
from kraken_bot.portfolio.models import LedgerEntry, AssetBalance

def test_balance_engine_cache_hit_and_miss():
    """Verify that the cache is used and correctly invalidated."""
    engine = BalanceEngine()
    asset = "XBT"

    # 1. First call: Cache miss, populate cache
    entry1 = LedgerEntry(
        id="1", time=100, type="deposit", asset=asset, amount=Decimal("1.0"),
        fee=Decimal("0"), balance=None, subtype="", aclass="", misc="", raw={}, refid=None
    )
    engine.apply_entry(entry1)

    # Verify cache is populated
    assert asset in engine._decimal_cache
    # Cache content: (float_total, dec_total, float_reserved, dec_reserved)
    # total should be 1.0
    cached = engine._decimal_cache[asset]
    assert cached[0] == 1.0
    assert cached[1] == Decimal("1.0")

    # 2. Second call: Cache hit (implicit, logic verification)
    # We can't easily spy on internal logic without mocking `Decimal`,
    # but we can verify that the result is correct, which implies correct cache usage.
    entry2 = LedgerEntry(
        id="2", time=101, type="trade", asset=asset, amount=Decimal("0.5"),
        fee=Decimal("0"), balance=None, subtype="", aclass="", misc="", raw={}, refid=None
    )
    engine.apply_entry(entry2)

    assert engine.balances[asset].total == 1.5
    cached = engine._decimal_cache[asset]
    assert cached[0] == 1.5
    assert cached[1] == Decimal("1.5")

    # 3. External modification (Invalidation)
    # Force modify the float balance externally to simulate drift or other update
    engine.balances[asset].total = 10.0
    # Cache still has 1.5. If we used cache blindly, we'd start from 1.5.
    # The new entry adds 1.0. Correct result should be 11.0.
    # If cache bug: 1.5 + 1.0 = 2.5.

    entry3 = LedgerEntry(
        id="3", time=102, type="deposit", asset=asset, amount=Decimal("1.0"),
        fee=Decimal("0"), balance=None, subtype="", aclass="", misc="", raw={}, refid=None
    )
    engine.apply_entry(entry3)

    # Verify we respected the external change
    assert engine.balances[asset].total == 11.0
    # And cache should be updated to new state
    cached = engine._decimal_cache[asset]
    assert cached[0] == 11.0

def test_balance_engine_cache_bound():
    """Verify that cache doesn't grow indefinitely."""
    engine = BalanceEngine()

    # Fill with 1001 entries
    for i in range(1005):
        asset_name = f"A{i}"
        entry = LedgerEntry(
            id=str(i), time=100, type="deposit", asset=asset_name, amount=Decimal("1.0"),
            fee=Decimal("0"), balance=None, subtype="", aclass="", misc="", raw={}, refid=None
        )
        engine.apply_entry(entry)

    # The cache should have been cleared at least once when > 1000
    # Current size should be small (5) or depending on when it cleared.
    # Logic: if len > 1000: clear().
    # It clears BEFORE adding the new one.
    # i=1000: len=1000. adds A1000 -> len=1001.
    # i=1001: len=1001 > 1000 -> clear() -> len=0. adds A1001 -> len=1.
    # So we expect size to be small.
    assert len(engine._decimal_cache) < 1000
