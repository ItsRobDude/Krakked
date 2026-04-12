from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from krakked.portfolio.manager import PortfolioService
from krakked.strategy.base import StrategyContext


def _resolve_mode(portfolio: PortfolioService) -> str:
    session = getattr(getattr(portfolio, "app_config", None), "session", None)
    mode = getattr(session, "mode", None)
    return str(mode) if mode else "paper"


def _get_ml_store(portfolio: PortfolioService):
    store = getattr(portfolio, "store", None)
    if store is None:
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


def save_training_checkpoint(
    ctx: StrategyContext,
    strategy_id: str,
    model_key: str,
    *,
    label_type: str,
    framework: str,
    model: object,
    checkpoint_state: str = "ready",
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    store = _get_ml_store(ctx.portfolio)
    if store is None or not hasattr(store, "save_ml_model_checkpoint"):
        return

    try:
        store.save_ml_model_checkpoint(
            strategy_id=strategy_id,
            model_key=model_key,
            checkpoint_kind="training",
            label_type=label_type,
            framework=framework,
            model=model,
            checkpoint_state=checkpoint_state,
            metadata=metadata,
        )
    except Exception:
        return


def load_training_checkpoint(
    ctx: StrategyContext, strategy_id: str, model_key: str
) -> Optional[Tuple[object, datetime, str, Dict[str, Any]]]:
    store = _get_ml_store(ctx.portfolio)
    if store is None or not hasattr(store, "load_ml_model_checkpoint"):
        return None

    try:
        return store.load_ml_model_checkpoint(
            strategy_id,
            model_key,
            checkpoint_kind="training",
        )
    except Exception:
        return None
