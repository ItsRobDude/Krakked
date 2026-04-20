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
    # Maximum allowed age for a plan at execution time. Plans older than this
    # are rejected before any orders are built or submitted.
    max_plan_age_seconds: int = 60


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


@dataclass
class SessionConfig:
    active: bool = False
    profile_name: Optional[str] = None
    mode: str = "paper"
    loop_interval_sec: float = 15.0
    emergency_flatten: bool = False
    account_id: Optional[str] = None


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
    """Configuration for portfolio risk limits and safety guardrails.

    These limits are enforced by the RiskEngine before any orders are generated.
    Violations result in blocked or clamped (reduced) orders.
    """

    # Maximum percentage of current equity to risk on a single trade.
    # Used to size positions based on volatility (stop distance).
    max_risk_per_trade_pct: float = 1.0

    # Hard cap on total exposure as a percentage of equity (e.g., 100.0 = no leverage).
    # If exceeded, all new opening orders are blocked.
    max_portfolio_risk_pct: float = 10.0

    # Maximum number of concurrent open positions allowed across the portfolio.
    max_open_positions: int = 10

    # Maximum exposure allowed for a single asset (as % of total equity).
    # Prevents over-concentration in one coin.
    max_per_asset_pct: float = 5.0

    # Per-strategy exposure limits (strategy_id -> max % of equity).
    # Useful for capping experimental strategies.
    max_per_strategy_pct: Dict[str, float] = field(default_factory=dict)

    # If the daily drawdown (peak-to-trough) exceeds this %, the kill switch activates.
    # All opening orders are blocked until the drawdown resets.
    max_daily_drawdown_pct: float = 10.0

    # If True, activates the kill switch when portfolio state drifts from Kraken's
    # reported balances or if price data for pending orders is stale/missing.
    kill_switch_on_drift: bool = True

    # If True, manually opened positions count towards risk limits (exposure caps).
    # If False, they are ignored by the risk engine but tracked in the portfolio.
    include_manual_positions: bool = True

    # Number of bars to look back when calculating ATR for volatility sizing.
    volatility_lookback_bars: int = 20

    # Minimum 24h volume (in USD) required for a pair to be tradable.
    # Pairs below this threshold are blocked to avoid slippage/liquidity traps.
    min_liquidity_24h_usd: float = 100000.0

    # --- Allocation Features (Future/Experimental) ---
    dynamic_allocation_enabled: bool = False
    dynamic_allocation_lookback_hours: int = 72
    min_strategy_weight_pct: float = 0.0
    max_strategy_weight_pct: float = 50.0


@dataclass
class StrategyConfig:
    """Configuration for a specific trading strategy instance.

    Strategies are identified by their `name` (the instance ID) and `type`
    (the implementation logic class).
    """

    name: str
    type: str
    enabled: bool
    # Generic parameter dict that specific strategies will parse into typed configs
    params: Dict[str, Any] = field(default_factory=dict)

    # Explicit userref to ensure consistent PnL tracking.
    # If set, this integer is attached to all orders for this strategy.
    # If None, a stable integer is deterministically derived from the strategy name.
    # Setting this manually prevents ID shifts if strategy names change.
    userref: Optional[int] = None
    # User-facing relative weight on a 1-100 scale. Krakked normalizes active
    # strategies internally, but this keeps the control simple in the UI.
    strategy_weight: int = 100


@dataclass
class StrategiesConfig:
    enabled: List[str] = field(default_factory=list)
    configs: Dict[str, StrategyConfig] = field(default_factory=dict)


@dataclass
class MLConfig:
    enabled: bool = False
    training_window_examples: int = 5000
    catch_up_max_days: int = 7
    catch_up_max_bars: int = 500


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
    ml: MLConfig = field(default_factory=MLConfig)
