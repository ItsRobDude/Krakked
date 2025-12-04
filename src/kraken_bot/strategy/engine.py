"""Strategy orchestration and risk routing."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Type

from kraken_bot.config import AppConfig, StrategyConfig
from kraken_bot.logging_config import structured_log_extra
from kraken_bot.market_data.api import MarketDataAPI
from kraken_bot.market_data.exceptions import DataStaleError
from kraken_bot.portfolio.manager import PortfolioService
from kraken_bot.strategy.regime import RegimeSnapshot, infer_regime

from .allocator import StrategyWeights, compute_weights
from .base import Strategy, StrategyContext
from .models import (
    DecisionRecord,
    ExecutionPlan,
    RiskAdjustedAction,
    RiskStatus,
    StrategyIntent,
    StrategyState,
)
from .risk import RiskEngine
from .strategies.dca_rebalance import DcaRebalanceStrategy
from .strategies.demo_strategy import TrendFollowingStrategy
from .strategies.ml_strategy import AIPredictorStrategy
from .strategies.mean_reversion import MeanReversionStrategy
from .strategies.relative_strength import RelativeStrengthStrategy
from .strategies.vol_breakout import VolBreakoutStrategy

logger = logging.getLogger(__name__)


def _strategy_registry() -> Dict[str, Type[Strategy]]:
    """Return a mapping of strategy type identifiers to implementations."""
    return {
        "trend_following": TrendFollowingStrategy,
        "dca_rebalance": DcaRebalanceStrategy,
        "mean_reversion": MeanReversionStrategy,
        "vol_breakout": VolBreakoutStrategy,
        "relative_strength": RelativeStrengthStrategy,
        "machine_learning": AIPredictorStrategy,
    }


class StrategyEngine:
    """Loads configured strategies, routes intents through risk, and persists plans."""

    def __init__(
        self, config: AppConfig, market_data: MarketDataAPI, portfolio: PortfolioService
    ):
        self.config = config
        self.market_data = market_data
        self.portfolio = portfolio
        strategy_userrefs = {
            cfg.name: str(cfg.userref) if cfg.userref is not None else None
            for cfg in config.strategies.configs.values()
        }
        self.risk_engine = RiskEngine(
            config.risk,
            market_data,
            portfolio,
            strategy_userrefs=strategy_userrefs,
            strategy_tags={
                cfg.name: cfg.name for cfg in config.strategies.configs.values()
            },
        )

        self.strategies: Dict[str, Strategy] = {}
        self.strategy_states: Dict[str, StrategyState] = {}

    def initialize(self) -> None:
        logger.info(
            "Initializing StrategyEngine...",
            extra=structured_log_extra(event="strategy_engine_init"),
        )
        registry = _strategy_registry()

        for config_key, strat_cfg in self.config.strategies.configs.items():
            strategy_id = strat_cfg.name
            if strategy_id != config_key:
                logger.warning(
                    "Strategy config key %s does not match declared name %s; using declared name",
                    config_key,
                    strategy_id,
                    extra=structured_log_extra(
                        event="strategy_key_mismatch",
                        strategy_key=config_key,
                        strategy_id=strategy_id,
                    ),
                )

            if not strat_cfg.enabled:
                logger.info(
                    "Skipping disabled strategy %s",
                    strategy_id,
                    extra=structured_log_extra(
                        event="strategy_disabled_skip", strategy_id=strategy_id
                    ),
                )
                continue

            if strategy_id not in self.config.strategies.enabled:
                logger.info(
                    "Strategy %s not in enabled list, skipping",
                    strategy_id,
                    extra=structured_log_extra(
                        event="strategy_not_enabled", strategy_id=strategy_id
                    ),
                )
                continue

            strat_class = registry.get(strat_cfg.type)
            if not strat_class:
                logger.warning(
                    "Unknown strategy type: %s for %s",
                    strat_cfg.type,
                    strategy_id,
                    extra=structured_log_extra(
                        event="strategy_unknown_type", strategy_id=strategy_id
                    ),
                )
                continue

            strategy = strat_class(strat_cfg)
            self.strategies[strategy_id] = strategy
            self.strategy_states[strategy_id] = StrategyState(
                strategy_id=strategy_id,
                enabled=True,
                last_intents_at=None,
                last_actions_at=None,
                current_positions=[],
                pnl_summary={},
                last_intents=None,
                params=dict(strat_cfg.params),
            )

            try:
                strategy.warmup(self.market_data, self.portfolio)
            except Exception as exc:  # pragma: no cover - defensive
                logger.error(
                    "Error warming up strategy %s: %s",
                    strategy_id,
                    exc,
                    extra=structured_log_extra(
                        event="strategy_warmup_error", strategy_id=strategy_id
                    ),
                )

        logger.info(
            "StrategyEngine initialized with %d strategies",
            len(self.strategies),
            extra=structured_log_extra(
                event="strategy_engine_ready",
                strategy_id="all",
                count=len(self.strategies),
            ),
        )

    def _score_intent(
        self,
        intent: StrategyIntent,
        weights: Optional[StrategyWeights],
    ) -> float:
        """
        Compute a decision score for an intent based on:
        - Strategy-level weight (dynamic allocation)
        - Per-intent confidence
        """

        base = intent.confidence

        if not weights:
            return base

        weight_pct = weights.per_strategy_pct.get(intent.strategy_id)
        if weight_pct is None:
            weight_factor = 1.0
        else:
            weight_factor = weight_pct / 100.0

        return base * weight_factor

    def run_cycle(self, now: Optional[datetime] = None) -> ExecutionPlan:
        """Run a full decision cycle and persist the resulting execution plan."""
        now = now or datetime.now(timezone.utc)
        plan_id = f"plan_{int(now.timestamp())}"
        logger.info(
            "Starting decision cycle %s",
            plan_id,
            extra=structured_log_extra(event="strategy_cycle", plan_id=plan_id),
        )

        if not self._data_ready():
            return ExecutionPlan(
                plan_id=plan_id,
                generated_at=now,
                actions=[],
                metadata={"error": "Market data unavailable"},
            )

        # Use the dynamically discovered universe (all USD spot pairs that
        # passed the US_CA + liquidity filters) for regime inference.
        universe_pairs = self.market_data.get_universe()
        if not universe_pairs:
            # Fallback: if for some reason discovery failed, fall back to the
            # static config list so we don't explode.
            universe_pairs = list(self.config.universe.include_pairs)

        regime = infer_regime(self.market_data, list(universe_pairs))

        weights = self._compute_strategy_weights(regime)
        all_intents: List[StrategyIntent] = []
        intent_summaries: Dict[str, List[Dict[str, Any]]] = {}
        for name, strategy in self.strategies.items():
            configured_timeframes = strategy.config.params.get("timeframes")
            if isinstance(configured_timeframes, (list, tuple)):
                timeframes = list(configured_timeframes)
            elif configured_timeframes is not None:
                timeframes = [configured_timeframes]
            else:
                single_timeframe = strategy.config.params.get("timeframe")
                timeframes = [single_timeframe] if single_timeframe else ["1h"]

            for timeframe in timeframes:
                context = self._build_context(now, strategy.config, timeframe, regime)
                try:
                    intents = strategy.generate_intents(context)
                    for intent in intents:
                        intent.strategy_id = name
                        intent.metadata = intent.metadata or {}
                        intent.metadata.setdefault("strategy_id", name)
                        intent.metadata.setdefault("timeframe", timeframe)
                        if weights:
                            weight_hint = weights.per_strategy_pct.get(name)
                            if weight_hint is not None:
                                intent.metadata.setdefault("weight_hint_pct", weight_hint)
                        if strategy.config.userref is not None:
                            intent.metadata.setdefault(
                                "userref", str(strategy.config.userref)
                            )
                        summary = intent_summaries.setdefault(name, [])
                        if len(summary) < 10:
                            summary.append(
                                {
                                    "pair": intent.pair,
                                    "side": intent.side,
                                    "intent_type": intent.intent_type,
                                    "desired_exposure_usd": intent.desired_exposure_usd,
                                    "confidence": intent.confidence,
                                    "timeframe": timeframe,
                                }
                            )
                    all_intents.extend(intents)
                    self.strategy_states[name].last_intents_at = now
                except DataStaleError as exc:
                    logger.warning(
                        (
                            "Stale market data for %s on timeframe %s (pair %s); "
                            "skipping this context and continuing."
                        ),
                        name,
                        timeframe,
                        exc.pair,
                        extra=structured_log_extra(
                            event="data_stale",
                            strategy_id=name,
                            plan_id=plan_id,
                            pair=exc.pair,
                            timeframe=timeframe,
                        ),
                    )
                    continue
                except Exception as exc:  # pragma: no cover - defensive
                    logger.error(
                        "Error generating intents for %s on timeframe %s: %s",
                        name,
                        timeframe,
                        exc,
                        extra=structured_log_extra(
                            event="strategy_intent_error",
                            strategy_id=name,
                            plan_id=plan_id,
                            timeframe=timeframe,
                        ),
                    )

        scored: List[tuple[StrategyIntent, float]] = []
        for intent in all_intents:
            score = self._score_intent(intent, weights)
            scored.append((intent, score))

        MIN_SCORE = 0.05
        filtered_scored = [(intent, score) for intent, score in scored if score >= MIN_SCORE]
        filtered = [intent for intent, _ in filtered_scored]

        MAX_INTENTS_PER_CYCLE = 500
        if len(filtered) > MAX_INTENTS_PER_CYCLE:
            filtered = [
                intent
                for intent, score in sorted(
                    filtered_scored, key=lambda t: t[1], reverse=True
                )[:MAX_INTENTS_PER_CYCLE]
            ]

        risk_actions = self.risk_engine.process_intents(filtered, weights=weights)

        for action in risk_actions:
            strat_cfg = self.config.strategies.configs.get(action.strategy_id)
            if strat_cfg and strat_cfg.userref is not None:
                action.userref = str(strat_cfg.userref)

        self._persist_actions(plan_id, now, risk_actions)

        ctx = self.risk_engine.build_risk_context()
        per_strategy_pnl = self.portfolio.get_realized_pnl_by_strategy(
            include_manual=False
        )

        for strategy_id, state in self.strategy_states.items():
            state.pnl_summary = {
                "realized_pnl_usd": per_strategy_pnl.get(strategy_id, 0.0),
                "exposure_pct": ctx.per_strategy_exposure_pct.get(strategy_id, 0.0),
            }
            state.last_intents = intent_summaries.get(strategy_id, [])

        plan = ExecutionPlan(
            plan_id=plan_id,
            generated_at=now,
            actions=risk_actions,
            metadata={"risk_status": asdict(self.risk_engine.get_status())},
        )

        self._persist_plan(plan)
        logger.info(
            "Execution plan created",
            extra=structured_log_extra(
                event="plan_created",
                plan_id=plan_id,
                action_count=len(plan.actions),
                blocked_actions=len([a for a in plan.actions if a.blocked]),
            ),
        )
        return plan

    def _compute_strategy_weights(
        self, regime: RegimeSnapshot
    ) -> StrategyWeights | None:
        """Compute dynamic strategy weights from performance and regime context."""

        if not self.config.risk.dynamic_allocation_enabled:
            return None

        try:
            performance = self.portfolio.get_strategy_performance(
                self.config.risk.dynamic_allocation_lookback_hours
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.error(
                "Failed to compute strategy performance for weighting: %s",
                exc,
                extra=structured_log_extra(event="strategy_weight_error"),
            )
            return None

        return compute_weights(performance, regime, self.config.risk)

    def _data_ready(self) -> bool:
        try:
            status = self.market_data.get_data_status()
        except Exception as exc:  # pragma: no cover - defensive
            logger.error(
                "Unable to fetch data status: %s",
                exc,
                extra=structured_log_extra(event="data_status_error"),
            )
            return False

        if not status.rest_api_reachable:
            logger.error(
                "REST API not reachable. Aborting cycle.",
                extra=structured_log_extra(event="rest_unreachable"),
            )
            return False

        if not status.websocket_connected:
            logger.error(
                "WebSocket not connected. Aborting cycle.",
                extra=structured_log_extra(event="websocket_unreachable"),
            )
            return False

        try:
            self.portfolio.sync()
        except Exception as exc:  # pragma: no cover - defensive
            logger.error(
                "Error syncing portfolio: %s",
                exc,
                extra=structured_log_extra(event="portfolio_sync_error"),
            )
            return False

        return True

    def _build_context(
        self,
        now: datetime,
        strategy_config: StrategyConfig,
        timeframe: str,
        regime: RegimeSnapshot,
    ) -> StrategyContext:
        # Base universe is what MarketDataAPI actually discovered and is streaming.
        dynamic_universe = self.market_data.get_universe()
        if not dynamic_universe:
            # Fallback to the static list if discovery failed.
            dynamic_universe = list(self.config.universe.include_pairs)

        return StrategyContext(
            now=now,
            universe=list(dynamic_universe),
            market_data=self.market_data,
            portfolio=self.portfolio,
            timeframe=timeframe,
            regime=regime,
        )

    def _persist_actions(
        self, plan_id: str, now: datetime, actions: List[RiskAdjustedAction]
    ) -> None:
        for action in actions:
            record = DecisionRecord(
                time=int(now.timestamp()),
                plan_id=plan_id,
                strategy_name=action.strategy_id,
                pair=action.pair,
                action_type=action.action_type,
                target_position_usd=action.target_notional_usd,
                blocked=action.blocked,
                block_reason=(
                    ";".join(action.blocked_reasons) if action.blocked_reasons else None
                ),
                kill_switch_active=self.risk_engine._kill_switch_active,
                raw_json=json.dumps(asdict(action), default=str),
            )
            self.portfolio.record_decision(record)

            for sid in action.strategy_id.split(","):
                if sid in self.strategy_states:
                    self.strategy_states[sid].last_actions_at = now

    def _persist_plan(self, plan: ExecutionPlan) -> None:
        persist_method = getattr(self.portfolio, "record_execution_plan", None)
        if callable(persist_method):
            persist_method(plan)
        else:  # pragma: no cover - backwards compatibility
            logger.debug(
                "PortfolioService missing record_execution_plan; skipping persistence.",
                extra=structured_log_extra(
                    event="plan_persist_skipped", plan_id=plan.plan_id
                ),
            )

    def get_risk_status(self) -> RiskStatus:
        return self.risk_engine.get_status()

    def get_strategy_state(self) -> List[StrategyState]:
        return list(self.strategy_states.values())

    def set_manual_kill_switch(self, active: bool) -> None:
        self.risk_engine.set_manual_kill_switch(active)

    def clear_manual_kill_switch(self) -> None:
        self.risk_engine.clear_manual_kill_switch()


# Backwards compatibility alias
StrategyRiskEngine = StrategyEngine
