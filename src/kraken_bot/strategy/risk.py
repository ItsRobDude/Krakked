"""Risk engine that sizes intents and enforces portfolio limits."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List

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
    open_positions: List[Any]
    asset_exposures: List[Any]
    manual_positions: List[Any]
    drift_flag: bool
    daily_drawdown_pct: float


class RiskEngine:
    def __init__(self, config: RiskConfig, market_data: MarketDataAPI, portfolio: PortfolioService):
        self.config = config
        self.market_data = market_data
        self.portfolio = portfolio
        self._kill_switch_active = False

    def build_risk_context(self) -> RiskContext:
        equity_view = self.portfolio.get_equity()
        positions = self.portfolio.get_positions()
        exposures = self.portfolio.get_asset_exposure()

        now_ts = int(datetime.now(timezone.utc).timestamp())
        day_ago = now_ts - 86400
        snapshots = self.portfolio.store.get_snapshots(since=day_ago)

        current_equity = equity_view.equity_base
        max_equity_24h = max([current_equity] + [s.equity_base for s in snapshots]) if snapshots else current_equity

        drawdown_pct = 0.0
        if max_equity_24h > 0:
            drawdown_pct = ((max_equity_24h - current_equity) / max_equity_24h) * 100.0

        manual_positions: List[Any] = []
        return RiskContext(
            equity_usd=current_equity,
            realized_pnl_usd=equity_view.realized_pnl_base_total,
            unrealized_pnl_usd=equity_view.unrealized_pnl_base_total,
            open_positions=positions,
            asset_exposures=exposures,
            manual_positions=manual_positions,
            drift_flag=equity_view.drift_flag,
            daily_drawdown_pct=drawdown_pct,
        )

    def process_intents(self, intents: List[StrategyIntent]) -> List[RiskAdjustedAction]:
        ctx = self.build_risk_context()

        if self.config.kill_switch_on_drift and ctx.drift_flag:
            logger.warning("Kill switch active due to portfolio drift.")
            self._kill_switch_active = True
            return self._block_all_opens(intents, ctx, "Portfolio Drift Detected")

        if ctx.daily_drawdown_pct > self.config.max_daily_drawdown_pct:
            logger.warning(
                "Kill switch active: Drawdown %.2f%% > %.2f%%",
                ctx.daily_drawdown_pct,
                self.config.max_daily_drawdown_pct,
            )
            self._kill_switch_active = True
            return self._block_all_opens(intents, ctx, f"Max Daily Drawdown Exceeded ({ctx.daily_drawdown_pct:.2f}%)")

        self._kill_switch_active = False

        intents_by_pair: Dict[str, List[StrategyIntent]] = {}
        for intent in intents:
            intents_by_pair.setdefault(intent.pair, []).append(intent)

        actions = [self._process_pair_intents(pair, pair_intents, ctx) for pair, pair_intents in intents_by_pair.items()]
        return actions

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

        total_desired_usd = 0.0
        strategies_involved: List[str] = []

        for intent in intents:
            strategies_involved.append(intent.strategy_id)
            if intent.side == "flat" or intent.intent_type in ["exit", "close"]:
                continue

            exposure = intent.desired_exposure_usd
            if exposure is None:
                exposure = self._size_by_volatility(pair, intent.timeframe, price, ctx)
            total_desired_usd += exposure or 0.0

        current_pos = next((p for p in ctx.open_positions if p.pair == pair), None)
        current_base = current_pos.base_size if current_pos else 0.0
        current_usd = current_base * price

        target_usd, blocked_reasons = self._apply_limits(total_desired_usd, ctx)

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
            action_type=action_type,
            target_base_size=target_base,
            target_notional_usd=target_usd,
            current_base_size=current_base,
            reason="Aggregated Intent" if not blocked_reasons else f"Clamped: {'; '.join(blocked_reasons)}",
            blocked=bool(blocked_reasons) and target_usd == 0,
            blocked_reasons=blocked_reasons,
            risk_limits_snapshot=asdict(self.config),
        )

    def _apply_limits(self, target_usd: float, ctx: RiskContext) -> tuple[float, List[str]]:
        blocked_reasons: List[str] = []
        max_asset_usd = ctx.equity_usd * (self.config.max_per_asset_pct / 100.0)
        if target_usd > max_asset_usd:
            blocked_reasons.append(f"Max per asset limit ({target_usd:.2f} > {max_asset_usd:.2f})")
            target_usd = max_asset_usd

        if ctx.open_positions:
            active_count = len([p for p in ctx.open_positions if p.base_size * self.market_data.get_latest_price(p.pair) > 10.0])
        else:
            active_count = 0

        if active_count >= self.config.max_open_positions and target_usd > 0:
            blocked_reasons.append(f"Max open positions reached ({active_count})")
            target_usd = 0.0

        return target_usd, blocked_reasons

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
        total_exposure = sum(p.current_value_base for p in ctx.open_positions)
        total_equity = ctx.equity_usd
        total_exp_pct = (total_exposure / total_equity * 100) if total_equity > 0 else 0.0

        per_asset: Dict[str, float] = {}
        for exp in ctx.asset_exposures:
            per_asset[exp.asset] = exp.percentage_of_equity * 100

        return RiskStatus(
            kill_switch_active=self._kill_switch_active,
            daily_drawdown_pct=ctx.daily_drawdown_pct,
            drift_flag=ctx.drift_flag,
            total_exposure_pct=total_exp_pct,
            per_asset_exposure_pct=per_asset,
            per_strategy_exposure_pct={},
        )
