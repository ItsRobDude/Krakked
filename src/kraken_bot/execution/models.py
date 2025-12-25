# src/kraken_bot/execution/models.py

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Dict, List, Optional


@dataclass
class LocalOrder:
    """
    Represents the full lifecycle of a single order within the bot's execution system.

    This model bridges the gap between abstract strategy intents and concrete
    exchange orders. It tracks the order from creation (pending) through
    submission, open state, and final resolution (filled/canceled).

    Key Attributes:
        local_id: A deterministic UUID (v5) generated from plan+strategy+pair+side.
                  Used for idempotency and internal tracking before Kraken assigns an ID.
        kraken_order_id: The exchange-assigned order ID (e.g., 'O1234-56789...').
                         Populated only after successful submission.
        status: The current state of the order. Common values:
                'pending' (created but not sent), 'submitted' (sent, waiting for ACK),
                'open' (accepted by exchange), 'filled', 'canceled', 'rejected'.
        risk_reducing: If True, this order is closing or reducing a position.
                       Risk-reducing orders bypass the `min_order_notional_usd`
                       guardrail to prevent stranding small "dust" positions.
        userref: An optional 32-bit integer tag used for strategy attribution
                 on the exchange. See `kraken_bot.execution.userref`.
    """

    local_id: str
    plan_id: Optional[str]
    strategy_id: Optional[str]
    pair: str
    side: str
    order_type: str
    kraken_order_id: Optional[str] = None
    # Kraken userref is conceptually an integer; keep it int in the core model.
    userref: Optional[int] = None
    requested_base_size: float = 0.0
    requested_price: Optional[float] = None
    status: str = "pending"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    cumulative_base_filled: float = 0.0
    avg_fill_price: Optional[float] = None
    risk_reducing: bool = False
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
