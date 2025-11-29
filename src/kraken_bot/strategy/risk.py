"""Risk engine that sizes intents and enforces portfolio limits."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from kraken_bot.config import RiskConfig
from kraken_bot.market_data.api import MarketDataAPI
from kraken_bot.portfolio.manager import PortfolioService
from .models import RiskAdjustedAction, RiskStatus, StrategyIntent

logger = logging.getLogger(__name__)


def compute_atr(ohlc_df: pd.DataFrame, window: int) -> float:
    """Compute the Average True Range for a dataframe with OHLC columns."""
    if len(ohlc_df) < window + 1:
        return 0.0

    high = ohlc_df["high"]
    low = ohlc_df["low"]
    close = ohlc_df["close"]
    prev_close = close.shift(1)

    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    atr = tr.rolling(window=window).mean().iloc[-1]
    return float(atr) if not np.isnan(atr) else 0.0


@dataclass
class RiskContext:
    equity_usd: float
    realized_pnl_usd: float
    unrealized_pnl_usd: float
    total_exposure_usd: float
    total_exposure_pct: float
    manual_exposure_usd: float
    manual_exposure_pct: float
    per_strategy_exposure_usd: Dict[str, float]
    per_strategy_exposure_pct: Dict[str, float]
    open_positions: List[Any]
    asset_exposures: List[Any]
    manual_positions: List[Any]
    manual_positions_included: bool
    drift_flag: bool
    daily_drawdown_pct: float


class RiskEngine:
    def __init__(
        self,
        config: RiskConfig,
        market_data: MarketDataAPI,
        portfolio: PortfolioService,
        strategy_userrefs: Optional[Dict[str, Optional[int]]] = None,
        strategy_tags: Optional[Dict[str, Optional[str]]] = None,
    ):
        self.config = config
        self.market_data = market_data
        self.portfolio = portfolio
        self.strategy_userrefs = strategy_userrefs or {}
        self.strategy_tags = strategy_tags or {}
        self._kill_switch_active = False
        self._manual_kill_switch_active = False

    def set_manual_kill_switch(self, active: bool) -> None:
        self._manual_kill_switch_active = active

    def clear_manual_kill_switch(self) -> None:
        self._manual_kill_switch_active = False

    def build_risk_context(self) -> RiskContext:
        include_manual_for_strategies = self.config.include_manual_positions
        equity_view = self.portfolio.get_equity(include_manual=True)
        positions = self.portfolio.get_positions()
        exposures = self.portfolio.get_asset_exposure(include_manual=True)

        manual_positions: List[Any] = []
        included_positions: List[Any] = []
        for pos in positions:
            price = self.market_data.get_latest_price(pos.pair)
            pos.current_value_base = (pos.base_size * price) if price else 0.0
            if self._is_manual_position(pos):
                manual_positions.append(pos)
                if not include_manual_for_strategies:
                    continue
            included_positions.append(pos)

        total_exposure_usd = sum(pos.current_value_base for pos in positions)
        total_exposure_pct = (total_exposure_usd / equity_view.equity_base * 100.0) if equity_view.equity_base else 0.0

        manual_exposure_usd = sum(pos.current_value_base for pos in manual_positions)
        manual_exposure_pct = (manual_exposure_usd / equity_view.equity_base * 100.0) if equity_view.equity_base else 0.0

        per_strategy_exposure_usd: Dict[str, float] = {}
        per_strategy_exposure_pct: Dict[str, float] = {}
        for pos in included_positions:
            strategy_key = pos.strategy_tag or "manual"
            per_strategy_exposure_usd[strategy_key] = per_strategy_exposure_usd.get(strategy_key, 0.0) + pos.current_value_base

        if equity_view.equity_base:
            for strategy_key, usd in per_strategy_exposure_usd.items():
                per_strategy_exposure_pct[strategy_key] = (usd / equity_view.equity_base) * 100.0

        now_ts = int(datetime.now(timezone.utc).timestamp())
        day_ago = now_ts - 86400
        snapshots = self.portfolio.store.get_snapshots(since=day_ago)

        current_equity = equity_view.equity_base
        max_equity_24h = max([current_equity] + [s.equity_base for s in snapshots]) if snapshots else current_equity

        drawdown_pct = 0.0
        if max_equity_24h > 0:
            drawdown_pct = ((max_equity_24h - current_equity) / max_equity_24h) * 100.0

        return RiskContext(
            equity_usd=current_equity,
            realized_pnl_usd=equity_view.realized_pnl_base_total,
            unrealized_pnl_usd=equity_view.unrealized_pnl_base_total,
            total_exposure_usd=total_exposure_usd,
            total_exposure_pct=total_exposure_pct,
            manual_exposure_usd=manual_exposure_usd,
            manual_exposure_pct=manual_exposure_pct,
            per_strategy_exposure_usd=per_strategy_exposure_usd,
            per_strategy_exposure_pct=per_strategy_exposure_pct,
            open_positions=positions,
            asset_exposures=exposures,
            manual_positions=manual_positions,
            manual_positions_included=include_manual_for_strategies,
            drift_flag=equity_view.drift_flag,
            daily_drawdown_pct=drawdown_pct,
        )

    def process_intents(self, intents: List[StrategyIntent]) -> List[RiskAdjustedAction]:
        ctx = self.build_risk_context()

        kill_switch_reasons: List[str] = []
        kill_switch_active = self._manual_kill_switch_active
        if self._manual_kill_switch_active:
            kill_switch_reasons.append("Manual Kill Switch")

        if self.config.kill_switch_on_drift and ctx.drift_flag:
            logger.warning("Kill switch active due to portfolio drift.")
            kill_switch_active = True
            kill_switch_reasons.append("Portfolio Drift Detected")

        if ctx.daily_drawdown_pct > self.config.max_daily_drawdown_pct:
            logger.warning(
                "Kill switch active: Drawdown %.2f%% > %.2f%%",
                ctx.daily_drawdown_pct,
                self.config.max_daily_drawdown_pct,
            )
            kill_switch_active = True
            kill_switch_reasons.append(
                f"Max Daily Drawdown Exceeded ({ctx.daily_drawdown_pct:.2f}%)"
            )

        self._kill_switch_active = kill_switch_active
        if kill_switch_active:
            reason = "; ".join(kill_switch_reasons) if kill_switch_reasons else "Kill Switch Active"
            return self._block_all_opens(intents, ctx, reason)

        intents_by_pair: Dict[str, List[StrategyIntent]] = {}
        for intent in intents:
            intents_by_pair.setdefault(intent.pair, []).append(intent)

        actions = [self._process_pair_intents(pair, pair_intents, ctx) for pair, pair_intents in intents_by_pair.items()]
        return actions

    def _resolve_userref(self, strategies: List[str]) -> Optional[int]:
        unique_strategies = {sid for sid in strategies if sid}
        if len(unique_strategies) != 1:
            return None
        strategy_id = next(iter(unique_strategies))
        return self.strategy_userrefs.get(strategy_id)

    def _resolve_strategy_tag(self, strategies: List[str]) -> Optional[str]:
        unique_strategies = {sid for sid in strategies if sid}
        if len(unique_strategies) != 1:
            return None
        strategy_id = next(iter(unique_strategies))
        return self.strategy_tags.get(strategy_id) or strategy_id

    def _block_all_opens(self, intents: List[StrategyIntent], ctx: RiskContext, reason: str) -> List[RiskAdjustedAction]:
        actions: List[RiskAdjustedAction] = []
        for intent in intents:
            current_pos = next((p for p in ctx.open_positions if p.pair == intent.pair), None)
            current_size = current_pos.base_size if current_pos else 0.0
            price = self.market_data.get_latest_price(intent.pair) or 0.0

            if intent.intent_type in ["exit", "reduce"]:
                target_usd = intent.desired_exposure_usd if intent.desired_exposure_usd is not None else 0.0
                target_base = (target_usd / price) if price > 0 else 0.0
                actions.append(
                    RiskAdjustedAction(
                        pair=intent.pair,
                        strategy_id=intent.strategy_id,
                        strategy_tag=self._resolve_strategy_tag([intent.strategy_id]),
                        userref=self._resolve_userref([intent.strategy_id]),
                        action_type="reduce" if target_base > 0 else "close",
                        target_base_size=target_base,
                        target_notional_usd=target_usd,
                        current_base_size=current_size,
                        reason=f"Allowed close/reduce during kill switch: {reason}",
                        blocked=False,
                        blocked_reasons=[],
                        risk_limits_snapshot=asdict(self.config),
                    )
                )
                continue

            actions.append(
                RiskAdjustedAction(
                    pair=intent.pair,
                    strategy_id=intent.strategy_id,
                    strategy_tag=self._resolve_strategy_tag([intent.strategy_id]),
                    userref=self._resolve_userref([intent.strategy_id]),
                    action_type="none",
                    target_base_size=current_size,
                    target_notional_usd=current_size * price,
                    current_base_size=current_size,
                    reason=f"Blocked by Kill Switch: {reason}",
                    blocked=True,
                    blocked_reasons=[reason],
                    risk_limits_snapshot=asdict(self.config),
                )
            )
        return actions

    def _process_pair_intents(self, pair: str, intents: List[StrategyIntent], ctx: RiskContext) -> RiskAdjustedAction:
        price = self.market_data.get_latest_price(pair)
        if not price or price <= 0:
            return self._create_blocked_action(pair, intents[0].strategy_id, "Missing price data", ctx)

        blocked_reasons: List[str] = []
        liquidity_24h = self._get_pair_liquidity(pair)

        target_usd_by_strategy: Dict[str, float] = {}
        strategies_involved: List[str] = []

        for intent in intents:
            strategies_involved.append(intent.strategy_id)
            if intent.side == "flat" or intent.intent_type in ["exit", "close"]:
                target_usd_by_strategy[intent.strategy_id] = 0.0
                continue

            exposure = intent.desired_exposure_usd
            if exposure is None:
                exposure = self._size_by_volatility(pair, intent.timeframe, price, ctx)
            target_usd_by_strategy[intent.strategy_id] = exposure or 0.0

        current_by_strategy: Dict[str, float] = {}
        pair_positions = [p for p in ctx.open_positions if p.pair == pair]
        for pos in pair_positions:
            strategy_key = pos.strategy_tag or "manual"
            current_by_strategy[strategy_key] = current_by_strategy.get(strategy_key, 0.0) + (pos.base_size * price)

        manual_current = current_by_strategy.get("manual", 0.0)
        if manual_current > 0 and "manual" not in target_usd_by_strategy:
            target_usd_by_strategy["manual"] = manual_current

        current_base = sum(p.base_size for p in pair_positions)
        current_usd = current_base * price

        liquidity_threshold = self.config.min_liquidity_24h_usd
        total_target_usd = sum(target_usd_by_strategy.values())
        if (
            liquidity_threshold
            and liquidity_24h is not None
            and total_target_usd > current_usd
            and liquidity_24h < liquidity_threshold
        ):
            blocked_reasons.append(
                f"Below min_liquidity_24h_usd ({liquidity_24h:,.2f} < {liquidity_threshold:,.2f})"
            )
            for key in list(target_usd_by_strategy.keys()):
                target_usd_by_strategy[key] = min(
                    target_usd_by_strategy[key], current_by_strategy.get(key, 0.0)
                )

        adjusted_targets, limit_reasons = self._apply_limits(target_usd_by_strategy, current_by_strategy, ctx)
        blocked_reasons.extend(limit_reasons)
        target_usd = sum(adjusted_targets.values())

        action_type = "none"
        if target_usd > current_usd + 10.0:
            action_type = "open" if current_usd == 0 else "increase"
        elif target_usd < current_usd - 10.0:
            if target_usd < 10.0:
                target_usd = 0.0
                action_type = "close"
            else:
                action_type = "reduce"
        else:
            action_type = "none"

        target_base = target_usd / price if price else 0.0
        return RiskAdjustedAction(
            pair=pair,
            strategy_id=",".join(strategies_involved),
            strategy_tag=self._resolve_strategy_tag(strategies_involved),
            userref=self._resolve_userref(strategies_involved),
            action_type=action_type,
            target_base_size=target_base,
            target_notional_usd=target_usd,
            current_base_size=current_base,
            reason="Aggregated Intent" if not blocked_reasons else f"Clamped: {'; '.join(blocked_reasons)}",
            blocked=bool(blocked_reasons) and target_usd == 0,
            blocked_reasons=blocked_reasons,
            risk_limits_snapshot=asdict(self.config),
        )

    def _apply_limits(
        self, target_by_strategy: Dict[str, float], current_by_strategy: Dict[str, float], ctx: RiskContext
    ) -> tuple[Dict[str, float], List[str]]:
        blocked_reasons: List[str] = []

        def clamp_total(available_total: float, reason: str, targets: Dict[str, float]) -> Dict[str, float]:
            manual_target = targets.get("manual", 0.0)
            non_manual_keys = [k for k in targets.keys() if k != "manual"]
            non_manual_total = sum(targets[k] for k in non_manual_keys)

            if available_total < manual_target:
                for k in non_manual_keys:
                    targets[k] = 0.0
                blocked_reasons.append(reason)
                return targets

            remaining_for_strategies = available_total - manual_target
            if non_manual_total <= remaining_for_strategies or non_manual_total == 0:
                blocked_reasons.append(reason)
                return targets

            scale = remaining_for_strategies / non_manual_total if non_manual_total > 0 else 0.0
            for k in non_manual_keys:
                targets[k] *= scale
            blocked_reasons.append(reason)
            return targets

        target_by_strategy = target_by_strategy.copy()
        total_target_usd = sum(target_by_strategy.values())
        current_usd = sum(current_by_strategy.values())

        for strategy_id, pct_limit in self.config.max_per_strategy_pct.items():
            if strategy_id == "manual" and not ctx.manual_positions_included:
                continue

            if strategy_id not in target_by_strategy:
                continue

            allowed_usd = ctx.equity_usd * (pct_limit / 100.0)
            current_total_for_strategy = ctx.per_strategy_exposure_usd.get(strategy_id, 0.0)
            current_pair_usd = current_by_strategy.get(strategy_id, 0.0)
            projected_total = current_total_for_strategy - current_pair_usd + target_by_strategy[strategy_id]

            if projected_total > allowed_usd:
                available = max(allowed_usd - (current_total_for_strategy - current_pair_usd), 0.0)
                reason = (
                    f"Strategy {strategy_id} budget exceeded "
                    f"({projected_total:.2f} > {allowed_usd:.2f})"
                )
                target_by_strategy[strategy_id] = min(target_by_strategy[strategy_id], available)
                blocked_reasons.append(reason)

        total_target_usd = sum(target_by_strategy.values())

        max_asset_usd = ctx.equity_usd * (self.config.max_per_asset_pct / 100.0)
        projected_total = ctx.total_exposure_usd - current_usd + total_target_usd
        if projected_total > max_asset_usd:
            available = max(max_asset_usd - (ctx.total_exposure_usd - current_usd), 0.0)
            reason = f"Max per asset limit ({projected_total:.2f} > {max_asset_usd:.2f})"
            target_by_strategy = clamp_total(available, reason, target_by_strategy)
            total_target_usd = sum(target_by_strategy.values())

        portfolio_limit_usd = ctx.equity_usd * (self.config.max_portfolio_risk_pct / 100.0)
        projected_total = ctx.total_exposure_usd - current_usd + total_target_usd
        if projected_total > portfolio_limit_usd:
            available = max(portfolio_limit_usd - (ctx.total_exposure_usd - current_usd), 0.0)
            reason = (
                "Max portfolio exposure limit "
                f"({projected_total:.2f} > {portfolio_limit_usd:.2f})"
            )
            target_by_strategy = clamp_total(available, reason, target_by_strategy)
            total_target_usd = sum(target_by_strategy.values())

        if ctx.open_positions:
            active_count = len(
                [p for p in ctx.open_positions if p.base_size * self.market_data.get_latest_price(p.pair) > 10.0]
            )
        else:
            active_count = 0

        if active_count >= self.config.max_open_positions and total_target_usd > current_usd:
            blocked_reasons.append(f"Max open positions reached ({active_count})")
            for key in list(target_by_strategy.keys()):
                if key != "manual":
                    target_by_strategy[key] = min(target_by_strategy[key], current_by_strategy.get(key, 0.0))

        return target_by_strategy, blocked_reasons

    @staticmethod
    def _is_manual_position(position: Any) -> bool:
        strategy_tag: Optional[str] = getattr(position, "strategy_tag", None)
        return not strategy_tag or strategy_tag == "manual"

    def _get_pair_liquidity(self, pair: str) -> Optional[float]:
        try:
            metadata = self.market_data.get_pair_metadata(pair)
            liquidity: Optional[float] = getattr(metadata, "liquidity_24h_usd", None)
            return liquidity
        except Exception as exc:  # noqa: BLE001
            logger.warning("Unable to fetch liquidity for %s: %s", pair, exc)
            return None

    def _size_by_volatility(self, pair: str, timeframe: str, price: float, ctx: RiskContext) -> float:
        tf = timeframe or "1d"
        ohlc = self.market_data.get_ohlc(pair, tf, lookback=self.config.volatility_lookback_bars + 10)
        if not ohlc:
            logger.warning("No OHLC for %s %s, cannot size trade.", pair, tf)
            return 0.0

        df = pd.DataFrame([asdict(b) for b in ohlc])
        atr = compute_atr(df, self.config.volatility_lookback_bars)
        if atr <= 0:
            logger.warning("ATR is 0 for %s, defaulting to 0 exposure.", pair)
            return 0.0

        stop_distance_pct = (2 * atr) / price
        risk_amount_usd = ctx.equity_usd * (self.config.max_risk_per_trade_pct / 100.0)
        if stop_distance_pct <= 0:
            return 0.0
        return risk_amount_usd / stop_distance_pct

    def _create_blocked_action(self, pair: str, strategy_id: str, reason: str, ctx: RiskContext) -> RiskAdjustedAction:
        current_pos = next((p for p in ctx.open_positions if p.pair == pair), None)
        current_base = current_pos.base_size if current_pos else 0.0
        return RiskAdjustedAction(
            pair=pair,
            strategy_id=strategy_id,
            strategy_tag=self._resolve_strategy_tag([strategy_id]),
            userref=self._resolve_userref([strategy_id]),
            action_type="none",
            target_base_size=current_base,
            target_notional_usd=0.0,
            current_base_size=current_base,
            reason=reason,
            blocked=True,
            blocked_reasons=[reason],
            risk_limits_snapshot=asdict(self.config),
        )

    def get_status(self) -> RiskStatus:
        ctx = self.build_risk_context()
        per_asset: Dict[str, float] = {}
        for exp in ctx.asset_exposures:
            per_asset[exp.asset] = exp.percentage_of_equity * 100

        return RiskStatus(
            kill_switch_active=self._kill_switch_active or self._manual_kill_switch_active,
            daily_drawdown_pct=ctx.daily_drawdown_pct,
            drift_flag=ctx.drift_flag,
            total_exposure_pct=ctx.total_exposure_pct,
            manual_exposure_pct=ctx.manual_exposure_pct,
            per_asset_exposure_pct=per_asset,
            per_strategy_exposure_pct=ctx.per_strategy_exposure_pct,
        )
