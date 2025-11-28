# tests/test_portfolio_store.py

import pytest
import os
from datetime import datetime
from kraken_bot.portfolio.store import SQLitePortfolioStore
from kraken_bot.portfolio.models import CashFlowRecord, PortfolioSnapshot, AssetValuation
from kraken_bot.strategy.models import DecisionRecord, ExecutionPlan, RiskAdjustedAction

@pytest.fixture
def store(tmp_path):
    db_path = tmp_path / "test_portfolio.db"
    return SQLitePortfolioStore(str(db_path))

def test_save_and_get_trades(store):
    trades = [
        {"id": "T1", "pair": "XBTUSD", "time": 1000, "price": 50000, "vol": 1, "cost": 50000, "type": "buy"},
        {"id": "T2", "pair": "XBTUSD", "time": 1001, "price": 51000, "vol": 0.5, "cost": 25500, "type": "sell"}
    ]
    store.save_trades(trades)

    fetched = store.get_trades()
    assert len(fetched) == 2
    assert fetched[0]['id'] == "T2" # Descending order
    assert fetched[1]['id'] == "T1"

    # Test filtering
    fetched_since = store.get_trades(since=1001)
    assert len(fetched_since) == 1
    assert fetched_since[0]['id'] == "T2"

def test_save_trades_with_list_field(store):
    # Regression test for 'InterfaceError' when 'trades' is a list
    trade_with_list = {
        "id": "T3", "pair": "XBTUSD", "time": 1002, "type": "buy",
        "price": 50000, "vol": 1, "cost": 50000,
        "trades": ["TX1", "TX2"]
    }
    store.save_trades([trade_with_list])

    fetched = store.get_trades(since=1002)
    assert len(fetched) == 1
    assert fetched[0]['id'] == "T3"
    # Verify raw_json preserved the list
    assert fetched[0]['trades'] == ["TX1", "TX2"]

def test_save_and_get_cash_flows(store):
    flows = [
        CashFlowRecord("C1", 1000, "USD", 1000.0, "deposit", "Initial"),
        CashFlowRecord("C2", 1002, "USD", -50.0, "withdrawal", "Test")
    ]
    store.save_cash_flows(flows)

    fetched = store.get_cash_flows()
    assert len(fetched) == 2
    assert fetched[0].id == "C2" # Descending

    fetched_since = store.get_cash_flows(since=1001)
    assert len(fetched_since) == 1
    assert fetched_since[0].id == "C2"

def test_save_and_get_snapshots(store):
    s1 = PortfolioSnapshot(
        timestamp=1000,
        equity_base=10000.0,
        cash_base=5000.0,
        asset_valuations=[AssetValuation("XBT", 0.1, 5000.0, "XBTUSD")],
        realized_pnl_base_total=100.0,
        unrealized_pnl_base_total=200.0,
        realized_pnl_base_by_pair={"XBTUSD": 100.0},
        unrealized_pnl_base_by_pair={"XBTUSD": 200.0}
    )
    store.save_snapshot(s1)

    snapshots = store.get_snapshots()
    assert len(snapshots) == 1
    assert snapshots[0].equity_base == 10000.0
    assert snapshots[0].asset_valuations[0].asset == "XBT"

    # Test update
    s2 = PortfolioSnapshot(
        timestamp=1000, # Same timestamp
        equity_base=11000.0, # Updated value
        cash_base=5000.0,
        asset_valuations=[],
        realized_pnl_base_total=0,
        unrealized_pnl_base_total=0,
        realized_pnl_base_by_pair={},
        unrealized_pnl_base_by_pair={}
    )
    store.save_snapshot(s2)
    snapshots = store.get_snapshots()
    assert len(snapshots) == 1
    assert snapshots[0].equity_base == 11000.0

def test_prune_snapshots(store):
    store.save_snapshot(PortfolioSnapshot(100, 0, 0, [], 0, 0, {}, {}))
    store.save_snapshot(PortfolioSnapshot(200, 0, 0, [], 0, 0, {}, {}))

    store.prune_snapshots(150) # Remove older than 150 (i.e. 100)

    snapshots = store.get_snapshots()
    assert len(snapshots) == 1
    assert snapshots[0].timestamp == 200


def test_get_trades_with_until_and_ordering(store):
    trades = [
        {"id": "T1", "pair": "XBTUSD", "time": 1000, "price": 50000, "vol": 1, "cost": 50000, "type": "buy"},
        {"id": "T2", "pair": "XBTUSD", "time": 1005, "price": 52000, "vol": 1, "cost": 52000, "type": "buy"},
        {"id": "T3", "pair": "ETHUSD", "time": 1010, "price": 3000, "vol": 2, "cost": 6000, "type": "sell"},
    ]
    store.save_trades(trades)

    limited = store.get_trades(until=1005, ascending=True)
    assert [t["id"] for t in limited] == ["T1", "T2"]

    eth_only = store.get_trades(pair="ETHUSD")
    assert len(eth_only) == 1
    assert eth_only[0]["id"] == "T3"


def test_get_cash_flows_with_until_and_ordering(store):
    flows = [
        CashFlowRecord("C1", 1000, "USD", 1000.0, "deposit", "Initial"),
        CashFlowRecord("C2", 1010, "USD", -50.0, "withdrawal", "Test"),
        CashFlowRecord("C3", 1020, "ETH", 1.0, "deposit", None),
    ]
    store.save_cash_flows(flows)

    usd_flows = store.get_cash_flows(asset="USD", ascending=True)
    assert [f.id for f in usd_flows] == ["C1", "C2"]

    bounded = store.get_cash_flows(until=1015)
    assert len(bounded) == 2
    assert bounded[0].id == "C2"


def test_decision_and_execution_plan_retrieval(store):
    decision = DecisionRecord(
        time=1700000000,
        plan_id="PLAN-1",
        strategy_name="trend",
        pair="XBTUSD",
        action_type="open",
        target_position_usd=1000.0,
        blocked=False,
        block_reason=None,
        kill_switch_active=False,
        raw_json="{}",
    )
    store.add_decision(decision)

    decisions = store.get_decisions(plan_id="PLAN-1")
    assert len(decisions) == 1
    assert decisions[0].strategy_name == "trend"

    plan = ExecutionPlan(
        plan_id="PLAN-1",
        generated_at=datetime.utcfromtimestamp(1700000000),
        actions=[
            RiskAdjustedAction(
                pair="XBTUSD",
                strategy_id="trend",
                action_type="open",
                target_base_size=0.01,
                target_notional_usd=1000.0,
                current_base_size=0.0,
                reason="entry",
                blocked=False,
                blocked_reasons=[],
                risk_limits_snapshot={},
            )
        ],
        metadata={"note": "test"},
    )

    store.save_execution_plan(plan)

    fetched = store.get_execution_plan("PLAN-1")
    assert fetched is not None
    assert fetched.plan_id == "PLAN-1"
    assert fetched.actions[0].pair == "XBTUSD"
    assert fetched.metadata["note"] == "test"

    recent = store.get_execution_plans(since=1699999999, limit=1)
    assert len(recent) == 1
