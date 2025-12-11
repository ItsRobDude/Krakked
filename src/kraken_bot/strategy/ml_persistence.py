from __future__ import annotations

from datetime import datetime
from typing import List, Sequence, Tuple

from kraken_bot.portfolio.manager import PortfolioService
from kraken_bot.strategy.base import StrategyContext


def _resolve_mode(portfolio: PortfolioService) -> str:
    session = getattr(getattr(portfolio, "app_config", None), "session", None)
    mode = getattr(session, "mode", None)
    return str(mode) if mode else "paper"


def _get_ml_store(portfolio: PortfolioService):
    store = getattr(portfolio, "store", None)
    if store is None or not hasattr(store, "record_ml_example"):
        return None
    return store


def record_example(
    ctx: StrategyContext,
    strategy_id: str,
    model_key: str,
    *,
    label_type: str,
    features: Sequence[float],
    label: float,
    sample_weight: float = 1.0,
) -> None:
    store = _get_ml_store(ctx.portfolio)
    if store is None:
        return

    try:
        store.record_ml_example(
            strategy_id=strategy_id,
            model_key=model_key,
            created_at=ctx.now if isinstance(ctx.now, datetime) else datetime.utcnow(),
            source_mode=_resolve_mode(ctx.portfolio),
            label_type=label_type,
            features=features,
            label=label,
            sample_weight=sample_weight,
        )
    except Exception:
        return


def load_training_window(
    ctx: StrategyContext,
    strategy_id: str,
    model_key: str,
    *,
    max_examples: int,
) -> Tuple[List[List[float]], List[float]]:
    store = _get_ml_store(ctx.portfolio)
    if store is None or not hasattr(store, "load_ml_training_window"):
        return [], []

    try:
        return store.load_ml_training_window(
            strategy_id=strategy_id,
            model_key=model_key,
            max_examples=max_examples,
        )
    except Exception:
        return [], []


def save_model(
    ctx: StrategyContext,
    strategy_id: str,
    model_key: str,
    *,
    label_type: str,
    framework: str,
    model: object,
) -> None:
    store = _get_ml_store(ctx.portfolio)
    if store is None or not hasattr(store, "save_ml_model"):
        return

    try:
        store.save_ml_model(
            strategy_id=strategy_id,
            model_key=model_key,
            label_type=label_type,
            framework=framework,
            model=model,
        )
    except Exception:
        return


def load_model(
    ctx: StrategyContext, strategy_id: str, model_key: str
) -> Optional[Tuple[object, datetime]]:
    store = _get_ml_store(ctx.portfolio)
    if store is None or not hasattr(store, "load_ml_model"):
        return None

    try:
        return store.load_ml_model(strategy_id, model_key)
    except Exception:
        return None
