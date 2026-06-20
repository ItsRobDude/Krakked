"""Strategy orchestration and risk routing."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Type

from krakked.config import AppConfig, StrategyConfig
from krakked.execution.router import classify_volume, dust_reason
from krakked.logging_config import structured_log_extra
from krakked.market_data.api import MarketDataAPI
from krakked.market_data.exceptions import DataStaleError
from krakked.market_regime import (
    MarketRegimeOverlayParams,
    MarketRegimeSnapshot,
    classify_market_regime_from_market_data,
)
from krakked.portfolio.manager import PortfolioService
from krakked.portfolio.models import SpotPosition
from krakked.portfolio.sync_status import (
    PortfolioSyncStatus,
    read_portfolio_sync_status,
)
from krakked.strategy.regime import RegimeSnapshot, infer_regime

from .allocator import (
    StrategyWeights,
    combine_weights,
    compute_manual_weights,
    compute_weights,
)
from .base import Strategy, StrategyContext
from .evaluation import StrategyEvaluationResult, new_strategy_evaluation_entry
from .models import (
    DecisionRecord,
    ExecutionPlan,
    RiskAdjustedAction,
    RiskStatus,
    StrategyIntent,
    StrategyState,
)
from .pair_keys import pair_key
from .risk import RiskEngine
from .strategies.dca_rebalance import DcaRebalanceStrategy
from .strategies.demo_strategy import TrendFollowingStrategy
from .strategies.mean_reversion import MeanReversionStrategy
from .strategies.ml_alt_strategy import AIPredictorAltStrategy
from .strategies.ml_regression_strategy import AIRegressionStrategy
from .strategies.ml_strategy import AIPredictorStrategy
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
        "machine_learning_alt": AIPredictorAltStrategy,
        "machine_learning_regression": AIRegressionStrategy,
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
        portfolio_sync = self._portfolio_sync_status()
        self._cached_risk_status = RiskStatus(
            kill_switch_active=False,
            daily_drawdown_pct=0.0,
            drift_flag=False,
            total_exposure_pct=0.0,
            manual_exposure_pct=0.0,
            per_asset_exposure_pct={},
            per_strategy_exposure_pct={},
            portfolio_sync_ok=portfolio_sync.ok,
            portfolio_sync_reason=portfolio_sync.reason,
            portfolio_last_sync_at=portfolio_sync.last_sync_at,
        )
        self._cached_strategy_state: List[StrategyState] = []
        self.last_cycle_intents: List[StrategyIntent] = []
        self._last_strategy_timeframe_bar_ts: Dict[tuple[str, str], int] = {}

    def _execution_mode(self) -> str | None:
        execution_config = getattr(self.config, "execution", None)
        mode = getattr(execution_config, "mode", None)
        return mode if isinstance(mode, str) else None

    def _portfolio_sync_status(self) -> PortfolioSyncStatus:
        return read_portfolio_sync_status(
            self.portfolio,
            execution_mode=self._execution_mode(),
        )

    def initialize(self) -> None:
        logger.info(
            "Initializing StrategyEngine...",
            extra=structured_log_extra(event="strategy_engine_init"),
        )
        registry = _strategy_registry()

        self.strategies = {}
        self.strategy_states = {}

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

            is_active = self._is_strategy_active(strat_cfg)
            self._ensure_strategy_state(strat_cfg, enabled=is_active)

            if not is_active:
                logger.info(
                    "Skipping disabled strategy %s",
                    strategy_id,
                    extra=structured_log_extra(
                        event="strategy_disabled_skip", strategy_id=strategy_id
                    ),
                )
                continue

            if not self._activate_strategy(strat_cfg, registry):
                self.strategy_states[strategy_id].enabled = False

        logger.info(
            "StrategyEngine initialized with %d strategies",
            len(self.strategies),
            extra=structured_log_extra(
                event="strategy_engine_ready",
                strategy_id="all",
                count=len(self.strategies),
            ),
        )
        self.refresh_strategy_weight_state()
        self.refresh_runtime_snapshots()

    def _is_strategy_active(self, strat_cfg: StrategyConfig) -> bool:
        return bool(
            strat_cfg.enabled and strat_cfg.name in self.config.strategies.enabled
        )

    def _ensure_strategy_state(
        self, strat_cfg: StrategyConfig, *, enabled: bool
    ) -> StrategyState:
        state = self.strategy_states.get(strat_cfg.name)
        if state is None:
            state = StrategyState(
                strategy_id=strat_cfg.name,
                enabled=enabled,
                last_intents_at=None,
                last_actions_at=None,
                current_positions=[],
                pnl_summary={},
                last_intents=None,
                params=dict(strat_cfg.params),
                configured_weight=strat_cfg.strategy_weight,
                effective_weight_pct=0.0 if enabled else None,
                last_evaluated_at=None,
            )
            self.strategy_states[strat_cfg.name] = state
            return state

        state.enabled = enabled
        state.params = dict(strat_cfg.params)
        state.configured_weight = strat_cfg.strategy_weight
        if not enabled:
            state.effective_weight_pct = None
        return state

    def _activate_strategy(
        self,
        strat_cfg: StrategyConfig,
        registry: Dict[str, Type[Strategy]] | None = None,
    ) -> bool:
        registry = registry or _strategy_registry()
        strategy_id = strat_cfg.name

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
            return False

        try:
            strategy = strat_class(strat_cfg)
            strategy.warmup(self.market_data, self.portfolio)
        except Exception as exc:  # pragma: no cover - defensive
            logger.error(
                "Error activating strategy %s: %s",
                strategy_id,
                exc,
                extra=structured_log_extra(
                    event="strategy_activation_error", strategy_id=strategy_id
                ),
            )
            return False

        self.strategies[strategy_id] = strategy
        return True

    def _compute_manual_strategy_weights(self) -> StrategyWeights | None:
        configured_weights = {
            strategy_id: max(state.configured_weight, 1)
            for strategy_id, state in self.strategy_states.items()
            if state.enabled
        }
        manual = compute_manual_weights(configured_weights)
        if not manual.per_strategy_pct:
            return None
        return manual

    def refresh_strategy_weight_state(
        self, weights: StrategyWeights | None = None
    ) -> None:
        manual_weights = weights or self._compute_manual_strategy_weights()
        active_weights = manual_weights.per_strategy_pct if manual_weights else {}

        for strategy_id, state in self.strategy_states.items():
            strat_cfg = self.config.strategies.configs.get(strategy_id)
            if strat_cfg:
                state.configured_weight = strat_cfg.strategy_weight
                state.params = dict(strat_cfg.params)
            state.effective_weight_pct = active_weights.get(strategy_id)
            if not state.enabled and state.effective_weight_pct is None:
                state.effective_weight_pct = None

    def _last_market_regime_throttle_payload(self) -> Dict[str, Any] | None:
        getter = getattr(self.risk_engine, "get_last_market_regime_throttle", None)
        if not callable(getter):
            return None
        payload = getter()
        return payload if isinstance(payload, dict) else None

    def refresh_runtime_snapshots(self) -> None:
        self.refresh_strategy_weight_state()
        cached_equity = self.portfolio.get_cached_equity()
        cached_exposures = self.portfolio.get_cached_asset_exposure()
        cached_positions = self.portfolio.get_cached_positions()
        drift_status = self.portfolio.get_cached_drift_status()
        realized_by_strategy = self.portfolio.get_realized_pnl_by_strategy(
            include_manual=False
        )

        per_strategy_exposure_pct: Dict[str, float] = {}
        manual_exposure_pct = 0.0
        total_exposure_pct = 0.0
        drawdown_pct = 0.0

        if cached_equity.equity_base:
            for position in cached_positions:
                current_value = abs(getattr(position, "current_value_base", 0.0) or 0.0)
                if current_value <= 0:
                    continue
                strategy_key = position.strategy_tag or "manual"
                pct = (current_value / cached_equity.equity_base) * 100.0
                if strategy_key == "manual":
                    manual_exposure_pct += pct
                else:
                    per_strategy_exposure_pct[strategy_key] = (
                        per_strategy_exposure_pct.get(strategy_key, 0.0) + pct
                    )

            total_exposure_pct = sum(
                max((exp.percentage_of_equity or 0.0), 0.0) * 100.0
                for exp in cached_exposures
                if exp.asset != self.portfolio.config.base_currency
            )

        for strategy_id, state in self.strategy_states.items():
            state.pnl_summary = {
                "realized_pnl_usd": realized_by_strategy.get(strategy_id, 0.0),
                "exposure_pct": per_strategy_exposure_pct.get(strategy_id, 0.0),
            }

        now_ts = int(datetime.now(timezone.utc).timestamp())
        day_ago = now_ts - 86400
        snapshots = self.portfolio.store.get_snapshots(since=day_ago)
        current_equity = cached_equity.equity_base
        drift_flag = bool(cached_equity.drift_flag)
        drift_info: Dict[str, Any] = {
            "expected_position_value_base": None,
            "actual_balance_value_base": None,
            "tolerance_base": None,
            "mismatched_assets": [],
        }
        max_equity_24h = (
            max([current_equity] + [snapshot.equity_base for snapshot in snapshots])
            if snapshots
            else current_equity
        )
        if max_equity_24h > 0:
            drawdown_pct = ((max_equity_24h - current_equity) / max_equity_24h) * 100.0

        if drift_status is not None:
            drift_flag = bool(drift_flag or getattr(drift_status, "drift_flag", False))
            drift_info = {
                "expected_position_value_base": getattr(
                    drift_status, "expected_position_value_base", None
                ),
                "actual_balance_value_base": getattr(
                    drift_status, "actual_balance_value_base", None
                ),
                "tolerance_base": getattr(drift_status, "tolerance_base", None),
                "mismatched_assets": [
                    asdict(asset)
                    for asset in (getattr(drift_status, "mismatched_assets", []) or [])
                ],
            }

        portfolio_sync = self._portfolio_sync_status()
        self._cached_risk_status = RiskStatus(
            kill_switch_active=self.risk_engine._kill_switch_active
            or self.risk_engine._manual_kill_switch_active,
            daily_drawdown_pct=drawdown_pct,
            drift_flag=drift_flag,
            total_exposure_pct=total_exposure_pct,
            manual_exposure_pct=manual_exposure_pct,
            per_asset_exposure_pct={
                exp.asset: (exp.percentage_of_equity or 0.0) * 100.0
                for exp in cached_exposures
            },
            per_strategy_exposure_pct=per_strategy_exposure_pct,
            drift_info=drift_info,
            market_regime_throttle=self._last_market_regime_throttle_payload(),
            portfolio_sync_ok=portfolio_sync.ok,
            portfolio_sync_reason=portfolio_sync.reason,
            portfolio_last_sync_at=portfolio_sync.last_sync_at,
        )
        self._cached_strategy_state = [
            StrategyState(
                strategy_id=state.strategy_id,
                enabled=state.enabled,
                last_intents_at=state.last_intents_at,
                last_actions_at=state.last_actions_at,
                current_positions=list(state.current_positions),
                pnl_summary=dict(state.pnl_summary),
                last_intents=list(state.last_intents) if state.last_intents else None,
                conflict_summary=(
                    list(state.conflict_summary) if state.conflict_summary else None
                ),
                params=dict(state.params),
                configured_weight=state.configured_weight,
                effective_weight_pct=state.effective_weight_pct,
                last_evaluated_at=state.last_evaluated_at,
                last_evaluation_summary=(
                    dict(state.last_evaluation_summary)
                    if state.last_evaluation_summary
                    else None
                ),
            )
            for state in self.strategy_states.values()
        ]

    def _build_conflict_summaries(
        self,
        intents: List[StrategyIntent],
        actions: List[RiskAdjustedAction],
    ) -> Dict[str, List[Dict[str, Any]]]:
        pair_groups: Dict[str, List[StrategyIntent]] = {}
        for intent in intents:
            pair_groups.setdefault(intent.pair, []).append(intent)

        actions_by_pair = {action.pair: action for action in actions}
        summaries_by_strategy: Dict[str, List[Dict[str, Any]]] = {}

        for pair, pair_intents in pair_groups.items():
            strategy_ids = sorted({intent.strategy_id for intent in pair_intents})
            if len(strategy_ids) < 2:
                continue

            def _rank_key(intent: StrategyIntent) -> tuple[float, float]:
                state = self.strategy_states.get(intent.strategy_id)
                effective_weight = (
                    state.effective_weight_pct
                    if state is not None and state.effective_weight_pct is not None
                    else 0.0
                )
                return effective_weight, intent.confidence

            ranked_intents = sorted(pair_intents, key=_rank_key, reverse=True)

            pair_action = actions_by_pair.get(pair)
            winner_strategy_id = (
                ranked_intents[0].strategy_id if ranked_intents else None
            )
            winning_reason = "higher effective share"

            if pair_action and pair_action.action_type == "none":
                winner_strategy_id = None
                winning_reason = (
                    "risk blocked competing intent"
                    if pair_action.blocked
                    else "no action because conflict netted out"
                )
            elif winner_strategy_id:
                winner_state = self.strategy_states.get(winner_strategy_id)
                if winner_state and not winner_state.enabled:
                    winning_reason = "other strategy paused"

            display_pair = self.market_data.get_display_pair(pair)

            for strategy_id in strategy_ids:
                if winner_strategy_id is None:
                    outcome = "netted_out"
                elif strategy_id == winner_strategy_id:
                    outcome = "winner"
                else:
                    outcome = "loser"

                summaries_by_strategy.setdefault(strategy_id, []).append(
                    {
                        "pair": display_pair,
                        "competing_strategies": strategy_ids,
                        "winner_strategy_id": winner_strategy_id,
                        "winning_reason": winning_reason,
                        "outcome": outcome,
                    }
                )

        return summaries_by_strategy

    @staticmethod
    def _new_strategy_evaluation() -> Dict[str, Any]:
        return new_strategy_evaluation_entry()

    @staticmethod
    def _add_evaluated_timeframe(entry: Dict[str, Any], timeframe: str) -> None:
        timeframes = entry.setdefault("timeframes_evaluated", [])
        if timeframe not in timeframes:
            timeframes.append(timeframe)

    @staticmethod
    def _configured_strategy_timeframes(strategy: Strategy) -> List[str]:
        configured_timeframes = strategy.config.params.get("timeframes")
        if isinstance(configured_timeframes, (list, tuple)):
            return [str(timeframe) for timeframe in configured_timeframes]
        if configured_timeframes is not None:
            return [str(configured_timeframes)]

        single_timeframe = strategy.config.params.get("timeframe")
        return [str(single_timeframe)] if single_timeframe else ["1h"]

    @staticmethod
    def _count_intents_by_strategy(
        intents: List[StrategyIntent],
    ) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for intent in intents:
            counts[intent.strategy_id] = counts.get(intent.strategy_id, 0) + 1
        return counts

    @staticmethod
    def _count_blocked_actions_by_strategy(
        actions: List[RiskAdjustedAction],
    ) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for action in actions:
            if not action.blocked:
                continue
            counts[action.strategy_id] = counts.get(action.strategy_id, 0) + 1
        return counts

    @staticmethod
    def _is_exit_intent(intent: StrategyIntent) -> bool:
        return intent.side == "flat" or intent.intent_type in {
            "exit",
            "close",
            "reduce",
        }

    def _market_regime_throttle_snapshot(
        self,
        now: datetime,
    ) -> MarketRegimeSnapshot | None:
        throttle_config = self.config.risk.market_regime_throttle
        if not throttle_config.enabled:
            return None

        pairs = list(throttle_config.pairs or self.config.universe.include_pairs or [])
        if not pairs:
            try:
                pairs = list(self.market_data.get_universe() or [])
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning(
                    "Failed to fetch market-regime throttle universe: %s",
                    exc,
                    extra=structured_log_extra(
                        event="market_regime_throttle_universe_failed"
                    ),
                )
                pairs = []

        params = MarketRegimeOverlayParams(
            timeframe=throttle_config.timeframe,
            benchmark_pair=throttle_config.benchmark_pair,
            momentum_lookback_bars=throttle_config.momentum_lookback_bars,
            basket_momentum_lookback_bars=(
                throttle_config.basket_momentum_lookback_bars
            ),
            volatility_lookback_bars=throttle_config.volatility_lookback_bars,
            drawdown_lookback_bars=throttle_config.drawdown_lookback_bars,
            neutral_allocation_multiplier=(
                throttle_config.neutral_allocation_multiplier
            ),
            risk_off_allocation_multiplier=(
                throttle_config.risk_off_allocation_multiplier
            ),
            neutral_benchmark_momentum_bps=(
                throttle_config.neutral_benchmark_momentum_bps
            ),
            neutral_basket_momentum_bps=throttle_config.neutral_basket_momentum_bps,
            risk_off_benchmark_momentum_bps=(
                throttle_config.risk_off_benchmark_momentum_bps
            ),
            risk_off_basket_momentum_bps=(throttle_config.risk_off_basket_momentum_bps),
            neutral_benchmark_drawdown_pct=(
                throttle_config.neutral_benchmark_drawdown_pct
            ),
            risk_off_benchmark_drawdown_pct=(
                throttle_config.risk_off_benchmark_drawdown_pct
            ),
            neutral_volatility_pct=throttle_config.neutral_volatility_pct,
            risk_off_volatility_pct=throttle_config.risk_off_volatility_pct,
        )

        try:
            return classify_market_regime_from_market_data(
                self.market_data,
                pairs=pairs,
                params=params,
                timestamp=int(now.timestamp()),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to compute market-regime throttle snapshot: %s",
                exc,
                extra=structured_log_extra(
                    event="market_regime_throttle_snapshot_failed"
                ),
            )
            return None

    def _timeframe_bar_status(
        self,
        strategy_id: str,
        timeframe: str,
        pairs: Sequence[str],
    ) -> tuple[str, Optional[int], Optional[str]]:
        """Return closed-bar readiness for this strategy/timeframe context."""

        latest_ts: Optional[int] = None
        saw_invalid_timestamp = False
        for pair in pairs:
            try:
                bars = self.market_data.get_ohlc(pair, timeframe, lookback=1)
            except Exception:
                continue
            if not bars:
                continue
            try:
                raw_timestamp = getattr(bars[-1], "timestamp", None)
            except Exception:
                saw_invalid_timestamp = True
                continue
            if raw_timestamp is None:
                saw_invalid_timestamp = True
                continue
            try:
                bar_ts = int(raw_timestamp)
            except (TypeError, ValueError):
                saw_invalid_timestamp = True
                continue
            latest_ts = bar_ts if latest_ts is None else max(latest_ts, bar_ts)

        if latest_ts is None:
            if saw_invalid_timestamp:
                return "invalid_bar_timestamp", None, None
            return "no_data", None, None

        key = (strategy_id, timeframe)
        last_seen = self._last_strategy_timeframe_bar_ts.get(key)
        if last_seen is not None and latest_ts <= last_seen:
            return "deferred_no_new_bar", latest_ts, None
        return "fresh", latest_ts, None

    def _mark_timeframe_bar_evaluated(
        self,
        strategy_id: str,
        timeframe: str,
        bar_timestamp: Optional[int],
    ) -> None:
        if bar_timestamp is None:
            return
        self._last_strategy_timeframe_bar_ts[(strategy_id, timeframe)] = int(
            bar_timestamp
        )

    @staticmethod
    def _isoformat_utc(value: datetime) -> str:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _limited_no_signal_reasons(
        reasons: Sequence[Dict[str, Any]],
        *,
        limit: int = 8,
    ) -> List[Dict[str, Any]]:
        limited: List[Dict[str, Any]] = []
        for reason in reasons:
            if not isinstance(reason, dict):
                continue
            payload = dict(reason)
            if "reason" in payload:
                payload["reason"] = str(payload["reason"])
            if "message" in payload:
                payload["message"] = str(payload["message"])
            if "pair" in payload:
                payload["pair"] = str(payload["pair"])
            if "timeframe" in payload:
                payload["timeframe"] = str(payload["timeframe"])
            limited.append(payload)
            if len(limited) >= limit:
                break
        return limited

    @staticmethod
    def _append_context_summary(
        evaluation: Dict[str, Any],
        *,
        timeframe: str,
        status: str,
        latest_bar_timestamp: Optional[int] = None,
        pair: Optional[str] = None,
        message: Optional[str] = None,
        intents_emitted: Optional[int] = None,
        reason: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        summaries = evaluation.setdefault("context_summaries", [])
        if not isinstance(summaries, list) or len(summaries) >= 12:
            return
        payload: Dict[str, Any] = {
            "timeframe": str(timeframe),
            "status": str(status),
        }
        if latest_bar_timestamp is not None:
            payload["latest_bar_timestamp"] = int(latest_bar_timestamp)
        if pair:
            payload["pair"] = str(pair)
        if message:
            payload["message"] = str(message)
        if intents_emitted is not None:
            payload["intents_emitted"] = int(intents_emitted)
        if reason:
            payload["reason"] = str(reason)
        if extra:
            payload.update(dict(extra))
        summaries.append(payload)

    @staticmethod
    def _append_context_payload(
        evaluation: Dict[str, Any],
        payload: Dict[str, Any],
        *,
        timeframe: str,
        latest_bar_timestamp: Optional[int] = None,
        intents_emitted: Optional[int] = None,
    ) -> None:
        summaries = evaluation.setdefault("context_summaries", [])
        if not isinstance(summaries, list) or len(summaries) >= 12:
            return
        context_payload = dict(payload)
        context_payload.setdefault("timeframe", str(timeframe))
        if latest_bar_timestamp is not None:
            context_payload.setdefault(
                "latest_bar_timestamp", int(latest_bar_timestamp)
            )
        if intents_emitted is not None:
            context_payload.setdefault("intents_emitted", int(intents_emitted))
        if "status" not in context_payload:
            context_payload["status"] = (
                "intents_emitted" if (intents_emitted or 0) > 0 else "no_signal"
            )
        summaries.append(context_payload)

    def _append_evaluation_result_diagnostics(
        self,
        *,
        evaluation: Dict[str, Any],
        result: StrategyEvaluationResult,
        timeframe: str,
        latest_bar_timestamp: Optional[int],
    ) -> None:
        if result.intents:
            evaluation["no_signal_reasons"] = []
        else:
            existing = evaluation.setdefault("no_signal_reasons", [])
            if not isinstance(existing, list):
                existing = []
                evaluation["no_signal_reasons"] = existing
            for reason in self._limited_no_signal_reasons(result.no_signal_reasons):
                reason.setdefault("timeframe", timeframe)
                if len(existing) >= 12:
                    break
                existing.append(reason)

        if result.context_summaries:
            for context_summary in result.context_summaries:
                if not isinstance(context_summary, dict):
                    continue
                self._append_context_payload(
                    evaluation,
                    context_summary,
                    timeframe=timeframe,
                    latest_bar_timestamp=latest_bar_timestamp,
                    intents_emitted=len(result.intents),
                )
            return

        reason_message = None
        if not result.intents and result.no_signal_reasons:
            first_reason = result.no_signal_reasons[0]
            if isinstance(first_reason, dict):
                reason_message = first_reason.get("message")
        self._append_context_summary(
            evaluation,
            timeframe=timeframe,
            status=result.status
            or ("intents_emitted" if result.intents else "no_signal"),
            latest_bar_timestamp=latest_bar_timestamp,
            message=(
                str(result.message)
                if result.message
                else (
                    str(reason_message)
                    if reason_message
                    else (
                        f"Generated {len(result.intents)} intent(s)"
                        if result.intents
                        else "No action chosen"
                    )
                )
            ),
            intents_emitted=len(result.intents),
        )

    def _build_strategy_evaluation_summary(
        self, evaluation: Dict[str, Any], now: datetime
    ) -> Dict[str, Any]:
        contexts = int(evaluation.get("contexts_evaluated", 0) or 0)
        fresh_contexts = int(evaluation.get("fresh_contexts_evaluated", 0) or 0)
        deferred_contexts = int(evaluation.get("deferred_no_new_bar_contexts", 0) or 0)
        no_data_contexts = int(evaluation.get("no_data_contexts", 0) or 0)
        invalid_contexts = int(evaluation.get("invalid_bar_timestamp_contexts", 0) or 0)
        data_stale_contexts = int(evaluation.get("data_stale_contexts", 0) or 0)
        intents = int(evaluation.get("intents_emitted", 0) or 0)
        strategy_error_contexts = int(evaluation.get("strategy_error_contexts", 0) or 0)
        reasons = (
            []
            if intents > 0
            else self._limited_no_signal_reasons(
                evaluation.get("no_signal_reasons") or [], limit=5
            )
        )
        skipped_no_pairs = int(evaluation.get("skipped_no_pairs", 0) or 0)

        if strategy_error_contexts > 0:
            status = "strategy_error"
            message = "Strategy evaluation failed"
        elif skipped_no_pairs:
            status = "no_pairs"
            message = "No eligible pairs configured for this strategy"
        elif intents > 0:
            status = "intents_emitted"
            label = "intent" if intents == 1 else "intents"
            message = f"Generated {intents} {label}"
        elif data_stale_contexts > 0:
            status = "data_stale"
            message = "Market data was stale for this strategy context"
        elif fresh_contexts > 0:
            status = "no_signal"
            if reasons:
                message = reasons[0].get("message") or "No action chosen"
            else:
                message = "No action chosen after evaluating a fresh closed bar"
        elif invalid_contexts > 0:
            status = "invalid_bar_timestamp"
            message = "Closed-bar timestamp was missing or invalid"
        elif no_data_contexts > 0:
            status = "no_data"
            message = "No closed bars are available for this strategy timeframe"
        elif deferred_contexts > 0:
            status = "deferred_no_new_bar"
            message = "Waiting for the next closed strategy bar"
        elif contexts > 0:
            status = "not_evaluated"
            message = "Strategy context did not reach evaluation"
        else:
            status = "awaiting_evaluation"
            message = "Awaiting first strategy evaluation"

        return {
            "status": status,
            "message": message,
            "evaluated_at": self._isoformat_utc(now),
            "contexts_evaluated": contexts,
            "fresh_contexts_evaluated": fresh_contexts,
            "deferred_no_new_bar_contexts": deferred_contexts,
            "no_data_contexts": no_data_contexts,
            "invalid_bar_timestamp_contexts": invalid_contexts,
            "data_stale_contexts": data_stale_contexts,
            "strategy_error_contexts": strategy_error_contexts,
            "intents_emitted": intents,
            "timeframes_evaluated": list(evaluation.get("timeframes_evaluated") or []),
            "context_summaries": list(evaluation.get("context_summaries") or [])[:5],
            "reasons": reasons,
        }

    def _update_strategy_evaluation_summary(
        self, strategy_id: str, evaluation: Dict[str, Any], now: datetime
    ) -> None:
        summary = self._build_strategy_evaluation_summary(evaluation, now)
        evaluation["last_evaluation_summary"] = summary
        state = self.strategy_states.get(strategy_id)
        if state is not None:
            state.last_evaluation_summary = summary

    def _disabled_strategy_summary(self) -> Dict[str, Any]:
        return {
            "status": "disabled",
            "message": "Strategy is paused",
            "evaluated_at": self._isoformat_utc(datetime.now(timezone.utc)),
            "contexts_evaluated": 0,
            "fresh_contexts_evaluated": 0,
            "deferred_no_new_bar_contexts": 0,
            "no_data_contexts": 0,
            "invalid_bar_timestamp_contexts": 0,
            "data_stale_contexts": 0,
            "strategy_error_contexts": 0,
            "intents_emitted": 0,
            "timeframes_evaluated": [],
            "context_summaries": [],
            "reasons": [],
        }

    def _awaiting_strategy_summary(self) -> Dict[str, Any]:
        return {
            "status": "awaiting_evaluation",
            "message": "Awaiting first strategy evaluation",
            "evaluated_at": self._isoformat_utc(datetime.now(timezone.utc)),
            "contexts_evaluated": 0,
            "fresh_contexts_evaluated": 0,
            "deferred_no_new_bar_contexts": 0,
            "no_data_contexts": 0,
            "invalid_bar_timestamp_contexts": 0,
            "data_stale_contexts": 0,
            "strategy_error_contexts": 0,
            "intents_emitted": 0,
            "timeframes_evaluated": [],
            "context_summaries": [],
            "reasons": [],
        }

    def _position_base_by_strategy_pair(
        self,
        positions: Sequence[SpotPosition],
    ) -> Dict[tuple[str, str], float]:
        totals: Dict[tuple[str, str], float] = {}
        for position in positions:
            strategy_id = getattr(position, "strategy_tag", None)
            pair = getattr(position, "pair", None)
            if not strategy_id or not pair:
                continue
            try:
                base_size = float(getattr(position, "base_size", 0.0) or 0.0)
            except (TypeError, ValueError):
                continue
            if base_size <= 0:
                continue
            key = (str(strategy_id), pair_key(self.market_data, pair))
            totals[key] = totals.get(key, 0.0) + base_size
        return totals

    def _record_intent_score(
        self,
        evaluation_summary: Dict[str, Dict[str, Any]],
        intent: StrategyIntent,
        score: float,
        *,
        min_score: float,
        positions_by_strategy_pair: Optional[Dict[tuple[str, str], float]] = None,
    ) -> None:
        evaluation = evaluation_summary.setdefault(
            intent.strategy_id, StrategyEngine._new_strategy_evaluation()
        )
        current_min = evaluation.get("min_score")
        current_max = evaluation.get("max_score")
        evaluation["min_score"] = (
            score if current_min is None else min(float(current_min), score)
        )
        evaluation["max_score"] = (
            score if current_max is None else max(float(current_max), score)
        )
        if score < min_score:
            evaluation["filtered_by_score"] += 1
            if StrategyEngine._is_exit_intent(intent):
                position_base = (positions_by_strategy_pair or {}).get(
                    (intent.strategy_id, pair_key(self.market_data, intent.pair)), 0.0
                )
                if position_base > 0:
                    evaluation["filtered_position_exits"] += 1
                else:
                    evaluation["filtered_no_position_exits"] += 1
            else:
                evaluation["filtered_low_score_entries"] += 1

    def _collect_intents(
        self,
        now: datetime,
        regime: RegimeSnapshot,
        plan_id: str,
        weights: Optional[StrategyWeights],
    ) -> tuple[
        List[StrategyIntent],
        Dict[str, List[Dict[str, Any]]],
        Dict[str, Dict[str, Any]],
    ]:
        """Collect intents from all active strategies across configured timeframes."""
        all_intents: List[StrategyIntent] = []
        intent_summaries: Dict[str, List[Dict[str, Any]]] = {}
        evaluation_summary = {
            name: self._new_strategy_evaluation() for name in self.strategies
        }

        for name, strategy in self.strategies.items():
            state = self.strategy_states.get(name)
            if state is not None and not state.enabled:
                continue
            evaluation = evaluation_summary.setdefault(
                name, self._new_strategy_evaluation()
            )
            evaluation["cycles_evaluated"] += 1
            strategy_pairs = self._get_strategy_pairs(name)
            if not strategy_pairs:
                evaluation["skipped_no_pairs"] += 1
                logger.info(
                    "No eligible pairs for strategy %s; skipping this cycle",
                    name,
                    extra=structured_log_extra(
                        event="strategy_no_pairs", strategy_id=name
                    ),
                )
                self._update_strategy_evaluation_summary(name, evaluation, now)
                continue

            timeframes = self._configured_strategy_timeframes(strategy)
            requires_closed_bar_context = bool(
                getattr(strategy, "requires_closed_bar_context", True)
            )

            for timeframe in timeframes:
                evaluation["contexts_evaluated"] += 1
                self._add_evaluated_timeframe(evaluation, timeframe)
                fresh_bar_ts: Optional[int] = None
                if requires_closed_bar_context:
                    bar_status, fresh_bar_ts, _ = self._timeframe_bar_status(
                        name, timeframe, strategy_pairs
                    )
                    if bar_status == "deferred_no_new_bar":
                        evaluation["deferred_no_new_bar_contexts"] += 1
                        # Compatibility alias for existing reports/consumers.
                        evaluation["skipped_stale_timeframe_contexts"] += 1
                        self._append_context_summary(
                            evaluation,
                            timeframe=timeframe,
                            status=bar_status,
                            latest_bar_timestamp=fresh_bar_ts,
                            message="Waiting for the next closed strategy bar",
                        )
                        continue
                    if bar_status == "no_data":
                        evaluation["no_data_contexts"] += 1
                        self._append_context_summary(
                            evaluation,
                            timeframe=timeframe,
                            status=bar_status,
                            message="No closed bars are available",
                        )
                        continue
                    if bar_status == "invalid_bar_timestamp":
                        evaluation["invalid_bar_timestamp_contexts"] += 1
                        self._append_context_summary(
                            evaluation,
                            timeframe=timeframe,
                            status=bar_status,
                            message="Closed-bar timestamp was missing or invalid",
                        )
                        continue
                context = self._build_context(
                    now, strategy.config, timeframe, regime, strategy_pairs
                )
                try:
                    result = strategy.evaluate(context)
                    intents = list(result.intents or [])
                    self._mark_timeframe_bar_evaluated(name, timeframe, fresh_bar_ts)
                    evaluation["fresh_contexts_evaluated"] += 1
                    evaluation["intents_emitted"] += len(intents)
                    self._append_evaluation_result_diagnostics(
                        evaluation=evaluation,
                        result=StrategyEvaluationResult(
                            intents=intents,
                            no_signal_reasons=list(result.no_signal_reasons or []),
                            context_summaries=list(result.context_summaries or []),
                            status=result.status,
                            message=result.message,
                        ),
                        timeframe=timeframe,
                        latest_bar_timestamp=fresh_bar_ts,
                    )
                    for intent in intents:
                        intent.strategy_id = name
                        intent.metadata = intent.metadata or {}
                        intent.metadata.setdefault("strategy_id", name)
                        intent.metadata.setdefault("timeframe", timeframe)
                        if weights:
                            weight_hint = weights.per_strategy_pct.get(name)
                            if weight_hint is not None:
                                intent.metadata.setdefault(
                                    "weight_hint_pct", weight_hint
                                )
                        if strategy.config.userref is not None:
                            intent.metadata.setdefault(
                                "userref", str(strategy.config.userref)
                            )
                        summary = intent_summaries.setdefault(name, [])
                        if len(summary) < 10:
                            summary.append(
                                {
                                    "pair": self.market_data.get_display_pair(
                                        intent.pair
                                    ),
                                    "side": intent.side,
                                    "intent_type": intent.intent_type,
                                    "desired_exposure_usd": intent.desired_exposure_usd,
                                    "confidence": intent.confidence,
                                    "timeframe": timeframe,
                                }
                            )
                    all_intents.extend(intents)
                    self.strategy_states[name].last_evaluated_at = now
                    self.strategy_states[name].last_intents_at = now
                except DataStaleError as exc:
                    evaluation["data_stale_contexts"] += 1
                    self._append_context_summary(
                        evaluation,
                        timeframe=timeframe,
                        status="data_stale",
                        pair=exc.pair,
                        message="Market data was stale for this strategy context",
                    )
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
                    evaluation["strategy_error_contexts"] += 1
                    self._append_context_summary(
                        evaluation,
                        timeframe=timeframe,
                        status="strategy_error",
                        message=str(exc),
                    )
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
                    self.strategy_states[name].last_evaluated_at = now

            self._update_strategy_evaluation_summary(name, evaluation, now)

        return all_intents, intent_summaries, evaluation_summary

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

        return base * weights.factor_for(intent.strategy_id)

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
        self.refresh_strategy_weight_state(weights)
        all_intents, intent_summaries, evaluation_summary = self._collect_intents(
            now, regime, plan_id, weights
        )
        self.last_cycle_intents = list(all_intents)

        scored: List[tuple[StrategyIntent, float]] = []
        for intent in all_intents:
            score = self._score_intent(intent, weights)
            scored.append((intent, score))

        MIN_SCORE = 0.05
        positions_by_strategy_pair = self._position_base_by_strategy_pair(
            self.portfolio.get_positions() or []
        )
        for intent, score in scored:
            self._record_intent_score(
                evaluation_summary,
                intent,
                score,
                min_score=MIN_SCORE,
                positions_by_strategy_pair=positions_by_strategy_pair,
            )
        filtered_scored = [
            (intent, score) for intent, score in scored if score >= MIN_SCORE
        ]
        filtered = [intent for intent, _ in filtered_scored]

        MAX_INTENTS_PER_CYCLE = 500
        if len(filtered) > MAX_INTENTS_PER_CYCLE:
            filtered = [
                intent
                for intent, score in sorted(
                    filtered_scored, key=lambda t: t[1], reverse=True
                )[:MAX_INTENTS_PER_CYCLE]
            ]

        for strategy_id, count in self._count_intents_by_strategy(filtered).items():
            evaluation = evaluation_summary.setdefault(
                strategy_id, self._new_strategy_evaluation()
            )
            evaluation["actions_after_scoring"] += count

        # Fetch pending orders from the store to prevent double-spending in risk checks
        pending_orders = []
        if self.portfolio.store and hasattr(self.portfolio.store, "get_open_orders"):
            try:
                pending_orders = self.portfolio.store.get_open_orders()
            except Exception as exc:  # pragma: no cover - defensive logging
                logger.warning(
                    "Failed to fetch pending orders for risk check: %s",
                    exc,
                    extra=structured_log_extra(event="pending_orders_fetch_error"),
                )

        market_regime_snapshot = self._market_regime_throttle_snapshot(now)
        risk_actions = self.risk_engine.process_intents(
            filtered,
            weights=weights,
            pending_orders=pending_orders,
            market_regime_snapshot=market_regime_snapshot,
        )
        conflict_summaries = self._build_conflict_summaries(filtered, risk_actions)
        for strategy_id, count in self._count_blocked_actions_by_strategy(
            risk_actions
        ).items():
            evaluation = evaluation_summary.setdefault(
                strategy_id, self._new_strategy_evaluation()
            )
            evaluation["blocked_actions"] += count

        for action in risk_actions:
            strat_cfg = self.config.strategies.configs.get(action.strategy_id)

            # Handling Composite IDs (e.g., "dca_rebalance,trend_core")
            if not strat_cfg and "," in action.strategy_id:
                # 1. Split the ID
                parts = action.strategy_id.split(",")
                # 2. Sort for determinism (ensures "a,b" and "b,a" always resolve to "a")
                parts.sort()

                # 3. Find the first constituent strategy that has a valid config
                for sub_id in parts:
                    candidate_cfg = self.config.strategies.configs.get(sub_id)
                    if candidate_cfg and candidate_cfg.userref is not None:
                        strat_cfg = candidate_cfg
                        break

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
            state.conflict_summary = conflict_summaries.get(strategy_id, [])

        plan_metadata = {
            "risk_status": asdict(self.risk_engine.get_status()),
            "strategy_evaluation": evaluation_summary,
        }
        throttle_payload = self._last_market_regime_throttle_payload()
        if throttle_payload:
            plan_metadata["market_regime_throttle"] = throttle_payload

        plan = ExecutionPlan(
            plan_id=plan_id,
            generated_at=now,
            actions=risk_actions,
            metadata=plan_metadata,
        )

        self._persist_plan(plan)
        self.refresh_runtime_snapshots()
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
        """Compute effective strategy weights from manual and dynamic inputs."""

        manual_weights = self._compute_manual_strategy_weights()
        if manual_weights is None:
            return None

        if not self.config.risk.dynamic_allocation_enabled:
            return manual_weights

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
            return manual_weights

        dynamic_weights = compute_weights(performance, regime, self.config.risk)
        return combine_weights(manual_weights, dynamic_weights)

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

        # Fail closed: a degraded sync returns normally but leaves last_sync_ok
        # False (account truth unavailable). Do not build a cycle on unverified
        # balances just because sync did not raise.
        if not self.portfolio.last_sync_ok:
            logger.error(
                "Account truth unavailable; skipping strategy cycle: %s",
                self.portfolio.last_sync_reason,
                extra=structured_log_extra(
                    event="portfolio_sync_unverified",
                    reason=self.portfolio.last_sync_reason,
                ),
            )
            return False

        return True

    def _build_context(
        self,
        now: datetime,
        strategy_config: StrategyConfig,
        timeframe: str,
        regime: RegimeSnapshot,
        allowed_pairs: list[str],
    ) -> StrategyContext:
        dynamic_universe = self.market_data.get_universe()
        if dynamic_universe:
            filtered_universe = [
                pair for pair in allowed_pairs if pair in dynamic_universe
            ]
            if not filtered_universe:
                filtered_universe = list(allowed_pairs)
        else:
            filtered_universe = list(allowed_pairs)

        return StrategyContext(
            now=now,
            universe=filtered_universe,
            market_data=self.market_data,
            portfolio=self.portfolio,
            timeframe=timeframe,
            regime=regime,
        )

    def _get_strategy_pairs(self, strategy_id: str) -> list[str]:
        strat_cfg = self.config.strategies.configs[strategy_id]
        global_universe = list(self.config.universe.include_pairs or [])
        global_excludes = set(self.config.universe.exclude_pairs or [])

        configured_pairs = list(strat_cfg.params.get("pairs") or [])

        if configured_pairs:
            base = configured_pairs
        else:
            base = global_universe

        if not base:
            return []

        return [pair for pair in base if pair not in global_excludes]

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
        return self._cached_risk_status

    def build_emergency_flatten_plan(
        self, positions: Sequence[SpotPosition], reason: str = "Manual flatten all"
    ) -> ExecutionPlan:
        """Construct a flatten-all execution plan for the provided positions."""

        now = datetime.now(timezone.utc)
        actions: list[RiskAdjustedAction] = []
        dust_positions: list[dict[str, Any]] = []
        untradeable_positions: list[dict[str, Any]] = []

        for position in positions:
            if position.base_size == 0:
                continue

            # Fetch metadata
            meta = None
            try:
                meta = self.market_data.get_pair_metadata(position.pair)
            except Exception:
                pass

            if not meta:
                untradeable_positions.append(
                    {
                        "pair": position.pair,
                        "base_size": position.base_size,
                        "reason": "Missing pair metadata",
                    }
                )
                continue

            rounded_close, is_executable = classify_volume(
                meta, abs(position.base_size)
            )
            if not is_executable:
                dust_positions.append(
                    {
                        "pair": position.pair,
                        "base_size": position.base_size,
                        "rounded_close": rounded_close,
                        "min_order_size": meta.min_order_size,
                        "reason": dust_reason(
                            meta, abs(position.base_size), rounded_close
                        ),
                    }
                )
                continue

            strategy_tag = position.strategy_tag or "manual"
            actions.append(
                RiskAdjustedAction(
                    pair=position.pair,
                    strategy_id=strategy_tag,
                    action_type="close",
                    target_base_size=0.0,
                    target_notional_usd=0.0,
                    current_base_size=position.base_size,
                    reason=reason,
                    blocked=False,
                    blocked_reasons=[],
                    strategy_tag=strategy_tag,
                    userref=None,
                    risk_limits_snapshot={},
                )
            )

        # Cap metadata list sizes
        capped_dust = dust_positions[:50]
        capped_untradeable = untradeable_positions[:50]

        plan = ExecutionPlan(
            plan_id=f"flatten_{int(now.timestamp())}",
            generated_at=now,
            actions=actions,
            emergency_reduce_only=True,
            metadata={
                "order_type": "market",
                "dust_positions": capped_dust,
                "untradeable_positions": capped_untradeable,
                "dust_count_total": len(dust_positions),
                "untradeable_count_total": len(untradeable_positions),
            },
        )

        return plan

    def get_strategy_state(self) -> List[StrategyState]:
        return self.get_cached_strategy_state()

    def get_cached_strategy_state(self) -> List[StrategyState]:
        return [
            StrategyState(
                strategy_id=state.strategy_id,
                enabled=state.enabled,
                last_intents_at=state.last_intents_at,
                last_actions_at=state.last_actions_at,
                current_positions=list(state.current_positions),
                pnl_summary=dict(state.pnl_summary),
                last_intents=list(state.last_intents) if state.last_intents else None,
                conflict_summary=(
                    list(state.conflict_summary) if state.conflict_summary else None
                ),
                params=dict(state.params),
                configured_weight=state.configured_weight,
                effective_weight_pct=state.effective_weight_pct,
                last_evaluated_at=state.last_evaluated_at,
                last_evaluation_summary=(
                    dict(state.last_evaluation_summary)
                    if state.last_evaluation_summary
                    else None
                ),
            )
            for state in self._cached_strategy_state
        ]

    def set_strategy_enabled(self, strategy_id: str, enabled: bool) -> None:
        strat_cfg = self.config.strategies.configs.get(strategy_id)
        if strat_cfg is None:
            raise ValueError(f"Strategy {strategy_id} not found")

        previous_state = self.strategy_states.get(strategy_id)
        was_enabled = bool(previous_state.enabled) if previous_state else False

        strat_cfg.enabled = enabled
        state = self._ensure_strategy_state(strat_cfg, enabled=enabled)

        if enabled:
            if strategy_id not in self.config.strategies.enabled:
                self.config.strategies.enabled.append(strategy_id)
            if strategy_id not in self.strategies:
                activated = self._activate_strategy(strat_cfg)
                state.enabled = activated
                if not activated:
                    strat_cfg.enabled = False
                    if strategy_id in self.config.strategies.enabled:
                        self.config.strategies.enabled.remove(strategy_id)
            if state.enabled and not was_enabled:
                state.last_evaluation_summary = self._awaiting_strategy_summary()
        else:
            if strategy_id in self.config.strategies.enabled:
                self.config.strategies.enabled.remove(strategy_id)
            self.strategies.pop(strategy_id, None)
            state.enabled = False
            state.last_evaluation_summary = self._disabled_strategy_summary()

        self.refresh_strategy_weight_state()
        self.refresh_runtime_snapshots()

    def set_manual_kill_switch(self, active: bool) -> None:
        self.risk_engine.set_manual_kill_switch(active)
        self.refresh_runtime_snapshots()

    def clear_manual_kill_switch(self) -> None:
        self.risk_engine.clear_manual_kill_switch()
        self.refresh_runtime_snapshots()


# Backwards compatibility alias
StrategyRiskEngine = StrategyEngine
