from datetime import datetime, timezone
from types import SimpleNamespace
from typing import cast

from krakked.config import AppConfig
from krakked.market_data.api import MarketDataAPI
from krakked.strategy.engine import StrategyEngine
from krakked.strategy.models import RiskAdjustedAction, StrategyIntent, StrategyState


def _build_engine() -> StrategyEngine:
    engine = StrategyEngine.__new__(StrategyEngine)
    engine.config = cast(AppConfig, SimpleNamespace())
    engine.market_data = cast(
        MarketDataAPI,
        SimpleNamespace(
            get_display_pair=lambda pair: {"XBTUSD": "BTC/USD"}.get(pair, pair)
        ),
    )
    engine.strategy_states = {
        "trend_core": StrategyState(
            strategy_id="trend_core",
            enabled=True,
            last_intents_at=None,
            last_actions_at=None,
            current_positions=[],
            pnl_summary={},
            configured_weight=100,
            effective_weight_pct=65.0,
        ),
        "vol_breakout": StrategyState(
            strategy_id="vol_breakout",
            enabled=True,
            last_intents_at=None,
            last_actions_at=None,
            current_positions=[],
            pnl_summary={},
            configured_weight=60,
            effective_weight_pct=35.0,
        ),
    }
    return engine


def test_build_conflict_summaries_picks_highest_effective_share():
    engine = _build_engine()
    now = datetime.now(timezone.utc)

    intents = [
        StrategyIntent(
            strategy_id="trend_core",
            pair="XBTUSD",
            side="long",
            intent_type="enter",
            desired_exposure_usd=100.0,
            confidence=0.8,
            timeframe="1h",
            generated_at=now,
        ),
        StrategyIntent(
            strategy_id="vol_breakout",
            pair="XBTUSD",
            side="flat",
            intent_type="exit",
            desired_exposure_usd=0.0,
            confidence=0.7,
            timeframe="1h",
            generated_at=now,
        ),
    ]
    actions = [
        RiskAdjustedAction(
            pair="XBTUSD",
            strategy_id="trend_core",
            action_type="open",
            target_base_size=1.0,
            target_notional_usd=100.0,
            current_base_size=0.0,
            reason="winner",
            blocked=False,
            blocked_reasons=[],
        )
    ]

    summaries = engine._build_conflict_summaries(intents, actions)

    assert summaries["trend_core"][0]["pair"] == "BTC/USD"
    assert summaries["trend_core"][0]["winner_strategy_id"] == "trend_core"
    assert summaries["trend_core"][0]["winning_reason"] == "higher effective share"
    assert summaries["trend_core"][0]["outcome"] == "winner"
    assert summaries["vol_breakout"][0]["outcome"] == "loser"


def test_build_conflict_summaries_reports_netted_out_actions():
    engine = _build_engine()
    now = datetime.now(timezone.utc)

    intents = [
        StrategyIntent(
            strategy_id="trend_core",
            pair="XBTUSD",
            side="long",
            intent_type="enter",
            desired_exposure_usd=100.0,
            confidence=0.8,
            timeframe="1h",
            generated_at=now,
        ),
        StrategyIntent(
            strategy_id="vol_breakout",
            pair="XBTUSD",
            side="flat",
            intent_type="exit",
            desired_exposure_usd=0.0,
            confidence=0.7,
            timeframe="1h",
            generated_at=now,
        ),
    ]
    actions = [
        RiskAdjustedAction(
            pair="XBTUSD",
            strategy_id="trend_core,vol_breakout",
            action_type="none",
            target_base_size=0.0,
            target_notional_usd=0.0,
            current_base_size=0.0,
            reason="netted out",
            blocked=True,
            blocked_reasons=["risk"],
        )
    ]

    summaries = engine._build_conflict_summaries(intents, actions)

    assert summaries["trend_core"][0]["winner_strategy_id"] is None
    assert summaries["trend_core"][0]["winning_reason"] == "risk blocked competing intent"
    assert summaries["trend_core"][0]["outcome"] == "netted_out"
    assert summaries["vol_breakout"][0]["outcome"] == "netted_out"
