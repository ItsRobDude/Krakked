# tests/test_portfolio_store.py

import os
from datetime import datetime

import pytest
from kraken_bot.execution.models import ExecutionResult, LocalOrder
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
                strategy_tag="trend",
                userref=123,
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
    assert fetched.actions[0].userref == 123
    assert fetched.actions[0].strategy_tag == "trend"
    assert fetched.metadata["note"] == "test"

    recent = store.get_execution_plans(since=1699999999, limit=1)
    assert len(recent) == 1


def test_save_and_load_local_order(store):
    created_at = datetime(2024, 1, 1, 12, 0, 0)
    order = LocalOrder(
        local_id="LOCAL-1",
        plan_id="PLAN-LOCAL",
        strategy_id="strat-1",
        pair="XBTUSD",
        side="buy",
        order_type="limit",
        kraken_order_id="KRAKEN-1",
        userref=42,
        requested_base_size=0.25,
        requested_price=25000.5,
        status="submitted",
        created_at=created_at,
        updated_at=created_at,
        cumulative_base_filled=0.05,
        avg_fill_price=24950.1,
        last_error="temporary glitch",
        raw_request={"price": "25000.5", "volume": "0.25", "validate": True},
        raw_response={"descr": {"order": "buy 0.25 XBTUSD @ limit 25000.5"}, "txid": ["KRAKEN-1"]},
    )

    store.save_order(order)

    open_orders = store.get_open_orders()
    assert len(open_orders) == 1

    loaded = open_orders[0]
    assert loaded.status == "submitted"
    assert loaded.requested_price == 25000.5
    assert loaded.avg_fill_price == 24950.1
    assert loaded.raw_request == {"price": "25000.5", "volume": "0.25", "validate": True}
    assert loaded.raw_response == {"descr": {"order": "buy 0.25 XBTUSD @ limit 25000.5"}, "txid": ["KRAKEN-1"]}
    assert loaded.last_error == "temporary glitch"

    by_reference = store.get_order_by_reference(kraken_order_id="KRAKEN-1")
    assert by_reference is not None
    assert by_reference.userref == 42
    assert by_reference.raw_request == loaded.raw_request
    assert by_reference.raw_response == loaded.raw_response


def test_save_and_load_execution_result(store):
    started_at = datetime(2024, 1, 2, 15, 30, 0)
    completed_at = datetime(2024, 1, 2, 15, 45, 0)
    result = ExecutionResult(
        plan_id="PLAN-RESULT",
        started_at=started_at,
        completed_at=completed_at,
        success=False,
        orders=[],
        errors=["order failed", "insufficient funds"],
        warnings=["retry later"],
    )

    store.save_execution_result(result)

    loaded_results = store.get_execution_results(limit=1)
    assert len(loaded_results) == 1

    loaded = loaded_results[0]
    assert loaded.plan_id == "PLAN-RESULT"
    assert loaded.success is False
    assert loaded.started_at == started_at
    assert loaded.completed_at == completed_at
    assert loaded.errors == ["order failed", "insufficient funds"]
