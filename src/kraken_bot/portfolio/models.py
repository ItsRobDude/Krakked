# src/kraken_bot/portfolio/models.py

from dataclasses import dataclass, field
from typing import Optional, List, Dict
from datetime import datetime

@dataclass
class AssetBalance:
    asset: str
    free: float
    reserved: float
    total: float

@dataclass
class SpotPosition:
    pair: str
    base_asset: str
    quote_asset: str
    base_size: float
    avg_entry_price: float
    realized_pnl_base: float
    fees_paid_base: float
    unrealized_pnl_base: float = 0.0
    current_value_base: float = 0.0
    strategy_tag: Optional[str] = None
    raw_userref: Optional[str] = None
    comment: Optional[str] = None

@dataclass
class RealizedPnLRecord:
    trade_id: str
    order_id: Optional[str]
    pair: str
    time: int  # UTC Timestamp
    side: str
    base_delta: float
    quote_delta: float
    fee_asset: str
    fee_amount: float
    pnl_quote: float
    strategy_tag: Optional[str]
    raw_userref: Optional[str] = None
    comment: Optional[str] = None

@dataclass
class CashFlowRecord:
    id: str
    time: int  # UTC Timestamp
    asset: str
    amount: float
    type: str # "deposit" | "withdrawal" | "reward" | "adjustment"
    note: Optional[str]

@dataclass
class AssetValuation:
    asset: str
    amount: float
    value_base: float
    source_pair: Optional[str]
    valuation_status: str = "valued"

@dataclass
class PortfolioSnapshot:
    timestamp: int  # UTC Timestamp
    equity_base: float
    cash_base: float
    asset_valuations: List[AssetValuation]
    realized_pnl_base_total: float
    unrealized_pnl_base_total: float
    realized_pnl_base_by_pair: Dict[str, float]
    unrealized_pnl_base_by_pair: Dict[str, float]

@dataclass
class AssetExposure:
    asset: str
    amount: float
    value_base: float
    percentage_of_equity: float
    source_pair: Optional[str] = None
    valuation_status: str = "valued"

@dataclass
class EquityView:
    equity_base: float
    cash_base: float
    realized_pnl_base_total: float
    unrealized_pnl_base_total: float
    drift_flag: bool
    unvalued_assets: List[str] = field(default_factory=list)
