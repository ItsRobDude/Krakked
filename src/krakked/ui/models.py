from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Generic, List, Optional, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

T = TypeVar("T")


class ApiEnvelope(BaseModel, Generic[T]):
    """Standard API envelope for UI responses."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    data: Optional[T]
    error: Optional[str] = None


class PortfolioSummary(BaseModel):
    equity_usd: Optional[float]
    cash_usd: Optional[float]
    realized_pnl_usd: Optional[float]
    unrealized_pnl_usd: Optional[float]
    drift_flag: Optional[bool]
    last_snapshot_ts: Optional[int]


class PositionPayload(BaseModel):
    pair: str
    base_asset: str
    base_size: float
    avg_entry_price: Optional[float]
    current_price: Optional[float]
    value_usd: Optional[float]
    unrealized_pnl_usd: Optional[float]
    strategy_tag: Optional[str] = None
    is_dust: bool = False
    min_order_size: Optional[float] = None
    rounded_close_size: Optional[float] = None
    dust_reason: Optional[str] = None


class AssetExposureBreakdown(BaseModel):
    asset: str
    value_usd: Optional[float]
    pct_of_equity: Optional[float]


class StrategyExposureBreakdown(BaseModel):
    strategy_id: str
    value_usd: Optional[float]
    pct_of_equity: Optional[float]


class ExposureBreakdown(BaseModel):
    by_asset: List[AssetExposureBreakdown]
    by_strategy: List[StrategyExposureBreakdown]


class RiskStatusPayload(BaseModel):
    kill_switch_active: bool
    daily_drawdown_pct: float
    drift_flag: bool
    total_exposure_pct: float
    manual_exposure_pct: float
    per_asset_exposure_pct: Dict[str, float]
    per_strategy_exposure_pct: Dict[str, float]


class RiskDecisionPayload(BaseModel):
    decided_at: datetime
    plan_id: str
    strategy_id: Optional[str]
    pair: str
    action_type: str
    blocked: bool
    block_reasons: List[str]
    kill_switch_active: bool


class ConfirmationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmation: str = Field(min_length=1)


class KillSwitchPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    active: bool
    confirmation: Optional[str] = None


class RiskConfigPatchPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_risk_per_trade_pct: Optional[float] = Field(None, ge=0.0, le=100.0)
    max_portfolio_risk_pct: Optional[float] = Field(None, ge=0.0, le=100.0)
    max_open_positions: Optional[int] = Field(None, ge=1)
    max_per_asset_pct: Optional[float] = Field(None, ge=0.0, le=100.0)
    max_per_strategy_pct: Optional[Dict[str, float]] = None
    max_daily_drawdown_pct: Optional[float] = Field(None, ge=0.0, le=100.0)
    kill_switch_on_drift: Optional[bool] = None
    include_manual_positions: Optional[bool] = None
    volatility_lookback_bars: Optional[int] = Field(None, ge=1)
    min_liquidity_24h_usd: Optional[float] = Field(None, ge=0.0)
    dynamic_allocation_enabled: Optional[bool] = None
    dynamic_allocation_lookback_hours: Optional[int] = Field(None, ge=1)
    min_strategy_weight_pct: Optional[float] = Field(None, ge=0.0, le=100.0)
    max_strategy_weight_pct: Optional[float] = Field(None, ge=0.0, le=100.0)

    @field_validator("max_per_strategy_pct")
    @classmethod
    def _validate_max_per_strategy_pct(
        cls, value: Optional[Dict[str, float]]
    ) -> Optional[Dict[str, float]]:
        if value is None:
            return value
        for strategy_id, pct in value.items():
            if not 0.0 <= pct <= 100.0:
                raise ValueError(
                    f"max_per_strategy_pct['{strategy_id}'] must be between 0 and 100"
                )
        return value


class RiskConfigPayload(BaseModel):
    max_risk_per_trade_pct: float
    max_portfolio_risk_pct: float
    max_open_positions: int
    max_per_asset_pct: float
    max_per_strategy_pct: Dict[str, float]
    max_daily_drawdown_pct: float
    kill_switch_on_drift: bool
    include_manual_positions: bool
    volatility_lookback_bars: int
    min_liquidity_24h_usd: float
    dynamic_allocation_enabled: bool
    dynamic_allocation_lookback_hours: int
    min_strategy_weight_pct: float
    max_strategy_weight_pct: float


class StrategyConfigParamsPatchPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    risk_profile: Optional[str] = None
    continuous_learning: Optional[bool] = None


class StrategyConfigPatchPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy_weight: Optional[int] = Field(None, ge=1, le=100)
    params: Optional[StrategyConfigParamsPatchPayload] = None


class StrategyPosition(BaseModel):
    pair: str
    base_asset: str
    quote_asset: str
    base_size: float
    avg_entry_price: float
    realized_pnl_base: float
    fees_paid_base: float
    unrealized_pnl_base: float
    current_value_base: float
    strategy_tag: Optional[str] = None
    raw_userref: Optional[str] = None
    comment: Optional[str] = None


class StrategyStatePayload(BaseModel):
    strategy_id: str
    label: str
    enabled: bool
    last_intents_at: Optional[datetime]
    last_actions_at: Optional[datetime]
    current_positions: List[StrategyPosition]
    pnl_summary: Dict[str, float]
    last_intents: Optional[list[dict[str, Any]]] = None
    params: Dict[str, Any] = Field(default_factory=dict)
    configured_weight: int = 100
    effective_weight_pct: Optional[float] = None


class StrategyPerformancePayload(BaseModel):
    strategy_id: str
    realized_pnl_quote: float
    window_start: datetime
    window_end: datetime
    trade_count: int
    win_rate: float
    max_drawdown_pct: float


class OpenOrderPayload(BaseModel):
    local_id: str
    plan_id: Optional[str]
    strategy_id: Optional[str]
    pair: str
    side: str
    order_type: str
    kraken_order_id: Optional[str] = None
    userref: Optional[int] = None
    requested_base_size: float
    requested_price: Optional[float]
    status: str
    created_at: datetime
    updated_at: datetime
    cumulative_base_filled: float
    avg_fill_price: Optional[float] = None
    last_error: Optional[str] = None
    raw_request: Dict[str, Any]
    raw_response: Optional[Dict[str, Any]] = None


class ExecutionResultPayload(BaseModel):
    plan_id: str
    started_at: datetime
    completed_at: Optional[datetime] = None
    success: bool
    orders: List[OpenOrderPayload]
    errors: List[str]
    warnings: List[str]


class SystemHealthPayload(BaseModel):
    app_version: Optional[str] = Field(
        None, description="Application semantic version reported to the UI."
    )
    execution_mode: Optional[str] = Field(
        None,
        description="execution_mode reflects the configured trading mode: dry-run, paper, or live.",
    )
    rest_api_reachable: bool
    websocket_connected: bool
    streaming_pairs: int
    stale_pairs: int
    subscription_errors: int
    market_data_ok: bool
    market_data_status: str
    market_data_reason: Optional[str] = None
    market_data_stale: Optional[bool] = Field(
        None,
        description="Indicates whether market data is considered stale based on stream freshness.",
    )
    market_data_max_staleness: Optional[float] = Field(
        None,
        description="Maximum observed staleness for market data feeds in seconds.",
    )
    execution_ok: bool
    current_mode: str
    ui_read_only: bool
    kill_switch_active: Optional[bool] = Field(
        None,
        description="Reports whether the risk engine's kill switch is currently active.",
    )
    drift_detected: bool
    drift_reason: Optional[str] = None


class ErrorRecord(BaseModel):
    at: datetime
    message: str


class SystemMetricsPayload(BaseModel):
    plans_generated: int
    plans_executed: int
    blocked_actions: int
    execution_errors: int
    market_data_errors: int
    recent_errors: List[ErrorRecord]
    last_equity_usd: Optional[float]
    last_realized_pnl_usd: Optional[float]
    last_unrealized_pnl_usd: Optional[float]
    open_orders_count: int
    open_positions_count: int
    drift_detected: bool
    drift_reason: Optional[str] = None
    market_data_ok: bool
    market_data_stale: bool
    market_data_reason: Optional[str] = None
    market_data_max_staleness: Optional[float] = None
