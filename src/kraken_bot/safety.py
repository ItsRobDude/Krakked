"""Safety status helpers for startup logging."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass

from kraken_bot.config import AppConfig
from kraken_bot.logging_config import get_log_environment, structured_log_extra

logger = logging.getLogger(__name__)


@dataclass
class SafetyStatus:
    """Summarize key safety toggles derived from execution config."""

    live_mode_enabled: bool
    validate_only: bool
    allow_live_trading: bool
    has_min_order_notional: bool
    has_max_pair_notional: bool
    has_max_total_notional: bool
    has_max_concurrent_orders: bool


def check_safety(config: AppConfig) -> SafetyStatus:
    """Evaluate safety-related settings from configuration."""

    execution = config.execution
    return SafetyStatus(
        live_mode_enabled=execution.mode == "live",
        validate_only=execution.validate_only,
        allow_live_trading=execution.allow_live_trading,
        has_min_order_notional=execution.min_order_notional_usd is not None,
        has_max_pair_notional=execution.max_pair_notional_usd is not None,
        has_max_total_notional=execution.max_total_notional_usd is not None,
        has_max_concurrent_orders=execution.max_concurrent_orders is not None,
    )


def log_safety_status(status: SafetyStatus, env: str | None = None) -> None:
    """Log a structured summary of safety toggles."""

    logger.info(
        "Safety status evaluated",
        extra=structured_log_extra(
            env=env or get_log_environment(),
            event="safety_status",
            **asdict(status),
        ),
    )


__all__ = ["SafetyStatus", "check_safety", "log_safety_status"]
