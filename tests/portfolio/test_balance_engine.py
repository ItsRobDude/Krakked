from decimal import Decimal

from kraken_bot.portfolio.balance_engine import (
    BalanceEngine,
    classify_cashflow,
    rebuild_balances,
)
from kraken_bot.portfolio.models import (
    CashFlowCategory,
    LedgerEntry,
)


def test_balance_engine_apply_entry():
    # Setup
    engine = BalanceEngine()

    # Entry 1: Deposit 1.0 BTC
    e1 = LedgerEntry(
        id="L1",
        time=1000,
        type="deposit",
        subtype="",
        aclass="currency",
        asset="XXBT",
        amount=Decimal("1.0"),
        fee=Decimal("0.0"),
        balance=None,
        refid=None,
        misc=None,
        raw={},
    )
    engine.apply_entry(e1)
    assert engine.balances["XXBT"].total == 1.0

    # Entry 2: Trade -0.1 BTC, Fee 0.0001 BTC
    e2 = LedgerEntry(
        id="L2",
        time=1001,
        type="trade",
        subtype="",
        aclass="currency",
        asset="XXBT",
        amount=Decimal("-0.1"),
        fee=Decimal("0.0001"),
        balance=None,
        refid=None,
        misc=None,
        raw={},
    )
    engine.apply_entry(e2)
    # 1.0 - 0.1 - 0.0001 = 0.8999
    assert abs(engine.balances["XXBT"].total - 0.8999) < 1e-9

    # Entry 3: Invariant check with balance provided by Kraken
    # Suppose we drifted or calculation was slightly off, but Kraken says 0.9
    e3 = LedgerEntry(
        id="L3",
        time=1002,
        type="adjustment",
        subtype="",
        aclass="currency",
        asset="XXBT",
        amount=Decimal("0.0"),
        fee=Decimal("0.0"),
        balance=Decimal("0.9"),
        refid=None,
        misc=None,
        raw={},
    )
    engine.apply_entry(e3)
    assert engine.balances["XXBT"].total == 0.9


def test_rebuild_balances():
    entries = [
        LedgerEntry(
            id="1",
            time=100,
            type="deposit",
            subtype="",
            aclass="",
            asset="USD",
            amount=Decimal("100"),
            fee=Decimal("0"),
            balance=None,
            refid=None,
            misc=None,
            raw={},
        ),
        LedgerEntry(
            id="2",
            time=101,
            type="withdrawal",
            subtype="",
            aclass="",
            asset="USD",
            amount=Decimal("-50"),
            fee=Decimal("1"),
            balance=None,
            refid=None,
            misc=None,
            raw={},
        ),
    ]

    balances = rebuild_balances(entries)
    assert balances["USD"].total == 49.0  # 100 - 50 - 1


def test_classify_cashflow():
    # Deposit
    e1 = LedgerEntry(
        id="1",
        time=100,
        type="deposit",
        subtype="",
        aclass="",
        asset="USD",
        amount=Decimal("100"),
        fee=Decimal("0"),
        balance=None,
        refid=None,
        misc=None,
        raw={},
    )
    cf1 = classify_cashflow(e1)
    assert cf1.type == CashFlowCategory.DEPOSIT.value
    assert cf1.amount == 100.0

    # Trade (PnL)
    e2 = LedgerEntry(
        id="2",
        time=101,
        type="trade",
        subtype="",
        aclass="",
        asset="USD",
        amount=Decimal("5"),
        fee=Decimal("0"),
        balance=None,
        refid=None,
        misc=None,
        raw={},
    )
    cf2 = classify_cashflow(e2)
    assert cf2.type == CashFlowCategory.TRADE_PNL.value

    # Internal / None
    e3 = LedgerEntry(
        id="3",
        time=102,
        type="transfer",
        subtype="internal",
        aclass="",
        asset="USD",
        amount=Decimal("0"),
        fee=Decimal("0"),
        balance=None,
        refid=None,
        misc=None,
        raw={},
    )
    cf3 = classify_cashflow(e3)
    assert cf3 is None
