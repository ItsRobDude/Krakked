from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class RegionCapabilities:
    supports_margin: bool
    supports_futures: bool
    supports_staking: bool


@dataclass
class RegionProfile:
    code: str
    capabilities: RegionCapabilities
    default_quote: str = "USD"


@dataclass
class UniverseConfig:
    include_pairs: list[str]
    exclude_pairs: list[str]
    min_24h_volume_usd: float


@dataclass
class MarketDataConfig:
    ws: dict
    ohlc_store: dict
    backfill_timeframes: list[str]
    ws_timeframes: list[str]
    metadata_path: Optional[str] = None


@dataclass
class ExecutionConfig:
    mode: str = "paper"
    default_order_type: str = "limit"
    max_slippage_bps: int = 50
    time_in_force: str = "GTC"
    post_only: bool = False
    validate_only: bool = True
    allow_live_trading: bool = False
    paper_tests_completed: bool = False
    dead_man_switch_seconds: int = 600
    max_retries: int = 3
    retry_backoff_seconds: int = 2
    retry_backoff_factor: float = 2.0
    max_concurrent_orders: int = 10
    min_order_notional_usd: float = 20.0
    max_pair_notional_usd: Optional[float] = None
    max_total_notional_usd: Optional[float] = None


@dataclass
class UIAuthConfig:
    enabled: bool = False
    token: str = ""


@dataclass
class UIRefreshConfig:
    dashboard_ms: int = 5000
    orders_ms: int = 5000
    strategies_ms: int = 10000


@dataclass
class UIConfig:
    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 8080
    base_path: str = "/"
    auth: UIAuthConfig = field(default_factory=UIAuthConfig)
    read_only: bool = False
    refresh_intervals: UIRefreshConfig = field(default_factory=UIRefreshConfig)


@dataclass
class ProfileConfig:
    name: str
    description: str = ""
    config_path: str = ""
    credentials_path: str = ""
    default_mode: str = "paper"
    default_loop_interval_sec: float = 15.0
    default_ml_enabled: bool = True


@dataclass
class SessionConfig:
    active: bool = False
    profile_name: Optional[str] = None
    mode: str = "paper"
    loop_interval_sec: float = 15.0
    ml_enabled: bool = True


@dataclass
class PortfolioConfig:
    base_currency: str = "USD"
    valuation_pairs: Dict[str, str] = field(default_factory=dict)
    include_assets: List[str] = field(default_factory=list)
    exclude_assets: List[str] = field(default_factory=list)
    cost_basis_method: str = "wac"
    track_manual_trades: bool = True
    snapshot_retention_days: int = 30
    reconciliation_tolerance: float = 1.0
    db_path: str = "portfolio.db"
    auto_migrate_schema: bool = True


@dataclass
class RiskConfig:
    max_risk_per_trade_pct: float = 1.0
    max_portfolio_risk_pct: float = 10.0
    max_open_positions: int = 10
    max_per_asset_pct: float = 5.0
    max_per_strategy_pct: Dict[str, float] = field(default_factory=dict)
    max_daily_drawdown_pct: float = 10.0
    kill_switch_on_drift: bool = True
    include_manual_positions: bool = True
    volatility_lookback_bars: int = 20
    min_liquidity_24h_usd: float = 100000.0
    dynamic_allocation_enabled: bool = False
    dynamic_allocation_lookback_hours: int = 72
    min_strategy_weight_pct: float = 0.0
    max_strategy_weight_pct: float = 50.0


@dataclass
class StrategyConfig:
    name: str
    type: str
    enabled: bool
    # Generic parameter dict that specific strategies will parse into typed configs
    params: Dict[str, Any] = field(default_factory=dict)
    # Explicit userref to ensure consistent PnL tracking
    userref: Optional[int] = None


@dataclass
class StrategiesConfig:
    enabled: List[str] = field(default_factory=list)
    configs: Dict[str, StrategyConfig] = field(default_factory=dict)


@dataclass
class AppConfig:
    region: RegionProfile
    universe: UniverseConfig
    market_data: MarketDataConfig
    portfolio: PortfolioConfig
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    strategies: StrategiesConfig = field(default_factory=StrategiesConfig)
    ui: UIConfig = field(default_factory=UIConfig)
    profiles: Dict[str, ProfileConfig] = field(default_factory=dict)
    session: SessionConfig = field(default_factory=SessionConfig)
