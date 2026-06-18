"""Safety status evaluation tests."""

from krakked.config import (
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
from krakked.safety import SafetyStatus, check_safety


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
    execution = ExecutionConfig()
    assert execution.validate_only is False

    status = check_safety(_make_config(execution))

    assert status == SafetyStatus(
        live_mode_enabled=False,
        validate_only=True,
        live_order_submission_blocked=True,
        allow_live_trading=False,
        has_live_strategy_allowlist=False,
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
        live_order_submission_blocked=True,
        allow_live_trading=False,
        has_live_strategy_allowlist=False,
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
        live_strategy_allowlist=["manual_dca"],
        max_pair_notional_usd=1000.0,
        max_total_notional_usd=5000.0,
        max_concurrent_orders=3,
    )
    status = check_safety(_make_config(execution))

    assert status == SafetyStatus(
        live_mode_enabled=True,
        validate_only=False,
        live_order_submission_blocked=False,
        allow_live_trading=True,
        has_live_strategy_allowlist=True,
        has_min_order_notional=True,
        has_max_pair_notional=True,
        has_max_total_notional=True,
        has_max_concurrent_orders=True,
    )


def test_safety_status_blocks_live_without_strategy_allowlist():
    execution = ExecutionConfig(
        mode="live",
        validate_only=False,
        allow_live_trading=True,
        max_pair_notional_usd=1000.0,
        max_total_notional_usd=5000.0,
    )
    status = check_safety(_make_config(execution))

    assert status.live_mode_enabled is True
    assert status.validate_only is False
    assert status.allow_live_trading is True
    assert status.has_live_strategy_allowlist is False
    assert status.live_order_submission_blocked is True


def test_safety_status_allowlist_flag_does_not_claim_enabled_strategy_approval():
    execution = ExecutionConfig(
        mode="live",
        validate_only=False,
        allow_live_trading=True,
        live_strategy_allowlist=["manual_dca"],
        max_pair_notional_usd=1000.0,
        max_total_notional_usd=5000.0,
    )
    status = check_safety(_make_config(execution))

    assert status.has_live_strategy_allowlist is True
    assert status.live_order_submission_blocked is False
