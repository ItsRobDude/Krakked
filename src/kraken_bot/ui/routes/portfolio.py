"""Portfolio-related HTTP endpoints."""

from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Request

from kraken_bot.portfolio.models import SpotPosition

logger = logging.getLogger(__name__)

router = APIRouter()


def _context(request: Request):
    return request.app.state.context


def _build_position_payload(position: SpotPosition, price: float | None) -> Dict[str, Any]:
    current_value = position.base_size * price if price is not None else None
    unrealized = None
    if price is not None:
        unrealized = current_value - (position.base_size * position.avg_entry_price)

    return {
        "pair": position.pair,
        "base_asset": position.base_asset,
        "base_size": position.base_size,
        "avg_entry_price": position.avg_entry_price,
        "current_price": price,
        "value_usd": current_value,
        "unrealized_pnl_usd": unrealized,
        "strategy_tag": position.strategy_tag,
    }


@router.get("/summary")
async def get_portfolio_summary(request: Request):
    ctx = _context(request)
    try:
        equity = ctx.portfolio.get_equity()
        latest_snapshot = ctx.portfolio.get_latest_snapshot()
        data = {
            "equity_usd": equity.equity_base,
            "cash_usd": equity.cash_base,
            "realized_pnl_usd": equity.realized_pnl_base_total,
            "unrealized_pnl_usd": equity.unrealized_pnl_base_total,
            "drift_flag": equity.drift_flag,
            "last_snapshot_ts": latest_snapshot.timestamp if latest_snapshot else None,
        }
        return {"data": data, "error": None}
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Failed to fetch portfolio summary")
        return {"data": None, "error": str(exc)}


@router.get("/positions")
async def get_positions(request: Request):
    ctx = _context(request)
    try:
        positions = []
        for position in ctx.portfolio.get_positions():
            price = None
            try:
                price = ctx.market_data.get_latest_price(position.pair)
            except Exception:
                logger.debug("Price lookup failed", extra={"pair": position.pair})
            positions.append(_build_position_payload(position, price))

        return {"data": positions, "error": None}
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Failed to fetch positions")
        return {"data": None, "error": str(exc)}


@router.get("/exposure")
async def get_exposure(request: Request):
    ctx = _context(request)
    try:
        by_asset = [
            {
                "asset": exp.asset,
                "value_usd": exp.value_base,
                "pct_of_equity": exp.percentage_of_equity,
            }
            for exp in ctx.portfolio.get_asset_exposure()
        ]

        risk_status = ctx.strategy_engine.get_risk_status()
        exposure_by_strategy = [
            {"strategy_id": sid, "value_usd": None, "pct_of_equity": pct}
            for sid, pct in (risk_status.per_strategy_exposure_pct or {}).items()
        ]

        data = {"by_asset": by_asset, "by_strategy": exposure_by_strategy}
        return {"data": data, "error": None}
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Failed to fetch exposure")
        return {"data": None, "error": str(exc)}


@router.get("/trades")
async def get_trades(request: Request):
    ctx = _context(request)
    params = request.query_params

    pair = params.get("pair")
    strategy_id = params.get("strategy_id")
    try:
        limit = int(params.get("limit", 100))
    except (TypeError, ValueError):
        limit = 100

    def _parse_int(value: str | None) -> int | None:
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    since = _parse_int(params.get("since"))
    until = _parse_int(params.get("until"))

    try:
        trades = ctx.portfolio.get_trade_history(
            pair=pair, limit=limit, since=since, until=until, ascending=False
        )
        if strategy_id:
            trades = [t for t in trades if t.get("strategy_tag") == strategy_id]

        return {"data": trades, "error": None}
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Failed to fetch trades")
        return {"data": None, "error": str(exc)}


@router.post("/snapshot")
async def create_snapshot(request: Request):
    ctx = _context(request)
    if ctx.config.ui.read_only:
        logger.warning("Snapshot blocked: UI in read-only mode", extra={"event": "snapshot_blocked"})
        return {"data": None, "error": "UI is in read-only mode"}

    try:
        snapshot = ctx.portfolio.create_snapshot()
        logger.info("Created manual snapshot", extra={"event": "snapshot_created", "timestamp": snapshot.timestamp})
        data = {
            "timestamp": snapshot.timestamp,
            "equity_usd": snapshot.equity_base,
            "cash_usd": snapshot.cash_base,
            "realized_pnl_usd": snapshot.realized_pnl_base_total,
            "unrealized_pnl_usd": snapshot.unrealized_pnl_base_total,
        }
        return {"data": data, "error": None}
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Failed to create portfolio snapshot")
        return {"data": None, "error": str(exc)}
