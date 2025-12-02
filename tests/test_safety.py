"""Safety status evaluation tests."""

from kraken_bot.config import (
    AppConfig,
    ExecutionConfig,
    MarketDataConfig,
    PortfolioConfig,
    RegionCapabilities,
    RegionProfile,
    RiskConfig,
    StrategiesConfig,
    UIConfig,
    UniverseConfig,
)
from kraken_bot.safety import SafetyStatus, check_safety


def _make_config(execution: ExecutionConfig) -> AppConfig:
    return AppConfig(
        region=RegionProfile(
            code="US", capabilities=RegionCapabilities(False, False, False)
        ),
        universe=UniverseConfig(
            include_pairs=[], exclude_pairs=[], min_24h_volume_usd=0.0
        ),
        market_data=MarketDataConfig(
            ws={}, ohlc_store={}, backfill_timeframes=[], ws_timeframes=[]
        ),
        portfolio=PortfolioConfig(),
        execution=execution,
        risk=RiskConfig(),
        strategies=StrategiesConfig(),
        ui=UIConfig(),
    )


def test_safety_status_for_paper_mode():
    status = check_safety(_make_config(ExecutionConfig()))

    assert status == SafetyStatus(
        live_mode_enabled=False,
        validate_only=True,
        allow_live_trading=False,
        has_min_order_notional=True,
        has_max_pair_notional=False,
        has_max_total_notional=False,
        has_max_concurrent_orders=True,
    )


def test_safety_status_for_live_validate_only():
    execution = ExecutionConfig(
        mode="live", validate_only=True, allow_live_trading=False
    )
    status = check_safety(_make_config(execution))

    assert status == SafetyStatus(
        live_mode_enabled=True,
        validate_only=True,
        allow_live_trading=False,
        has_min_order_notional=True,
        has_max_pair_notional=False,
        has_max_total_notional=False,
        has_max_concurrent_orders=True,
    )


def test_safety_status_for_fully_live():
    execution = ExecutionConfig(
        mode="live",
        validate_only=False,
        allow_live_trading=True,
        max_pair_notional_usd=1000.0,
        max_total_notional_usd=5000.0,
        max_concurrent_orders=3,
    )
    status = check_safety(_make_config(execution))

    assert status == SafetyStatus(
        live_mode_enabled=True,
        validate_only=False,
        allow_live_trading=True,
        has_min_order_notional=True,
        has_max_pair_notional=True,
        has_max_total_notional=True,
        has_max_concurrent_orders=True,
    )
