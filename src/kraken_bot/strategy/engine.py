# src/kraken_bot/strategy/engine.py

import logging
import time
import json
from datetime import datetime, timezone
from typing import List, Dict, Optional, Any
from dataclasses import asdict

from kraken_bot.config import AppConfig, StrategiesConfig, StrategyConfig
from kraken_bot.market_data.api import MarketDataAPI
from kraken_bot.portfolio.manager import PortfolioService
from .models import ExecutionPlan, RiskAdjustedAction, DecisionRecord, StrategyIntent, StrategyState, RiskStatus
from .base import Strategy, StrategyContext
from .risk import RiskEngine

# Registry for available strategies.
# TODO: In Phase 6, implement dynamic discovery or a more robust plugin system.
from .strategies.demo_strategy import TrendFollowingStrategy

logger = logging.getLogger(__name__)

class StrategyRiskEngine:
    def __init__(self, config: AppConfig, market_data: MarketDataAPI, portfolio: PortfolioService):
        self.config = config
        self.market_data = market_data
        self.portfolio = portfolio
        self.risk_engine = RiskEngine(config.risk, market_data, portfolio)

        self.strategies: Dict[str, Strategy] = {}
        self.strategy_states: Dict[str, StrategyState] = {}

    def initialize(self):
        logger.info("Initializing StrategyRiskEngine...")

        # Instantiate strategies based on config
        registry = {
            "trend_following": TrendFollowingStrategy,
            "mean_reversion": None # Placeholder
        }

        for name, strat_cfg in self.config.strategies.configs.items():
            if not strat_cfg.enabled:
                continue

            if name not in self.config.strategies.enabled:
                # Double check global enabled list
                continue

            strat_class = registry.get(strat_cfg.type)
            if strat_class:
                logger.info(f"Loading strategy: {name} ({strat_cfg.type})")
                strategy = strat_class(strat_cfg)
                self.strategies[name] = strategy

                # Init state
                self.strategy_states[name] = StrategyState(
                    strategy_id=name,
                    enabled=True,
                    last_intents_at=None,
                    last_actions_at=None,
                    current_positions=[],
                    pnl_summary={}
                )

                # Warmup
                try:
                    strategy.warmup(self.market_data, self.portfolio)
                except Exception as e:
                    logger.error(f"Error warming up strategy {name}: {e}")
            else:
                logger.warning(f"Unknown strategy type: {strat_cfg.type} for {name}")

        logger.info(f"StrategyRiskEngine initialized with {len(self.strategies)} strategies.")

    def run_cycle(self, now: Optional[datetime] = None) -> ExecutionPlan:
        """
        Main decision cycle.
        """
        if not now:
            now = datetime.now(timezone.utc)

        cycle_id = f"plan_{int(now.timestamp())}"
        logger.info(f"Starting decision cycle {cycle_id}")

        # 1. Sync Data
        try:
            # Check Data Status
            status = self.market_data.get_data_status()
            # If completely stale, abort?
            # Or just proceed with caution (risk engine will see old prices?)
            # Phase 4 spec: "fail fast if data is stale"
            # How stale is stale?
            # Let's check rest_api_reachable at least.
            if not status.rest_api_reachable:
                 logger.error("REST API not reachable. Aborting cycle.")
                 return ExecutionPlan(plan_id=cycle_id, generated_at=now, actions=[], metadata={"error": "REST API unreachable"})

            self.portfolio.sync()
        except Exception as e:
            logger.error(f"Error syncing data in cycle: {e}")
            return ExecutionPlan(plan_id=cycle_id, generated_at=now, actions=[], metadata={"error": str(e)})

        # 2. Generate Intents
        all_intents: List[StrategyIntent] = []

        for name, strategy in self.strategies.items():
            # TODO: Phase 5/6 - Implement smarter scheduling (only run if candle closed).
            # Currently, we run every cycle and rely on strategy logic or external scheduler.

            # TODO: Implement per-strategy universe filtering based on StrategyConfig.
            universe = self.config.universe.include_pairs

            ctx = StrategyContext(
                now=now,
                universe=universe,
                market_data=self.market_data,
                portfolio=self.portfolio,
                timeframe="1h" # Default/Placeholder. Ideally loop over strategy timeframes.
            )

            try:
                intents = strategy.generate_intents(ctx)
                all_intents.extend(intents)

                # Update State
                state = self.strategy_states[name]
                state.last_intents_at = now

            except Exception as e:
                logger.error(f"Error generating intents for {name}: {e}")

        # 3. Risk Evaluation
        risk_actions = self.risk_engine.process_intents(all_intents)

        # 4. Persistence & Logging
        for action in risk_actions:
            # Create DecisionRecord
            record = DecisionRecord(
                time=int(now.timestamp()),
                plan_id=cycle_id,
                strategy_name=action.strategy_id, # Might be aggregated
                pair=action.pair,
                action_type=action.action_type,
                target_position_usd=action.target_notional_usd,
                blocked=action.blocked,
                block_reason=";".join(action.blocked_reasons),
                kill_switch_active=self.risk_engine._kill_switch_active,
                raw_json=json.dumps(asdict(action), default=str)
            )
            self.portfolio.record_decision(record)

            # Update last actions time for strategies involved
            if not action.blocked:
                ids = action.strategy_id.split(",")
                for sid in ids:
                    if sid in self.strategy_states:
                        self.strategy_states[sid].last_actions_at = now

        # 5. Return Plan
        plan = ExecutionPlan(
            plan_id=cycle_id,
            generated_at=now,
            actions=risk_actions,
            metadata={
                "risk_status": asdict(self.risk_engine.get_status())
            }
        )
        return plan

    def get_risk_status(self) -> RiskStatus:
        return self.risk_engine.get_status()

    def get_strategy_state(self) -> List[StrategyState]:
        return list(self.strategy_states.values())
