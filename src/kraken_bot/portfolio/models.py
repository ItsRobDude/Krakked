# src/kraken_bot/portfolio/models.py

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional


@dataclass
class LedgerEntry:
    id: str  # txid / ledger id
    time: float  # unix timestamp
    type: str  # "trade", "deposit", "withdrawal", ...
    subtype: str  # "spottofutures", "reward", ...
    aclass: str  # usually "currency"
    asset: str  # e.g. "XXBT", "ZUSD"
    amount: Decimal  # signed: +credit, -debit
    fee: Decimal
    balance: Optional[Decimal]  # post-transaction balance from Kraken
    refid: Optional[str]  # trade id / withdrawal id / etc.
    misc: Optional[str]  # raw `misc` from API
    raw: Dict[str, Any]  # full original payload for safety


@dataclass
class BalanceSnapshot:
    id: Optional[int]
    time: float  # timestamp of snapshot
    last_ledger_id: str  # last ledger id included in this snapshot
    balances: Dict[
        str, "AssetBalance"
    ]  # {asset: AssetBalance(total, free, reserved)}


class CashFlowCategory(Enum):
    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal"
    FEE = "fee"
    STAKING_REWARD = "staking_reward"
    STAKING_ALLOCATION = "staking_allocation"
    STAKING_DEALLOCATION = "staking_deallocation"
    SPOT_TO_FUTURES = "spot_to_futures"
    FUTURES_TO_SPOT = "futures_to_spot"
    ADJUSTMENT = "adjustment"
    TRADE_PNL = "trade_pnl"
    INTERNAL = "internal"  # neutral internal shuffles


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
    type: str  # "deposit" | "withdrawal" | "reward" | "adjustment"
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
    valuation_status: str = "valued"


@dataclass
class EquityView:
    equity_base: float
    cash_base: float
    realized_pnl_base_total: float
    unrealized_pnl_base_total: float
    drift_flag: bool
    unvalued_assets: List[str] = field(default_factory=list)


@dataclass
class DriftMismatchedAsset:
    asset: str
    expected_quantity: float
    actual_quantity: float
    difference_base: float


@dataclass
class DriftStatus:
    drift_flag: bool
    expected_position_value_base: float
    actual_balance_value_base: float
    tolerance_base: float
    mismatched_assets: List[DriftMismatchedAsset] = field(default_factory=list)
