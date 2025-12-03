# src/kraken_bot/strategy/performance.py
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List

from kraken_bot.portfolio.models import RealizedPnLRecord
from kraken_bot.portfolio.portfolio import Portfolio


@dataclass
class StrategyPerformance:
    strategy_id: str
    realized_pnl_quote: float
    window_start: datetime
    window_end: datetime
    trade_count: int
    win_rate: float
    max_drawdown_pct: float


def _drawdown_pct(pnl_series: List[float]) -> float:
    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0

    for pnl in pnl_series:
        cumulative += pnl
        peak = max(peak, cumulative)
        if peak > 0:
            drawdown = (peak - cumulative) / peak * 100
            max_drawdown = max(max_drawdown, drawdown)

    return max_drawdown


def compute_strategy_performance(
    portfolio: Portfolio, window: timedelta
) -> Dict[str, StrategyPerformance]:
    """Aggregate recent realized PnL into per-strategy performance metrics."""

    window_end = datetime.now(timezone.utc)
    window_start = window_end - window

    records_by_strategy: Dict[str, List[RealizedPnLRecord]] = {}

    for record in portfolio.realized_pnl_history:
        record_time = datetime.fromtimestamp(record.time, tz=timezone.utc)
        if record_time < window_start or record_time > window_end:
            continue

        strategy_id = record.strategy_tag or "manual"
        records_by_strategy.setdefault(strategy_id, []).append(record)

    performance: Dict[str, StrategyPerformance] = {}

    for strategy_id, records in records_by_strategy.items():
        records.sort(key=lambda r: r.time)
        pnl_values = [r.pnl_quote for r in records]
        trade_count = len(records)
        wins = sum(1 for r in records if r.pnl_quote > 0)

        performance[strategy_id] = StrategyPerformance(
            strategy_id=strategy_id,
            realized_pnl_quote=sum(pnl_values),
            window_start=window_start,
            window_end=window_end,
            trade_count=trade_count,
            win_rate=(wins / trade_count) if trade_count else 0.0,
            max_drawdown_pct=_drawdown_pct(pnl_values) if pnl_values else 0.0,
        )

    return performance
