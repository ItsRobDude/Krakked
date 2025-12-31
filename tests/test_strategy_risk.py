# tests/test_strategy_risk.py

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pandas as pd

from kraken_bot.config import PortfolioConfig, RiskConfig
from kraken_bot.market_data.api import MarketDataAPI
from kraken_bot.market_data.models import PairMetadata
from kraken_bot.portfolio.manager import PortfolioService
from kraken_bot.portfolio.models import (
    AssetExposure,
    DriftMismatchedAsset,
    DriftStatus,
    EquityView,
    RealizedPnLRecord,
    SpotPosition,
)
from kraken_bot.portfolio.portfolio import Portfolio
from kraken_bot.strategy.models import StrategyIntent
from kraken_bot.strategy.risk import RiskEngine, compute_atr


def test_compute_atr():
    data = {
        "high": [10, 11, 12, 11, 13],
        "low": [9, 10, 11, 10, 11],
        "close": [10, 10.5, 11.5, 10.5, 12.5],
    }
    df = pd.DataFrame(data)
    atr = compute_atr(df, window=3)
    assert abs(atr - 1.833) < 0.01


def test_risk_engine_sizing():
    config = RiskConfig(max_risk_per_trade_pct=1.0, volatility_lookback_bars=3)
    market = MagicMock(spec=MarketDataAPI)
    portfolio = MagicMock(spec=PortfolioService)

    market.get_latest_price.return_value = 100.0

    real_meta = PairMetadata(
        canonical="XBTUSD", base="XBT", quote="USD", rest_symbol="XXBTZUSD", ws_symbol="XBT/USD",
        raw_name="XXBTZUSD", price_decimals=1, volume_decimals=4, lot_size=1, min_order_size=0.0001,
        status="online", liquidity_24h_usd=1_000_000.0
    )
    market.get_pair_metadata.return_value = real_meta

    from dataclasses import dataclass
    @dataclass
    class MockBar:
        high: float
        low: float
        close: float

    market.get_ohlc.return_value = [MockBar(105, 95, 100) for _ in range(15)]

    portfolio.get_equity.return_value = EquityView(
        equity_base=10000.0,
        cash_base=10000.0,
        realized_pnl_base_total=0,
        unrealized_pnl_base_total=0,
        drift_flag=False,
    )
    portfolio.get_positions.return_value = []
    portfolio.get_asset_exposure.return_value = []
    portfolio.store = MagicMock()
    portfolio.store.get_snapshots.return_value = []

    engine = RiskEngine(
        config,
        market,
        portfolio,
        strategy_userrefs={"test": 42},
        strategy_tags={"test": "trend_v1"},
    )

    intent = StrategyIntent(
        strategy_id="test",
        pair="XBTUSD",
        side="long",
        intent_type="enter",
        desired_exposure_usd=None,
        confidence=1.0,
        timeframe="1h",
        generated_at=datetime.now(timezone.utc),
    )

    actions = engine.process_intents([intent])

    assert len(actions) == 1
    action = actions[0]
    assert 490 < action.target_notional_usd < 510
    assert not action.blocked
    assert action.userref == "42"
    assert action.strategy_tag == "trend_v1"


def test_kill_switch_drawdown():
    config = RiskConfig(max_daily_drawdown_pct=5.0)
    market = MagicMock(spec=MarketDataAPI)
    portfolio = MagicMock(spec=PortfolioService)

    real_meta = PairMetadata(
        canonical="XBTUSD", base="XBT", quote="USD", rest_symbol="XXBTZUSD", ws_symbol="XBT/USD",
        raw_name="XXBTZUSD", price_decimals=1, volume_decimals=4, lot_size=1, min_order_size=0.0001,
        status="online", liquidity_24h_usd=1_000_000.0
    )
    market.get_pair_metadata.return_value = real_meta

    portfolio.get_equity.return_value = EquityView(
        equity_base=9000.0,
        cash_base=9000.0,
        realized_pnl_base_total=0,
        unrealized_pnl_base_total=0,
        drift_flag=False,
    )
    portfolio.store = MagicMock()
    snap = MagicMock()
    snap.equity_base = 10000.0
    portfolio.store.get_snapshots.return_value = [snap]
    portfolio.get_positions.return_value = []
    portfolio.get_asset_exposure.return_value = []

    engine = RiskEngine(config, market, portfolio)

    intent = StrategyIntent(
        "test", "XBTUSD", "long", "enter", 1000.0, 1.0, "1h", datetime.now(timezone.utc)
    )
    actions = engine.process_intents([intent])

    assert actions[0].blocked
    assert "Kill Switch" in str(actions[0].reason)
    assert engine.get_status().kill_switch_active


def test_manual_kill_switch_blocks_opens_allows_reductions():
    config = RiskConfig()
    market = MagicMock(spec=MarketDataAPI)
    portfolio = MagicMock(spec=PortfolioService)

    market.get_latest_price.return_value = 100.0

    real_meta = PairMetadata(
        canonical="XBTUSD", base="XBT", quote="USD", rest_symbol="XXBTZUSD", ws_symbol="XBT/USD",
        raw_name="XXBTZUSD", price_decimals=1, volume_decimals=4, lot_size=1, min_order_size=0.0001,
        status="online"
    )
    market.get_pair_metadata.return_value = real_meta

    portfolio.get_equity.return_value = EquityView(
        equity_base=10000.0,
        cash_base=10000.0,
        realized_pnl_base_total=0,
        unrealized_pnl_base_total=0,
        drift_flag=False,
    )

    pos = SpotPosition(
        pair="XBTUSD", base_asset="XBT", quote_asset="USD", base_size=1.0,
        avg_entry_price=100.0, realized_pnl_base=0, fees_paid_base=0, strategy_tag="test",
        current_value_base=100.0
    )
    portfolio.get_positions.return_value = [pos]

    portfolio.get_asset_exposure.return_value = []
    portfolio.store = MagicMock()
    portfolio.store.get_snapshots.return_value = []

    engine = RiskEngine(config, market, portfolio)
    engine.set_manual_kill_switch(True)

    intents = [
        StrategyIntent("test", "XBTUSD", "long", "enter", 1000.0, 1.0, "1h", datetime.now(timezone.utc)),
        StrategyIntent("test", "XBTUSD", "flat", "exit", None, 1.0, "1h", datetime.now(timezone.utc)),
    ]

    actions = engine.process_intents(intents)

    # 1. Enter Blocked
    assert actions[0].blocked
    assert actions[0].action_type == "none"
    assert "Manual Kill Switch" in actions[0].reason

    # 2. Exit Allowed (size 1.0 > min 0.0001)
    assert not actions[1].blocked
    assert actions[1].action_type == "close"
    assert "Allowed close/reduce" in actions[1].reason


# --- Restored Tests ---

def test_kill_switch_reasons_are_additive():
    config = RiskConfig(kill_switch_on_drift=True)
    market = MagicMock(spec=MarketDataAPI)
    portfolio = MagicMock(spec=PortfolioService)

    market.get_latest_price.return_value = 100.0
    real_meta = PairMetadata(
        canonical="XBTUSD", base="XBT", quote="USD", rest_symbol="XXBTZUSD", ws_symbol="XBT/USD",
        raw_name="XXBTZUSD", price_decimals=1, volume_decimals=4, lot_size=1, min_order_size=0.0001,
        status="online"
    )
    market.get_pair_metadata.return_value = real_meta

    portfolio.get_equity.return_value = EquityView(
        equity_base=10000.0,
        cash_base=10000.0,
        realized_pnl_base_total=0,
        unrealized_pnl_base_total=0,
        drift_flag=True,
    )
    portfolio.get_positions.return_value = []
    portfolio.get_asset_exposure.return_value = []
    portfolio.store = MagicMock()
    portfolio.store.get_snapshots.return_value = []

    engine = RiskEngine(config, market, portfolio)
    engine.set_manual_kill_switch(True)

    intent = StrategyIntent(
        "test", "XBTUSD", "long", "enter", 1000.0, 1.0, "1h", datetime.now(timezone.utc)
    )
    action = engine.process_intents([intent])[0]

    assert action.blocked
    assert "Manual Kill Switch" in action.reason
    assert "Portfolio Drift Detected" in action.reason
    assert engine.get_status().kill_switch_active


def test_drift_kill_switch_blocks_opening_orders():
    config = RiskConfig(kill_switch_on_drift=True)
    market = MagicMock(spec=MarketDataAPI)
    portfolio = MagicMock(spec=PortfolioService)

    market.get_latest_price.return_value = 100.0
    real_meta = PairMetadata(
        canonical="XBTUSD", base="XBT", quote="USD", rest_symbol="XXBTZUSD", ws_symbol="XBT/USD",
        raw_name="XXBTZUSD", price_decimals=1, volume_decimals=4, lot_size=1, min_order_size=0.0001,
        status="online"
    )
    market.get_pair_metadata.return_value = real_meta

    portfolio.get_equity.return_value = EquityView(
        equity_base=10000.0,
        cash_base=10000.0,
        realized_pnl_base_total=0.0,
        unrealized_pnl_base_total=0.0,
        drift_flag=False,
    )
    portfolio.get_positions.return_value = []
    portfolio.get_asset_exposure.return_value = []
    portfolio.get_realized_pnl_by_strategy.return_value = {}

    # Simulate drift
    portfolio.get_drift_status.return_value = DriftStatus(
        drift_flag=True,
        expected_position_value_base=100.0,
        actual_balance_value_base=50.0,
        tolerance_base=1.0,
        mismatched_assets=[
            DriftMismatchedAsset(
                asset="XBT",
                expected_quantity=1.0,
                actual_quantity=0.5,
                difference_base=50.0,
            )
        ],
    )
    portfolio.store = MagicMock()
    portfolio.store.get_snapshots.return_value = []

    engine = RiskEngine(config, market, portfolio)

    intents = [
        StrategyIntent(
            "trend_core", "XBTUSD", "long", "enter", 1000.0, 1.0, "1h", datetime.now(timezone.utc),
        ),
        StrategyIntent(
            "trend_core", "XBTUSD", "long", "reduce", 0.0, 1.0, "1h", datetime.now(timezone.utc),
        ),
    ]

    actions = engine.process_intents(intents)

    assert actions[0].blocked
    assert "Portfolio Drift Detected" in actions[0].reason
    assert not actions[1].blocked
    assert engine.get_status().drift_flag


def test_max_per_asset():
    config = RiskConfig(max_per_asset_pct=10.0)  # 10% max
    market = MagicMock(spec=MarketDataAPI)
    portfolio = MagicMock(spec=PortfolioService)
    market.get_latest_price.return_value = 100.0
    real_meta = PairMetadata(
        canonical="XBTUSD", base="XBT", quote="USD", rest_symbol="XXBTZUSD", ws_symbol="XBT/USD",
        raw_name="XXBTZUSD", price_decimals=1, volume_decimals=4, lot_size=1, min_order_size=0.0001,
        status="online"
    )
    market.get_pair_metadata.return_value = real_meta

    portfolio.get_equity.return_value = EquityView(
        equity_base=10000.0,
        cash_base=10000.0,
        realized_pnl_base_total=0,
        unrealized_pnl_base_total=0,
        drift_flag=False,
    )
    portfolio.get_positions.return_value = []
    portfolio.get_asset_exposure.return_value = []
    portfolio.store = MagicMock()
    portfolio.store.get_snapshots.return_value = []

    engine = RiskEngine(config, market, portfolio)

    # Try to buy 2000 USD (20%)
    intent = StrategyIntent(
        "test", "XBTUSD", "long", "enter", 2000.0, 1.0, "1h", datetime.now(timezone.utc)
    )
    actions = engine.process_intents([intent])

    # Should be clamped to 1000 (10%)
    assert abs(actions[0].target_notional_usd - 1000.0) < 1.0
    assert "Max per asset limit" in actions[0].reason


def test_manual_vs_strategy_grouping_and_exposure():
    market = MagicMock(spec=MarketDataAPI)
    portfolio = MagicMock(spec=PortfolioService)

    market.get_latest_price.side_effect = lambda pair: {
        "XBTUSD": 100.0,
        "ETHUSD": 50.0,
    }[pair]

    mock_meta = MagicMock()
    mock_meta.liquidity_24h_usd = 1_000_000.0
    market.get_pair_metadata.return_value = mock_meta

    manual_position = SpotPosition(
        pair="XBTUSD",
        base_asset="XBT",
        quote_asset="USD",
        base_size=3.0,
        avg_entry_price=50.0,
        realized_pnl_base=0.0,
        fees_paid_base=0.0,
        strategy_tag=None,
        current_value_base=300.0,  # 3.0 * 100.0
    )
    strategy_position = SpotPosition(
        pair="ETHUSD",
        base_asset="ETH",
        quote_asset="USD",
        base_size=2.0,
        avg_entry_price=25.0,
        realized_pnl_base=0.0,
        fees_paid_base=0.0,
        strategy_tag="trend_core",
        current_value_base=100.0,  # 2.0 * 50.0
    )

    equity = EquityView(
        equity_base=1000.0,
        cash_base=500.0,
        realized_pnl_base_total=0.0,
        unrealized_pnl_base_total=0.0,
        drift_flag=False,
    )

    exposures_total = [
        AssetExposure(asset="XBT", amount=3.0, value_base=300.0, percentage_of_equity=0.3),
        AssetExposure(asset="ETH", amount=2.0, value_base=100.0, percentage_of_equity=0.1),
    ]
    exposures_strategy_only = [
        AssetExposure(asset="ETH", amount=2.0, value_base=100.0, percentage_of_equity=0.1)
    ]

    portfolio.get_equity.return_value = equity
    portfolio.get_positions.return_value = [manual_position, strategy_position]
    portfolio.get_asset_exposure.side_effect = lambda include_manual=True: (
        exposures_total if include_manual else exposures_strategy_only
    )
    portfolio.get_realized_pnl_by_strategy.side_effect = [
        {"manual": 50.0, "trend_core": 100.0},
        {"trend_core": 100.0},
    ]
    portfolio.get_drift_status.return_value = DriftStatus(
        drift_flag=False,
        expected_position_value_base=0.0,
        actual_balance_value_base=0.0,
        tolerance_base=1.0,
    )
    portfolio.store = MagicMock()
    portfolio.store.get_snapshots.return_value = []

    engine = RiskEngine(RiskConfig(), market, portfolio)
    ctx = engine.build_risk_context()

    assert abs(ctx.manual_exposure_pct - 30.0) < 1e-6
    assert abs(ctx.total_exposure_pct - 40.0) < 1e-6
    assert ctx.per_strategy_exposure_pct == {"trend_core": 10.0}
    assert len(ctx.manual_positions) == 1
    assert all(pos.strategy_tag != "manual" for pos in ctx.manual_positions)


def test_userref_falls_back_to_strategy_id_when_missing_mapping():
    config = RiskConfig()

    market = MagicMock(spec=MarketDataAPI)
    market.get_latest_price.return_value = 100.0
    market.get_pair_metadata.return_value = MagicMock(liquidity_24h_usd=1_000_000.0)

    portfolio = MagicMock(spec=PortfolioService)
    portfolio.get_equity.return_value = EquityView(
        equity_base=10000.0,
        cash_base=10000.0,
        realized_pnl_base_total=0,
        unrealized_pnl_base_total=0,
        drift_flag=False,
    )
    portfolio.get_positions.return_value = []
    portfolio.get_asset_exposure.return_value = []
    portfolio.get_realized_pnl_by_strategy.return_value = {}
    portfolio.store = MagicMock()
    portfolio.store.get_snapshots.return_value = []

    engine = RiskEngine(config, market, portfolio, strategy_tags={"alpha": "alpha"})

    intent = StrategyIntent(
        strategy_id="alpha",
        pair="XBTUSD",
        side="long",
        intent_type="enter",
        desired_exposure_usd=500.0,
        confidence=1.0,
        timeframe="1h",
        generated_at=datetime.now(timezone.utc),
    )

    action = engine.process_intents([intent])[0]

    assert action.userref.startswith("alpha")


# --- New Dust Tests ---

def test_dust_sell_is_noop():
    """Verify that a sell delta below min_order_size results in a NO-OP action."""
    # Ensure limits don't clamp
    config = RiskConfig(
        max_portfolio_risk_pct=100.0,
        max_per_asset_pct=100.0,
        max_open_positions=100,
    )
    market = MagicMock(spec=MarketDataAPI)
    portfolio = MagicMock(spec=PortfolioService)

    # Price and Metadata
    market.get_latest_price.return_value = 100.0

    # Use real PairMetadata to ensure logic works
    real_meta = PairMetadata(
        canonical="DOGEUSD", base="DOGE", quote="USD", rest_symbol="XDGUSD", ws_symbol="DOGE/USD",
        raw_name="XDGUSD", price_decimals=1, volume_decimals=1, lot_size=1, min_order_size=1.0,
        status="online"
    )
    market.get_pair_metadata.return_value = real_meta

    # Current Position: 10.5 DOGE
    pos = SpotPosition(
        pair="DOGEUSD", base_asset="DOGE", quote_asset="USD", base_size=10.5,
        avg_entry_price=100.0, realized_pnl_base=0, fees_paid_base=0, strategy_tag="test",
        current_value_base=1050.0
    )
    portfolio.get_positions.return_value = [pos]

    portfolio.get_equity.return_value = EquityView(10000.0, 10000.0, 0, 0, False)
    portfolio.get_asset_exposure.return_value = []
    portfolio.get_realized_pnl_by_strategy.return_value = {}
    portfolio.store = MagicMock()
    portfolio.store.get_snapshots.return_value = []

    engine = RiskEngine(config, market, portfolio)

    # Intent: Reduce to 10.0 DOGE (Sell 0.5)
    # 0.5 < min 1.0 => Dust sell
    target_usd = 10.0 * 100.0 # 1000 USD
    intent = StrategyIntent(
        "test", "DOGEUSD", "long", "reduce", target_usd, 1.0, "1h", datetime.now(timezone.utc)
    )

    actions = engine.process_intents([intent])
    action = actions[0]

    # Assertions
    assert action.action_type == "none"
    assert action.target_base_size == 10.5
    assert action.target_notional_usd == 1050.0
    assert action.blocked is False
    assert "Dust:" in action.reason
    assert "rounded sell volume 0.5" in action.reason


def test_untradeable_missing_metadata():
    """Verify that missing metadata forces NO-OP for sell."""
    config = RiskConfig()
    market = MagicMock(spec=MarketDataAPI)
    portfolio = MagicMock(spec=PortfolioService)

    market.get_latest_price.return_value = 100.0
    # Simulate missing metadata
    market.get_pair_metadata.side_effect = Exception("Not found")

    pos = SpotPosition(
        pair="UNK", base_asset="UNK", quote_asset="USD", base_size=10.0,
        avg_entry_price=10.0, realized_pnl_base=0, fees_paid_base=0, strategy_tag="test",
        current_value_base=1000.0
    )
    portfolio.get_positions.return_value = [pos]
    portfolio.get_equity.return_value = EquityView(10000.0, 10000.0, 0, 0, False)
    portfolio.get_asset_exposure.return_value = []
    portfolio.get_realized_pnl_by_strategy.return_value = {}
    portfolio.store = MagicMock()
    portfolio.store.get_snapshots.return_value = []

    engine = RiskEngine(config, market, portfolio)

    # Intent: Close
    intent = StrategyIntent(
        "test", "UNK", "flat", "exit", 0.0, 1.0, "1h", datetime.now(timezone.utc)
    )

    actions = engine.process_intents([intent])
    action = actions[0]

    assert action.action_type == "none"
    assert action.blocked is False
    assert "Untradeable: missing pair metadata" in action.reason
