"""Safety status helpers for startup logging."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass

from krakked.config import AppConfig
from krakked.logging_config import get_log_environment, structured_log_extra

logger = logging.getLogger(__name__)


@dataclass
class SafetyStatus:
    """Summarize key safety toggles derived from execution config."""

    live_mode_enabled: bool
    validate_only: bool
    live_order_submission_blocked: bool
    allow_live_trading: bool
    has_live_strategy_allowlist: bool
    has_min_order_notional: bool
    has_max_pair_notional: bool
    has_max_total_notional: bool
    has_max_concurrent_orders: bool


def check_safety(config: AppConfig) -> SafetyStatus:
    """Evaluate safety-related settings from configuration."""

    execution = config.execution
    validate_only = execution.validate_only or execution.mode == "paper"
    has_live_strategy_allowlist = bool(
        [
            strategy_id
            for strategy_id in getattr(execution, "live_strategy_allowlist", [])
            if str(strategy_id).strip()
        ]
    )
    live_order_submission_blocked = (
        execution.mode != "live"
        or execution.validate_only
        or not execution.allow_live_trading
        or not has_live_strategy_allowlist
    )
    return SafetyStatus(
        live_mode_enabled=execution.mode == "live",
        validate_only=validate_only,
        live_order_submission_blocked=live_order_submission_blocked,
        allow_live_trading=execution.allow_live_trading,
        has_live_strategy_allowlist=has_live_strategy_allowlist,
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
