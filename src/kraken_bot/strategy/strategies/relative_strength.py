from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from kraken_bot.config import StrategyConfig
from kraken_bot.market_data.exceptions import DataStaleError
from kraken_bot.strategy.base import Strategy, StrategyContext
from kraken_bot.strategy.models import StrategyIntent


@dataclass
class RelativeStrengthConfig:
    pairs: List[str]
    lookback_bars: int
    timeframe: str
    rebalance_interval_hours: int
    top_n: int
    total_allocation_pct: float
    risk_profile: str


class RelativeStrengthStrategy(Strategy):
    PROFILE_PRESETS: Dict[str, Dict[str, Any]] = {
        "conservative": {
            "lookback_bars": 72,
            "timeframe": "4h",
            "rebalance_interval_hours": 48,
            "top_n": 1,
            "total_allocation_pct": 10.0,
        },
        "balanced": {
            "lookback_bars": 42,
            "timeframe": "4h",
            "rebalance_interval_hours": 24,
            "top_n": 2,
            "total_allocation_pct": 20.0,
        },
        "aggressive": {
            "lookback_bars": 24,
            "timeframe": "1h",
            "rebalance_interval_hours": 12,
            "top_n": 3,
            "total_allocation_pct": 25.0,
        },
    }

    def __init__(self, base_cfg: StrategyConfig):
        super().__init__(base_cfg)
        params = base_cfg.params or {}
        risk_profile = str(params.get("risk_profile", "balanced")).lower()
        preset = self.PROFILE_PRESETS.get(risk_profile, self.PROFILE_PRESETS["balanced"])

        pairs_param = params.get("pairs") or ["BTC/USD", "ETH/USD"]
        pairs = list(pairs_param) if isinstance(pairs_param, list) else [str(pairs_param)]

        self.params = RelativeStrengthConfig(
            pairs=pairs,
            lookback_bars=max(int(params.get("lookback_bars", preset["lookback_bars"])), 2),
            timeframe=str(params.get("timeframe", preset["timeframe"])),
            rebalance_interval_hours=max(
                int(params.get("rebalance_interval_hours", preset["rebalance_interval_hours"])), 1
            ),
            top_n=max(int(params.get("top_n", preset["top_n"])), 1),
            total_allocation_pct=max(
                float(params.get("total_allocation_pct", preset["total_allocation_pct"])), 0.0
            ),
            risk_profile=risk_profile,
        )
        self._last_rebalance: Optional[datetime] = None

    def warmup(self, market_data, portfolio) -> None:
        return None

    def _rebalance_due(self, now: datetime) -> bool:
        if self._last_rebalance is None:
            return True

        elapsed = now - self._last_rebalance
        return elapsed >= timedelta(hours=self.params.rebalance_interval_hours)

    def _compute_returns(self, ctx: StrategyContext, timeframe: str) -> Dict[str, float]:
        returns: Dict[str, float] = {}
        for pair in self.params.pairs or ctx.universe:
            try:
                ohlc = ctx.market_data.get_ohlc(pair, timeframe, lookback=self.params.lookback_bars)
            except DataStaleError:
                continue

            if not ohlc or len(ohlc) < self.params.lookback_bars:
                continue

            closes = [bar.close for bar in ohlc[-self.params.lookback_bars:]]
            first_close = closes[0]
            last_close = closes[-1]
            if first_close <= 0:
                continue

            returns[pair] = (last_close - first_close) / first_close
        return returns

    def _current_exposure_usd(self, pair: str, positions_by_pair: Dict[str, Any], ctx: StrategyContext) -> float:
        position = positions_by_pair.get(pair)
        if not position or position.base_size <= 0:
            return 0.0

        try:
            price = ctx.market_data.get_latest_price(pair) or 0.0
        except DataStaleError:
            price = 0.0
        except Exception:  # noqa: BLE001
            price = 0.0

        return position.base_size * price

    def generate_intents(self, ctx: StrategyContext) -> List[StrategyIntent]:
        if not self._rebalance_due(ctx.now):
            return []

        timeframe = ctx.timeframe or self.params.timeframe
        returns = self._compute_returns(ctx, timeframe)

        if not returns:
            self._last_rebalance = ctx.now
            return []

        ranked_pairs = sorted(returns.items(), key=lambda item: item[1], reverse=True)
        top_pairs = [pair for pair, _ in ranked_pairs[: self.params.top_n]]

        equity_view = ctx.portfolio.get_equity(include_manual=True)
        if equity_view.equity_base <= 0 or not top_pairs:
            self._last_rebalance = ctx.now
            return []

        total_target_allocation = equity_view.equity_base * (self.params.total_allocation_pct / 100.0)
        if total_target_allocation <= 0:
            self._last_rebalance = ctx.now
            return []

        target_per_asset = total_target_allocation / len(top_pairs)

        positions = ctx.portfolio.get_positions() or []
        positions_by_pair = {
            pos.pair: pos
            for pos in positions
            if getattr(pos, "strategy_tag", None) == self.id and getattr(pos, "base_size", 0) > 0
        }

        intents: List[StrategyIntent] = []

        for pair, ret in ranked_pairs:
            is_top = pair in top_pairs
            position = positions_by_pair.get(pair)

            if not is_top and position:
                intents.append(
                    StrategyIntent(
                        strategy_id=self.id,
                        pair=pair,
                        side="flat",
                        intent_type="exit",
                        desired_exposure_usd=0.0,
                        confidence=1.0,
                        timeframe=timeframe,
                        generated_at=ctx.now,
                        metadata={"relative_return": ret},
                    )
                )
                continue

            if is_top:
                current_usd = self._current_exposure_usd(pair, positions_by_pair, ctx)
                if current_usd < target_per_asset:
                    intents.append(
                        StrategyIntent(
                            strategy_id=self.id,
                            pair=pair,
                            side="long",
                            intent_type="increase" if position else "enter",
                            desired_exposure_usd=target_per_asset,
                            confidence=min(1.0, max(0.0, ret)),
                            timeframe=timeframe,
                            generated_at=ctx.now,
                            metadata={
                                "relative_return": ret,
                                "target_exposure_usd": target_per_asset,
                                "current_exposure_usd": current_usd,
                            },
                        )
                    )

        self._last_rebalance = ctx.now
        return intents
