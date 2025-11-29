import pytest

from kraken_bot.config import ExecutionConfig
from kraken_bot.execution.models import LocalOrder
from kraken_bot.execution.router import build_order_payload


def _order(**overrides) -> LocalOrder:
    base = dict(
        local_id="1",
        plan_id=None,
        strategy_id=None,
        pair="XBTUSD",
        side="buy",
        order_type="limit",
        userref=1,
        requested_base_size=0.5,
        requested_price=100.0,
    )
    base.update(overrides)
    return LocalOrder(**base)


def test_build_order_payload_sets_validate_and_userref():
    config = ExecutionConfig(mode="paper", validate_only=True)
    order = _order(userref=42)

    payload = build_order_payload(order, config)

    assert payload["validate"] == 1
    assert payload["userref"] == 42


def test_build_order_payload_applies_slippage_and_rounding():
    config = ExecutionConfig(max_slippage_bps=100, validate_only=False)
    pair_meta = {"price_decimals": 2, "volume_decimals": 3}
    order = _order(requested_base_size=0.12345, requested_price=10.0)

    payload = build_order_payload(order, config, pair_metadata=pair_meta)

    assert payload["volume"] == pytest.approx(0.123)
    assert payload["price"] == pytest.approx(10.1)


def test_build_order_payload_excludes_price_for_market_orders():
    config = ExecutionConfig(validate_only=False)
    order = _order(order_type="market", requested_price=55.0)

    payload = build_order_payload(order, config)

    assert "price" not in payload
