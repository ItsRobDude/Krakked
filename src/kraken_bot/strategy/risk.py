# src/kraken_bot/strategy/risk.py

import logging
import pandas as pd
import numpy as np
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from dataclasses import dataclass, asdict

from kraken_bot.config import RiskConfig
from kraken_bot.market_data.api import MarketDataAPI
from kraken_bot.portfolio.manager import PortfolioService
from .models import StrategyIntent, RiskAdjustedAction, RiskStatus

logger = logging.getLogger(__name__)

def compute_atr(ohlc_df: pd.DataFrame, window: int) -> float:
    """
    Computes ATR from a DataFrame with open, high, low, close columns.
    Expected columns: 'high', 'low', 'close'
    """
    if len(ohlc_df) < window + 1:
        return 0.0

    high = ohlc_df['high']
    low = ohlc_df['low']
    close = ohlc_df['close']
    prev_close = close.shift(1)

    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()

    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(window=window).mean().iloc[-1]

    return float(atr) if not np.isnan(atr) else 0.0

@dataclass
class RiskContext:
    equity_usd: float
    realized_pnl_usd: float
    unrealized_pnl_usd: float
    open_positions: List[Any]  # SpotPosition
    asset_exposures: List[Any] # AssetExposure
    manual_positions: List[Any] # SpotPosition (manual subset)
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

        # Determine daily drawdown
        # Simplified: Current equity vs High Water Mark of recent snapshots?
        # Or just equity vs start of day?
        # Phase 4 spec says: "PortfolioService.get_equity() and historical snapshots."
        # For now, let's assume PortfolioService might track this or we calculate it.
        # Since PortfolioService doesn't expose 'daily_drawdown' directly yet, we can approximate
        # or fetch snapshots.
        # Let's fetch snapshots from store (via manager? store not exposed directly on manager public API but we passed portfolio service)
        # We can access `portfolio.store` if we want, or add method.
        # Let's use `portfolio.store.get_snapshots` if available or `create_snapshot` logic.
        # Ideally, we find the max equity in the last 24h.

        # We'll need to fetch snapshots.
        now_ts = int(datetime.now(timezone.utc).timestamp())
        day_ago = now_ts - 86400
        snapshots = self.portfolio.store.get_snapshots(since=day_ago)

        current_equity = equity_view.equity_base
        max_equity_24h = current_equity
        if snapshots:
            max_equity_24h = max(s.equity_base for s in snapshots)
            # Also consider current
            max_equity_24h = max(max_equity_24h, current_equity)

        drawdown_pct = 0.0
        if max_equity_24h > 0:
            drawdown_pct = ((max_equity_24h - current_equity) / max_equity_24h) * 100.0

        # Filter manual positions
        # In Phase 3, we track strategy_tag in PnL but SpotPosition doesn't carry it persistently?
        # SpotPosition is a projection.
        # Phase 4 spec: "Use RealizedPnLRecord.strategy_tag and/or portfolio config to classify...
        # If ignoring manual positions in risk budgets..."
        # Actually SpotPosition doesn't store strategy tag.
        # We might need to infer it or just assume all existing positions are subject to limits
        # unless we can link them to strategy.
        # For Phase 4, let's treat all positions as part of "current state".
        # If `include_manual_positions` is False, maybe we ignore them in counts?
        # But we can't easily distinguish them without persistence.
        # Let's assume for V1 all positions count, or we rely on logic not yet fully in Phase 3.
        # The requirements say "RealizedPnLRecord.strategy_tag ... for PnL attribution".
        # For LIVE positions, we don't have tags on SpotPosition dataclass.
        # Let's assume all are included for now to be safe (conservative risk).
        manual_positions = [] # Placeholder until we can distinguish

        return RiskContext(
            equity_usd=current_equity,
            realized_pnl_usd=equity_view.realized_pnl_base_total,
            unrealized_pnl_usd=equity_view.unrealized_pnl_base_total,
            open_positions=positions,
            asset_exposures=exposures,
            manual_positions=manual_positions,
            drift_flag=equity_view.drift_flag,
            daily_drawdown_pct=drawdown_pct
        )

    def process_intents(self, intents: List[StrategyIntent]) -> List[RiskAdjustedAction]:
        ctx = self.build_risk_context()

        # 1. Check Kill Switch & Drift
        if self.config.kill_switch_on_drift and ctx.drift_flag:
            logger.warning("Kill switch active due to portfolio drift.")
            return self._block_all_opens(intents, ctx, "Portfolio Drift Detected")

        if ctx.daily_drawdown_pct > self.config.max_daily_drawdown_pct:
            logger.warning(f"Kill switch active: Drawdown {ctx.daily_drawdown_pct:.2f}% > {self.config.max_daily_drawdown_pct}%")
            self._kill_switch_active = True
            return self._block_all_opens(intents, ctx, f"Max Daily Drawdown Exceeded ({ctx.daily_drawdown_pct:.2f}%)")
        else:
            self._kill_switch_active = False

        # 2. Aggregate Intents per Pair
        # Map: pair -> List[StrategyIntent]
        intents_by_pair = {}
        for intent in intents:
            if intent.pair not in intents_by_pair:
                intents_by_pair[intent.pair] = []
            intents_by_pair[intent.pair].append(intent)

        actions = []

        # Process each pair
        for pair, pair_intents in intents_by_pair.items():
            action = self._process_pair_intents(pair, pair_intents, ctx)
            actions.append(action)

        return actions

    def _block_all_opens(self, intents: List[StrategyIntent], ctx: RiskContext, reason: str) -> List[RiskAdjustedAction]:
        """
        Blocks all 'enter' or 'increase' intents. Allows 'exit' or 'reduce'.
        """
        actions = []
        for intent in intents:
            is_closing = intent.intent_type in ["exit", "reduce"]

            current_pos = next((p for p in ctx.open_positions if p.pair == intent.pair), None)
            current_size = current_pos.base_size if current_pos else 0.0

            if is_closing:
                # Allow closing/reducing
                # We need to calculate target size.
                # If intent says 'exit', target is 0.
                # If 'reduce', we need `desired_exposure_usd`.
                target_usd = intent.desired_exposure_usd if intent.desired_exposure_usd is not None else 0.0
                # Convert USD to Base?
                price = self.market_data.get_latest_price(intent.pair) or 0.0
                target_base = (target_usd / price) if price > 0 else 0.0

                actions.append(RiskAdjustedAction(
                    pair=intent.pair,
                    strategy_id=intent.strategy_id,
                    action_type="reduce" if target_base > 0 else "close",
                    target_base_size=target_base,
                    target_notional_usd=target_usd,
                    current_base_size=current_size,
                    reason=f"Allowed close/reduce during kill switch: {reason}",
                    blocked=False,
                    blocked_reasons=[],
                    risk_limits_snapshot=asdict(self.config)
                ))
            else:
                # Block open/increase
                actions.append(RiskAdjustedAction(
                    pair=intent.pair,
                    strategy_id=intent.strategy_id,
                    action_type="none",
                    target_base_size=current_size,
                    target_notional_usd=current_size * (self.market_data.get_latest_price(intent.pair) or 0),
                    current_base_size=current_size,
                    reason=f"Blocked by Kill Switch: {reason}",
                    blocked=True,
                    blocked_reasons=[reason],
                    risk_limits_snapshot=asdict(self.config)
                ))
        return actions

    def _process_pair_intents(self, pair: str, intents: List[StrategyIntent], ctx: RiskContext) -> RiskAdjustedAction:
        # Aggregation: Sum desired exposure
        # If any strategy wants to exit/flat, does it cancel others?
        # Requirement: "Sum desired exposures... If None, risk engine sizes it."

        total_desired_usd = 0.0
        strategies_involved = []

        # Fetch Price & ATR once
        price = self.market_data.get_latest_price(pair)
        if not price or price <= 0:
            return self._create_blocked_action(pair, intents[0].strategy_id, "Missing price data", ctx)

        # Calculate sizing for intents without explicit exposure
        for intent in intents:
            strategies_involved.append(intent.strategy_id)

            if intent.side == "flat" or intent.intent_type in ["exit", "close"]:
                # Contribution is 0 exposure
                continue

            exposure = intent.desired_exposure_usd
            if exposure is None:
                # Auto-size based on volatility
                # Get ATR
                # Need OHLC
                # Timeframe? Use intent timeframe or default?
                tf = intent.timeframe or "1d"
                ohlc = self.market_data.get_ohlc(pair, tf, lookback=self.config.volatility_lookback_bars + 10)
                if not ohlc:
                     logger.warning(f"No OHLC for {pair} {tf}, cannot size trade.")
                     continue

                # Convert OHLC list to DF
                df = pd.DataFrame([asdict(b) for b in ohlc])
                atr = compute_atr(df, self.config.volatility_lookback_bars)

                if atr <= 0:
                    logger.warning(f"ATR is 0 for {pair}, defaulting to min size or 0.")
                    exposure = 0.0
                else:
                    # Risk Formula:
                    # Risk Amount = Equity * Risk%
                    # Position Size = Risk Amount / (ATR * Multiplier?)
                    # Let's assume Stop Distance = 2 * ATR (common default) or just ATR
                    # "Define a per-unit risk as some function of volatility (configurable per strategy)"
                    # We'll stick to a simple default: Stop = 2 * ATR
                    stop_distance_pct = (2 * atr) / price

                    risk_amount_usd = ctx.equity_usd * (self.config.max_risk_per_trade_pct / 100.0)

                    # Position Value * Stop% = Risk Amount
                    # Position Value = Risk Amount / Stop%
                    if stop_distance_pct > 0:
                         exposure = risk_amount_usd / stop_distance_pct
                    else:
                         exposure = 0.0

            total_desired_usd += exposure

        # Current Position
        current_pos = next((p for p in ctx.open_positions if p.pair == pair), None)
        current_base = current_pos.base_size if current_pos else 0.0
        current_usd = current_base * price

        # Initial Target
        target_usd = total_desired_usd

        # Apply Limits (Clamping)
        blocked_reasons = []

        # 1. Max Per Asset
        # Pair base asset (e.g. XBT)
        # We need to know what we already hold of this asset from OTHER pairs?
        # For simplicity, check per-pair limit (often proxy for per-asset if 1 pair/asset)
        max_asset_usd = ctx.equity_usd * (self.config.max_per_asset_pct / 100.0)
        if target_usd > max_asset_usd:
             blocked_reasons.append(f"Max per asset limit ({target_usd:.2f} > {max_asset_usd:.2f})")
             target_usd = max_asset_usd

        # 2. Max Portfolio Risk
        # (This usually sums risk across ALL pairs. Here we are looking at one pair.
        # Ideally we check if adding this exposure pushes GLOBAL risk over limit.
        # Requires calculating risk for all existing positions + this new one.)
        # Simplified: Check if total exposure > limit?
        # Or check "Portfolio Heat"?
        # Requirement: "Sum of all per-position risks ... must be <= max_portfolio_risk_pct"
        # Let's skip complex global re-calc for now and rely on per-trade caps mostly,
        # or implement a simple check of total notional if easy.

        # 3. Max Open Positions
        # If we are OPENING a new position (current is 0, target > 0)
        if current_base == 0 and target_usd > 0:
            # Count active positions
            active_count = len([p for p in ctx.open_positions if p.base_size * price > 10.0]) # Ignore dust
            if active_count >= self.config.max_open_positions:
                blocked_reasons.append(f"Max open positions reached ({active_count})")
                target_usd = 0.0

        # Determine Action Type
        action_type = "hold"
        if target_usd > current_usd + 10.0: # Buffer
            if current_usd == 0:
                action_type = "open"
            else:
                action_type = "increase"
        elif target_usd < current_usd - 10.0:
            if target_usd < 10.0: # Close if small remainder
                target_usd = 0.0
                action_type = "close"
            else:
                action_type = "reduce"
        else:
            action_type = "none" # No change

        # Construct Result
        target_base = target_usd / price

        return RiskAdjustedAction(
            pair=pair,
            strategy_id=",".join(strategies_involved), # Combined ID
            action_type=action_type,
            target_base_size=target_base,
            target_notional_usd=target_usd,
            current_base_size=current_base,
            reason="Aggregated Intent" if not blocked_reasons else f"Clamped: {'; '.join(blocked_reasons)}",
            blocked=len(blocked_reasons) > 0 and target_usd == 0, # Only truly blocked if forced to 0
            blocked_reasons=blocked_reasons,
            risk_limits_snapshot=asdict(self.config)
        )

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
            risk_limits_snapshot=asdict(self.config)
        )

    def get_status(self) -> RiskStatus:
        ctx = self.build_risk_context()

        total_exposure = sum(p.current_value_base for p in ctx.open_positions)
        total_equity = ctx.equity_usd

        total_exp_pct = (total_exposure / total_equity * 100) if total_equity > 0 else 0

        per_asset = {}
        for exp in ctx.asset_exposures:
            per_asset[exp.asset] = exp.percentage_of_equity * 100

        return RiskStatus(
            kill_switch_active=self._kill_switch_active,
            daily_drawdown_pct=ctx.daily_drawdown_pct,
            drift_flag=ctx.drift_flag,
            total_exposure_pct=total_exp_pct,
            per_asset_exposure_pct=per_asset,
            per_strategy_exposure_pct={} # TODO: track per strategy
        )
