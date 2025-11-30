# src/kraken_bot/execution/models.py

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Dict, List, Optional


@dataclass
class LocalOrder:
    local_id: str
    plan_id: Optional[str]
    strategy_id: Optional[str]
    pair: str
    side: str
    order_type: str
    kraken_order_id: Optional[str] = None
    userref: Optional[int] = None
    requested_base_size: float = 0.0
    requested_price: Optional[float] = None
    status: str = "pending"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    cumulative_base_filled: float = 0.0
    avg_fill_price: Optional[float] = None
    last_error: Optional[str] = None
    raw_request: Dict[str, Any] = field(default_factory=dict)
    raw_response: Optional[Dict[str, Any]] = None


@dataclass
class ExecutionResult:
    plan_id: str
    started_at: datetime
    completed_at: Optional[datetime] = None
    success: bool = False
    orders: List[LocalOrder] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
