# src/kraken_bot/strategy/models.py

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from kraken_bot.portfolio.models import SpotPosition


@dataclass
class StrategyIntent:
    strategy_id: str  # e.g. "trend_core"
    pair: str  # canonical pair, e.g. "XBTUSD"
    side: str  # "long" | "flat"
    intent_type: str  # "enter" | "exit" | "increase" | "reduce" | "hold"
    desired_exposure_usd: Optional[float]  # if None, risk engine sizes it
    confidence: float  # [0.0, 1.0], strength of signal
    timeframe: str  # e.g. "1h", "4h"
    generated_at: datetime  # UTC
    metadata: Dict[str, Any] = field(
        default_factory=dict
    )  # free-form (e.g. indicators, scores)


@dataclass
class RiskAdjustedAction:
    pair: str
    strategy_id: str
    action_type: str  # "open" | "increase" | "reduce" | "close" | "none"
    target_base_size: float  # desired final base units (XBT, ETH, etc.)
    target_notional_usd: float  # desired final USD notional
    current_base_size: float  # from PortfolioService
    reason: str  # human-readable explanation
    blocked: bool  # true if action is blocked by risk limits
    blocked_reasons: List[str]  # list of violated limits, if any
    strategy_tag: Optional[str] = None
    userref: Optional[str] = None
    risk_limits_snapshot: Dict[str, Any] = field(
        default_factory=dict
    )  # config values, equity, etc.


@dataclass
class ExecutionPlan:
    plan_id: str
    generated_at: datetime
    actions: List[RiskAdjustedAction]
    metadata: Dict[str, Any] = field(
        default_factory=dict
    )  # e.g. equity snapshot, risk mode, etc.


@dataclass
class DecisionRecord:
    time: int  # UTC Timestamp
    plan_id: str
    strategy_name: Optional[str]
    pair: str
    action_type: str  # open/increase/reduce/close
    target_position_usd: float
    blocked: bool
    block_reason: Optional[str]
    kill_switch_active: bool
    raw_json: str  # full serialized StrategyDecisionBatch/Action as JSON string


@dataclass
class RiskStatus:
    kill_switch_active: bool
    daily_drawdown_pct: float
    drift_flag: bool
    total_exposure_pct: float
    manual_exposure_pct: float
    per_asset_exposure_pct: Dict[str, float]
    per_strategy_exposure_pct: Dict[str, float]
    # Optional extra detail about drift for logging / future UI
    drift_info: Optional[Dict[str, Any]] = None


@dataclass
class StrategyState:
    strategy_id: str
    enabled: bool
    last_intents_at: Optional[datetime]
    last_actions_at: Optional[datetime]
    current_positions: List[SpotPosition]  # positions attributable to this strategy
    pnl_summary: Dict[str, float]  # high-level from PortfolioService
    last_intents: Optional[List[Dict[str, Any]]] = None
    params: Dict[str, Any] = field(default_factory=dict)
