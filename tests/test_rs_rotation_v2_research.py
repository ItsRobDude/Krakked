from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from krakked.backtest.rs_rotation_v2_research import (
    RSRotationV2ResearchParams,
    evaluate_rs_rotation_v2_bars,
)
from krakked.market_data.models import OHLCBar


def _bars_from_prices(prices: list[float]) -> list[OHLCBar]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    bars: list[OHLCBar] = []
    previous = prices[0]
    for index, close in enumerate(prices):
        ts = int((start + timedelta(hours=4 * index)).timestamp())
        bars.append(
            OHLCBar(
                timestamp=ts,
                open=previous,
                high=max(previous, close),
                low=min(previous, close),
                close=close,
                volume=1_000.0,
            )
        )
        previous = close
    return bars


def _trend(start_price: float, pct_per_bar: float, count: int = 60) -> list[float]:
    prices = [start_price]
    for _ in range(count - 1):
        prices.append(prices[-1] * (1.0 + pct_per_bar))
    return prices


def _window() -> tuple[datetime, datetime]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = start + timedelta(hours=4 * 59)
    return start, end


def test_v2_stays_cash_when_momentum_does_not_clear_cost_hurdle() -> None:
    start, end = _window()
    params = RSRotationV2ResearchParams(
        lookback_bars=10,
        volatility_lookback_bars=10,
        rebalance_interval_bars=5,
        top_n=1,
        fee_bps=25.0,
        slippage_bps=50.0,
        edge_buffer_bps=50.0,
    )

    result = evaluate_rs_rotation_v2_bars(
        {
            "BTC/USD": _bars_from_prices(_trend(100.0, 0.0001)),
            "ETH/USD": _bars_from_prices(_trend(50.0, 0.0001)),
            "SOL/USD": _bars_from_prices(_trend(20.0, 0.0001)),
        },
        start=start,
        end=end,
        params=params,
    )

    summary = result.summary
    assert summary["trade_count"] == 0
    assert summary["active_cycles"] == 0
    assert summary["cash_cycles"] == summary["total_cycles"]
    assert summary["return_pct"] == pytest.approx(0.0)
    assert summary["status"] == "research_fail"
    assert "positive_return_after_costs" in summary["gate_failures"]


def test_v2_selects_strong_absolute_momentum_after_costs() -> None:
    start, end = _window()
    params = RSRotationV2ResearchParams(
        lookback_bars=10,
        volatility_lookback_bars=10,
        rebalance_interval_bars=5,
        top_n=1,
        fee_bps=0.0,
        slippage_bps=0.0,
        edge_buffer_bps=0.0,
    )

    result = evaluate_rs_rotation_v2_bars(
        {
            "BTC/USD": _bars_from_prices(_trend(100.0, 0.001)),
            "ETH/USD": _bars_from_prices(_trend(50.0, 0.003)),
            "SOL/USD": _bars_from_prices(_trend(20.0, 0.0005)),
        },
        start=start,
        end=end,
        params=params,
    )

    summary = result.summary
    assert summary["active_cycles"] > 0
    assert summary["trade_count"] > 0
    assert summary["selection_counts"]["ETH/USD"] > 0
    assert summary["return_pct"] > 0.0


def test_v2_regime_gate_blocks_positive_alt_in_btc_downtrend() -> None:
    start, end = _window()
    params = RSRotationV2ResearchParams(
        lookback_bars=10,
        volatility_lookback_bars=10,
        rebalance_interval_bars=5,
        top_n=1,
        fee_bps=0.0,
        slippage_bps=0.0,
        edge_buffer_bps=0.0,
        require_btc_regime=True,
        require_basket_regime=False,
    )

    result = evaluate_rs_rotation_v2_bars(
        {
            "BTC/USD": _bars_from_prices(_trend(100.0, -0.001)),
            "ETH/USD": _bars_from_prices(_trend(50.0, 0.004)),
            "SOL/USD": _bars_from_prices(_trend(20.0, 0.003)),
        },
        start=start,
        end=end,
        params=params,
    )

    assert result.summary["trade_count"] == 0
    assert result.summary["active_cycles"] == 0
    assert all(not cycle["btc_regime_ok"] for cycle in result.cycles or [])


def test_v2_hysteresis_keeps_existing_holding_without_large_score_gap() -> None:
    start, end = _window()
    eth_prices = _trend(50.0, 0.006, count=30) + _trend(59.8, 0.002, count=30)
    sol_prices = _trend(20.0, 0.002, count=30) + _trend(21.23, 0.006, count=30)
    params = RSRotationV2ResearchParams(
        lookback_bars=5,
        volatility_lookback_bars=5,
        rebalance_interval_bars=1,
        top_n=1,
        fee_bps=0.0,
        slippage_bps=0.0,
        edge_buffer_bps=0.0,
        min_score_gap=1e20,
        require_btc_regime=False,
        require_basket_regime=False,
    )

    result = evaluate_rs_rotation_v2_bars(
        {
            "ETH/USD": _bars_from_prices(eth_prices),
            "SOL/USD": _bars_from_prices(sol_prices),
        },
        start=start,
        end=end,
        params=params,
    )

    active_cycles = [
        cycle for cycle in result.cycles or [] if cycle.get("selected_pairs")
    ]
    assert active_cycles
    assert active_cycles[-1]["selected_pairs"] == ["ETH/USD"]
    assert result.summary["selection_counts"]["ETH/USD"] > 0
