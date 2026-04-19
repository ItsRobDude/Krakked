"""Portfolio-related HTTP endpoints."""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Request

from krakked.execution.router import classify_volume, dust_reason
from krakked.portfolio.models import SpotPosition
from krakked.ui.logging import build_request_log_extra
from krakked.ui.models import (
    ApiEnvelope,
    AssetExposureBreakdown,
    ExposureBreakdown,
    PortfolioSummary,
    PositionPayload,
    StrategyExposureBreakdown,
)
from krakked.ui.route_runtime import run_bounded_route_read

logger = logging.getLogger(__name__)

router = APIRouter()


def _context(request: Request):
    return request.app.state.context


def _build_position_payload(
    position: SpotPosition, price: float | None, metadata: Any | None
) -> PositionPayload:
    current_value: float | None = getattr(position, "current_value_base", None)
    unrealized: float | None = getattr(position, "unrealized_pnl_base", None)

    if price is not None:
        current_value = position.base_size * price
        unrealized = current_value - (position.base_size * position.avg_entry_price)
    elif current_value is not None and abs(position.base_size) > 1e-12:
        price = current_value / position.base_size

    is_dust = False
    min_order_size = None
    rounded_close_size = None
    dust_reason_text = None

    if metadata:
        min_order_size = metadata.min_order_size
        rounded, ok = classify_volume(metadata, abs(position.base_size))
        rounded_close_size = rounded
        if not ok:
            is_dust = True
            dust_reason_text = dust_reason(metadata, abs(position.base_size), rounded)
    else:
        # Missing metadata => untradeable => treat as dust-like
        is_dust = True
        dust_reason_text = "Untradeable: missing pair metadata"

    return PositionPayload(
        pair=position.pair,
        base_asset=position.base_asset,
        base_size=position.base_size,
        avg_entry_price=position.avg_entry_price,
        current_price=price,
        value_usd=current_value,
        unrealized_pnl_usd=unrealized,
        strategy_tag=position.strategy_tag,
        is_dust=is_dust,
        min_order_size=min_order_size,
        rounded_close_size=rounded_close_size,
        dust_reason=dust_reason_text,
    )


@router.get("/summary", response_model=ApiEnvelope[PortfolioSummary])
async def get_portfolio_summary(request: Request) -> ApiEnvelope[PortfolioSummary]:
    ctx = _context(request)

    def _read_summary() -> PortfolioSummary:
        equity = ctx.portfolio.get_cached_equity()
        last_snapshot_ts = ctx.portfolio.get_cached_last_snapshot_ts()
        return PortfolioSummary(
            equity_usd=equity.equity_base,
            cash_usd=equity.cash_base,
            realized_pnl_usd=equity.realized_pnl_base_total,
            unrealized_pnl_usd=equity.unrealized_pnl_base_total,
            drift_flag=equity.drift_flag,
            last_snapshot_ts=last_snapshot_ts,
            portfolio_baseline=getattr(ctx.portfolio, "baseline_source", None),
        )

    return await run_bounded_route_read(
        request,
        route_key="portfolio.summary",
        reader=_read_summary,
        logger=logger,
        busy_error="Portfolio summary refresh is already in progress.",
        timeout_error="Portfolio summary timed out.",
        failure_event="portfolio_summary_failed",
    )


@router.get("/positions", response_model=ApiEnvelope[List[PositionPayload]])
async def get_positions(request: Request) -> ApiEnvelope[List[PositionPayload]]:
    ctx = _context(request)

    def _read_positions() -> List[PositionPayload]:
        positions: List[PositionPayload] = []
        for position in ctx.portfolio.get_cached_positions():
            price = (
                (position.current_value_base / position.base_size)
                if abs(position.base_size) > 1e-12 and position.current_value_base
                else None
            )
            metadata = None

            try:
                metadata = ctx.market_data.get_pair_metadata(position.pair)
            except Exception:
                logger.debug(
                    "Metadata lookup failed",
                    extra=build_request_log_extra(
                        request, event="metadata_lookup_failed", pair=position.pair
                    ),
                )

            positions.append(_build_position_payload(position, price, metadata))

        return positions

    return await run_bounded_route_read(
        request,
        route_key="portfolio.positions",
        reader=_read_positions,
        logger=logger,
        busy_error="Position refresh is already in progress.",
        timeout_error="Positions request timed out.",
        failure_event="positions_fetch_failed",
    )


@router.get("/exposure", response_model=ApiEnvelope[ExposureBreakdown])
async def get_exposure(request: Request) -> ApiEnvelope[ExposureBreakdown]:
    ctx = _context(request)

    def _read_exposure() -> ExposureBreakdown:
        by_asset = [
            AssetExposureBreakdown(
                asset=exp.asset,
                value_usd=exp.value_base,
                pct_of_equity=exp.percentage_of_equity,
            )
            for exp in ctx.portfolio.get_cached_asset_exposure()
        ]

        risk_status = ctx.strategy_engine.get_risk_status()
        exposure_by_strategy = [
            StrategyExposureBreakdown(
                strategy_id=sid,
                value_usd=None,
                pct_of_equity=pct,
            )
            for sid, pct in (risk_status.per_strategy_exposure_pct or {}).items()
        ]
        return ExposureBreakdown(by_asset=by_asset, by_strategy=exposure_by_strategy)

    return await run_bounded_route_read(
        request,
        route_key="portfolio.exposure",
        reader=_read_exposure,
        logger=logger,
        busy_error="Exposure refresh is already in progress.",
        timeout_error="Exposure request timed out.",
        failure_event="exposure_fetch_failed",
    )


@router.get("/trades", response_model=ApiEnvelope[List[Dict[str, Any]]])
async def get_trades(request: Request) -> ApiEnvelope[List[Dict[str, Any]]]:
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

        return ApiEnvelope(data=trades, error=None)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception(
            "Failed to fetch trades",
            extra=build_request_log_extra(request, event="trades_fetch_failed"),
        )
        return ApiEnvelope(data=None, error=str(exc))


@router.post("/snapshot", response_model=ApiEnvelope[Dict[str, Any]])
async def create_snapshot(request: Request) -> ApiEnvelope[Dict[str, Any]]:
    ctx = _context(request)
    if ctx.config.ui.read_only:
        logger.warning(
            "Snapshot blocked: UI in read-only mode",
            extra=build_request_log_extra(request, event="snapshot_blocked"),
        )
        return ApiEnvelope(data=None, error="UI is in read-only mode")

    try:
        snapshot = ctx.portfolio.create_snapshot()
        logger.info(
            "Created manual snapshot",
            extra=build_request_log_extra(
                request, event="snapshot_created", timestamp=snapshot.timestamp
            ),
        )
        data = {
            "timestamp": snapshot.timestamp,
            "equity_usd": snapshot.equity_base,
            "cash_usd": snapshot.cash_base,
            "realized_pnl_usd": snapshot.realized_pnl_base_total,
            "unrealized_pnl_usd": snapshot.unrealized_pnl_base_total,
        }
        return ApiEnvelope(data=data, error=None)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception(
            "Failed to create portfolio snapshot",
            extra=build_request_log_extra(request, event="snapshot_failed"),
        )
        return ApiEnvelope(data=None, error=str(exc))
