"""Replay-only research probe for a replacement relative-strength signal."""

from __future__ import annotations

import copy
from bisect import bisect_right
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from statistics import mean, pstdev
from typing import Any, Mapping, Sequence

from krakked.config import AppConfig
from krakked.market_data.models import OHLCBar
from krakked.market_data.ohlc_fetcher import TIMEFRAME_MAP

from .runner import BacktestMarketData, BacktestPreflight

REPORT_TYPE = "rs_rotation_v2_research"
REPORT_VERSION = 1


@dataclass(frozen=True)
class RSRotationV2ResearchParams:
    timeframe: str = "4h"
    lookback_bars: int = 42
    volatility_lookback_bars: int = 42
    rebalance_interval_bars: int = 6
    forward_horizon_bars: int = 6
    top_n: int = 2
    total_allocation_pct: float = 5.0
    starting_cash_usd: float = 10_000.0
    fee_bps: float = 25.0
    slippage_bps: float = 50.0
    edge_buffer_bps: float = 50.0
    min_abs_momentum_bps: float = 0.0
    min_score_gap: float = 0.25
    require_btc_regime: bool = True
    require_basket_regime: bool = True
    benchmark_pair: str = "BTC/USD"
    min_trade_usd: float = 10.0
    min_active_cycles: int = 3
    max_drawdown_pct: float = 5.0

    def __post_init__(self) -> None:
        if self.timeframe not in TIMEFRAME_MAP:
            raise ValueError(f"Unsupported timeframe for v2 research: {self.timeframe}")
        if self.lookback_bars < 2:
            raise ValueError("lookback_bars must be at least 2")
        if self.volatility_lookback_bars < 2:
            raise ValueError("volatility_lookback_bars must be at least 2")
        if self.rebalance_interval_bars < 1:
            raise ValueError("rebalance_interval_bars must be at least 1")
        if self.forward_horizon_bars < 1:
            raise ValueError("forward_horizon_bars must be at least 1")
        if self.top_n < 1:
            raise ValueError("top_n must be at least 1")
        if self.total_allocation_pct < 0:
            raise ValueError("total_allocation_pct must be non-negative")
        if self.starting_cash_usd <= 0:
            raise ValueError("starting_cash_usd must be positive")
        if self.fee_bps < 0 or self.slippage_bps < 0 or self.edge_buffer_bps < 0:
            raise ValueError("cost model bps values must be non-negative")
        if self.min_abs_momentum_bps < 0:
            raise ValueError("min_abs_momentum_bps must be non-negative")
        if self.min_score_gap < 0:
            raise ValueError("min_score_gap must be non-negative")
        if self.min_trade_usd < 0:
            raise ValueError("min_trade_usd must be non-negative")
        if self.min_active_cycles < 0:
            raise ValueError("min_active_cycles must be non-negative")
        if self.max_drawdown_pct < 0:
            raise ValueError("max_drawdown_pct must be non-negative")

    @property
    def round_trip_cost_bps(self) -> float:
        return (2.0 * (self.fee_bps + self.slippage_bps)) + self.edge_buffer_bps

    @property
    def absolute_momentum_hurdle_bps(self) -> float:
        return max(self.min_abs_momentum_bps, self.round_trip_cost_bps)


@dataclass
class RSRotationV2ResearchResult:
    generated_at: datetime
    start: datetime
    end: datetime
    pairs: list[str]
    params: RSRotationV2ResearchParams
    summary: dict[str, Any]
    preflight: dict[str, Any] | None = None
    cycles: list[dict[str, Any]] | None = None
    trades: list[dict[str, Any]] | None = None

    def to_report_dict(self) -> dict[str, Any]:
        return {
            "report_version": REPORT_VERSION,
            "report_type": REPORT_TYPE,
            "generated_at": self.generated_at.astimezone(UTC).isoformat(),
            "summary": copy.deepcopy(self.summary),
            "preflight": copy.deepcopy(self.preflight),
            "cycles": copy.deepcopy(self.cycles or []),
            "trades": copy.deepcopy(self.trades or []),
        }


@dataclass(frozen=True)
class _PairSignal:
    pair: str
    trailing_return: float
    trailing_return_bps: float
    volatility: float
    score: float
    close: float


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), tz=UTC).isoformat()


def _clean_pairs(pairs: Sequence[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for pair in pairs:
        value = str(pair).strip()
        if not value or value in seen:
            continue
        cleaned.append(value)
        seen.add(value)
    return cleaned


def _default_pairs(config: AppConfig) -> list[str]:
    rs_cfg = config.strategies.configs.get("rs_rotation")
    params = rs_cfg.params if rs_cfg is not None else {}
    strategy_pairs = params.get("pairs") if isinstance(params, dict) else None
    if isinstance(strategy_pairs, list) and strategy_pairs:
        return _clean_pairs([str(pair) for pair in strategy_pairs])
    return _clean_pairs(list(config.universe.include_pairs))


def default_rs_rotation_v2_timeframe(config: AppConfig) -> str:
    rs_cfg = config.strategies.configs.get("rs_rotation")
    params = rs_cfg.params if rs_cfg is not None else {}
    if isinstance(params, dict):
        timeframe = str(params.get("timeframe") or "").strip()
        if timeframe:
            return timeframe
    return "4h"


def default_rs_rotation_v2_lookback_bars(config: AppConfig) -> int:
    rs_cfg = config.strategies.configs.get("rs_rotation")
    params = rs_cfg.params if rs_cfg is not None else {}
    if isinstance(params, dict):
        try:
            return max(int(params.get("lookback_bars", 42)), 2)
        except (TypeError, ValueError):
            pass
    return 42


def default_rs_rotation_v2_top_n(config: AppConfig) -> int:
    rs_cfg = config.strategies.configs.get("rs_rotation")
    params = rs_cfg.params if rs_cfg is not None else {}
    if isinstance(params, dict):
        try:
            return max(int(params.get("top_n", 2)), 1)
        except (TypeError, ValueError):
            pass
    return 2


def default_rs_rotation_v2_allocation_pct(config: AppConfig) -> float:
    rs_cfg = config.strategies.configs.get("rs_rotation")
    params = rs_cfg.params if rs_cfg is not None else {}
    if isinstance(params, dict):
        try:
            return max(float(params.get("total_allocation_pct", 5.0)), 0.0)
        except (TypeError, ValueError):
            pass
    return 5.0


def _bar_window(
    bars: Sequence[OHLCBar], timestamps: Sequence[int], ts: int, lookback: int
) -> list[OHLCBar]:
    end_index = bisect_right(timestamps, int(ts))
    if end_index <= 0:
        return []
    start_index = max(0, end_index - lookback)
    return list(bars[start_index:end_index])


def _bar_at(
    bars: Sequence[OHLCBar], timestamps: Sequence[int], ts: int
) -> OHLCBar | None:
    end_index = bisect_right(timestamps, int(ts))
    if end_index <= 0:
        return None
    return bars[end_index - 1]


def _bar_after_horizon(
    bars: Sequence[OHLCBar],
    timestamps: Sequence[int],
    ts: int,
    horizon_bars: int,
) -> OHLCBar | None:
    current_index = bisect_right(timestamps, int(ts)) - 1
    future_index = current_index + horizon_bars
    if current_index < 0 or future_index >= len(bars):
        return None
    return bars[future_index]


def _compute_signal(
    pair: str,
    bars: Sequence[OHLCBar],
    timestamps: Sequence[int],
    ts: int,
    params: RSRotationV2ResearchParams,
) -> _PairSignal | None:
    lookback = max(params.lookback_bars, params.volatility_lookback_bars)
    window = _bar_window(bars, timestamps, ts, lookback)
    if len(window) < lookback:
        return None

    momentum_window = window[-params.lookback_bars :]
    volatility_window = window[-params.volatility_lookback_bars :]
    first_close = float(momentum_window[0].close)
    last_close = float(momentum_window[-1].close)
    if first_close <= 0.0 or last_close <= 0.0:
        return None

    returns: list[float] = []
    for prev, current in zip(volatility_window, volatility_window[1:]):
        prev_close = float(prev.close)
        current_close = float(current.close)
        if prev_close <= 0.0 or current_close <= 0.0:
            continue
        returns.append((current_close - prev_close) / prev_close)

    if len(returns) < 2:
        return None

    trailing_return = (last_close - first_close) / first_close
    volatility = max(pstdev(returns), 1e-9)
    return _PairSignal(
        pair=pair,
        trailing_return=trailing_return,
        trailing_return_bps=trailing_return * 10_000.0,
        volatility=volatility,
        score=trailing_return / volatility,
        close=last_close,
    )


def _selected_with_hysteresis(
    eligible: Sequence[_PairSignal],
    held_pairs: set[str],
    params: RSRotationV2ResearchParams,
) -> list[_PairSignal]:
    ranked = sorted(eligible, key=lambda item: item.score, reverse=True)
    selected = list(ranked[: params.top_n])
    selected_pairs = {item.pair for item in selected}
    signal_by_pair = {item.pair: item for item in ranked}

    for held_pair in sorted(held_pairs):
        held_signal = signal_by_pair.get(held_pair)
        if held_signal is None or held_pair in selected_pairs:
            continue
        if len(selected) < params.top_n:
            selected.append(held_signal)
            selected_pairs.add(held_pair)
            continue

        worst = min(selected, key=lambda item: item.score)
        if worst.score - held_signal.score < params.min_score_gap:
            selected_pairs.discard(worst.pair)
            selected = [item for item in selected if item.pair != worst.pair]
            selected.append(held_signal)
            selected_pairs.add(held_pair)

    return sorted(selected, key=lambda item: item.score, reverse=True)


def _mark_equity(
    cash_usd: float,
    positions: Mapping[str, float],
    prices: Mapping[str, float],
) -> float:
    return cash_usd + sum(
        float(qty) * float(prices.get(pair, 0.0)) for pair, qty in positions.items()
    )


def _trade_payload(
    *,
    side: str,
    pair: str,
    timestamp: int,
    close_price: float,
    fill_price: float,
    quantity: float,
    notional_usd: float,
    fee_usd: float,
    slippage_estimate_usd: float,
    reason: str,
) -> dict[str, Any]:
    return {
        "timestamp": int(timestamp),
        "time": _iso(timestamp),
        "pair": pair,
        "side": side,
        "close_price": close_price,
        "fill_price": fill_price,
        "quantity": quantity,
        "notional_usd": notional_usd,
        "fee_usd": fee_usd,
        "slippage_estimate_usd": slippage_estimate_usd,
        "reason": reason,
    }


def _forward_diagnostics(
    *,
    cycles: Sequence[dict[str, Any]],
    bars_by_pair: Mapping[str, Sequence[OHLCBar]],
    timestamps_by_pair: Mapping[str, Sequence[int]],
    params: RSRotationV2ResearchParams,
) -> dict[str, Any]:
    selected_returns: list[float] = []
    universe_returns: list[float] = []
    spreads: list[float] = []
    positive_selected = 0
    beats_universe = 0

    for cycle in cycles:
        selected = list(cycle.get("selected_pairs") or [])
        if not selected:
            continue
        ts = int(cycle["timestamp"])
        selected_pair_returns: list[float] = []
        all_pair_returns: list[float] = []

        for pair, bars in bars_by_pair.items():
            timestamps = timestamps_by_pair[pair]
            current_bar = _bar_at(bars, timestamps, ts)
            future_bar = _bar_after_horizon(
                bars,
                timestamps,
                ts,
                params.forward_horizon_bars,
            )
            if (
                current_bar is None
                or future_bar is None
                or float(current_bar.close) <= 0.0
            ):
                continue
            forward_return = (
                float(future_bar.close) - float(current_bar.close)
            ) / float(current_bar.close)
            all_pair_returns.append(forward_return)
            if pair in selected:
                selected_pair_returns.append(forward_return)

        if not selected_pair_returns or not all_pair_returns:
            continue

        selected_mean = mean(selected_pair_returns)
        universe_mean = mean(all_pair_returns)
        selected_returns.append(selected_mean)
        universe_returns.append(universe_mean)
        spread = selected_mean - universe_mean
        spreads.append(spread)
        if selected_mean > 0.0:
            positive_selected += 1
        if selected_mean > universe_mean:
            beats_universe += 1

    count = len(selected_returns)
    return {
        "horizon_bars": params.forward_horizon_bars,
        "evaluated_cycles": count,
        "mean_selected_forward_return_pct": (
            mean(selected_returns) * 100.0 if selected_returns else None
        ),
        "mean_universe_forward_return_pct": (
            mean(universe_returns) * 100.0 if universe_returns else None
        ),
        "mean_selected_spread_pct": mean(spreads) * 100.0 if spreads else None,
        "positive_selected_cycle_rate": (positive_selected / count if count else None),
        "beats_universe_cycle_rate": beats_universe / count if count else None,
    }


def _equal_weight_reference(
    *,
    cycle_timestamps: Sequence[int],
    bars_by_pair: Mapping[str, Sequence[OHLCBar]],
    timestamps_by_pair: Mapping[str, Sequence[int]],
    params: RSRotationV2ResearchParams,
) -> dict[str, Any] | None:
    if not cycle_timestamps:
        return None

    first_ts = int(cycle_timestamps[0])
    last_ts = int(cycle_timestamps[-1])
    returns: list[float] = []
    for pair, bars in bars_by_pair.items():
        timestamps = timestamps_by_pair[pair]
        first_bar = _bar_at(bars, timestamps, first_ts)
        last_bar = _bar_at(bars, timestamps, last_ts)
        if first_bar is None or last_bar is None or float(first_bar.close) <= 0.0:
            continue
        returns.append(
            (float(last_bar.close) - float(first_bar.close)) / float(first_bar.close)
        )

    if not returns:
        return None

    mean_asset_return = mean(returns)
    return_pct = mean_asset_return * params.total_allocation_pct
    ending_equity_usd = params.starting_cash_usd * (1.0 + (return_pct / 100.0))
    return {
        "mean_asset_return_pct": mean_asset_return * 100.0,
        "allocation_pct": params.total_allocation_pct,
        "return_pct": return_pct,
        "ending_equity_usd": ending_equity_usd,
        "pair_count": len(returns),
    }


def evaluate_rs_rotation_v2_bars(
    bars_by_pair: Mapping[str, Sequence[OHLCBar]],
    *,
    start: datetime,
    end: datetime,
    params: RSRotationV2ResearchParams,
    preflight: BacktestPreflight | dict[str, Any] | None = None,
    warnings: Sequence[str] | None = None,
) -> RSRotationV2ResearchResult:
    start = _as_utc(start)
    end = _as_utc(end)
    cleaned_bars: dict[str, list[OHLCBar]] = {
        pair: sorted(list(bars), key=lambda bar: int(bar.timestamp))
        for pair, bars in bars_by_pair.items()
    }
    cleaned_bars = {pair: bars for pair, bars in cleaned_bars.items() if bars}
    timestamps_by_pair = {
        pair: [int(bar.timestamp) for bar in bars]
        for pair, bars in cleaned_bars.items()
    }
    pairs = list(cleaned_bars)
    timeline = sorted(
        {ts for timestamps in timestamps_by_pair.values() for ts in timestamps}
    )
    warnings_list = list(warnings or [])

    cash_usd = params.starting_cash_usd
    positions: dict[str, float] = {}
    trades: list[dict[str, Any]] = []
    cycles: list[dict[str, Any]] = []
    selection_counts: dict[str, int] = {pair: 0 for pair in pairs}
    total_turnover_usd = 0.0
    total_fees_usd = 0.0
    total_slippage_estimate_usd = 0.0
    active_cycles = 0
    cash_cycles = 0
    peak_equity = params.starting_cash_usd
    max_drawdown_pct = 0.0
    cycle_timestamps: list[int] = []

    if not timeline:
        warnings_list.append("No OHLC bars were available inside the requested window.")

    for timeline_index, ts in enumerate(timeline):
        if timeline_index % params.rebalance_interval_bars != 0:
            continue

        signals = [
            signal
            for pair, bars in cleaned_bars.items()
            if (
                signal := _compute_signal(
                    pair,
                    bars,
                    timestamps_by_pair[pair],
                    ts,
                    params,
                )
            )
            is not None
        ]
        signal_by_pair = {signal.pair: signal for signal in signals}

        btc_regime_ok = True
        if params.require_btc_regime:
            benchmark_signal = signal_by_pair.get(params.benchmark_pair)
            btc_regime_ok = (
                benchmark_signal is not None and benchmark_signal.trailing_return > 0.0
            )

        basket_regime_ok = True
        if params.require_basket_regime:
            basket_regime_ok = (
                bool(signals)
                and mean(signal.trailing_return for signal in signals) > 0.0
            )

        regime_ok = btc_regime_ok and basket_regime_ok
        hurdle_bps = params.absolute_momentum_hurdle_bps
        eligible = [
            signal
            for signal in signals
            if regime_ok and signal.trailing_return_bps > hurdle_bps
        ]
        held_pairs = {
            pair
            for pair, quantity in positions.items()
            if quantity > 0.0 and pair in signal_by_pair
        }
        selected_signals = _selected_with_hysteresis(eligible, held_pairs, params)
        selected_pairs = [signal.pair for signal in selected_signals]

        prices: dict[str, float] = {}
        for pair, bars in cleaned_bars.items():
            bar = _bar_at(bars, timestamps_by_pair[pair], ts)
            if bar is not None:
                prices[pair] = float(bar.close)

        before_equity = _mark_equity(cash_usd, positions, prices)
        target_total_usd = (
            before_equity * (params.total_allocation_pct / 100.0)
            if selected_pairs
            else 0.0
        )
        target_per_pair_usd = (
            target_total_usd / len(selected_pairs) if selected_pairs else 0.0
        )
        target_exposures = {
            pair: (target_per_pair_usd if pair in selected_pairs else 0.0)
            for pair in prices
        }

        # Sell first so rotations free cash before buys.
        for pair, quantity in list(positions.items()):
            price = prices.get(pair)
            if price is None or price <= 0.0 or quantity <= 0.0:
                continue
            current_value = quantity * price
            target_value = target_exposures.get(pair, 0.0)
            value_delta = current_value - target_value
            if value_delta <= params.min_trade_usd:
                continue
            sell_quantity = min(quantity, value_delta / price)
            fill_price = price * (1.0 - (params.slippage_bps / 10_000.0))
            notional = sell_quantity * fill_price
            fee = notional * (params.fee_bps / 10_000.0)
            slippage = sell_quantity * price * (params.slippage_bps / 10_000.0)
            cash_usd += notional - fee
            remaining = quantity - sell_quantity
            if remaining <= 1e-12:
                positions.pop(pair, None)
            else:
                positions[pair] = remaining
            total_turnover_usd += notional
            total_fees_usd += fee
            total_slippage_estimate_usd += slippage
            trades.append(
                _trade_payload(
                    side="sell",
                    pair=pair,
                    timestamp=ts,
                    close_price=price,
                    fill_price=fill_price,
                    quantity=sell_quantity,
                    notional_usd=notional,
                    fee_usd=fee,
                    slippage_estimate_usd=slippage,
                    reason="rebalance_to_cash_or_new_rank",
                )
            )

        for pair in selected_pairs:
            price = prices.get(pair)
            if price is None or price <= 0.0:
                continue
            current_quantity = positions.get(pair, 0.0)
            current_value = current_quantity * price
            target_value = target_exposures.get(pair, 0.0)
            value_delta = target_value - current_value
            if value_delta <= params.min_trade_usd or cash_usd <= params.min_trade_usd:
                continue
            fill_price = price * (1.0 + (params.slippage_bps / 10_000.0))
            desired_notional = min(value_delta, cash_usd)
            fee = desired_notional * (params.fee_bps / 10_000.0)
            total_cost = desired_notional + fee
            if total_cost > cash_usd:
                desired_notional = cash_usd / (1.0 + (params.fee_bps / 10_000.0))
                fee = desired_notional * (params.fee_bps / 10_000.0)
                total_cost = desired_notional + fee
            if desired_notional <= params.min_trade_usd:
                continue
            buy_quantity = desired_notional / fill_price
            slippage = buy_quantity * price * (params.slippage_bps / 10_000.0)
            cash_usd -= total_cost
            positions[pair] = current_quantity + buy_quantity
            total_turnover_usd += desired_notional
            total_fees_usd += fee
            total_slippage_estimate_usd += slippage
            trades.append(
                _trade_payload(
                    side="buy",
                    pair=pair,
                    timestamp=ts,
                    close_price=price,
                    fill_price=fill_price,
                    quantity=buy_quantity,
                    notional_usd=desired_notional,
                    fee_usd=fee,
                    slippage_estimate_usd=slippage,
                    reason="rebalance_to_v2_rank",
                )
            )

        after_equity = _mark_equity(cash_usd, positions, prices)
        peak_equity = max(peak_equity, after_equity)
        if peak_equity > 0.0:
            drawdown = ((peak_equity - after_equity) / peak_equity) * 100.0
            max_drawdown_pct = max(max_drawdown_pct, drawdown)

        if selected_pairs:
            active_cycles += 1
            for pair in selected_pairs:
                selection_counts[pair] = selection_counts.get(pair, 0) + 1
        else:
            cash_cycles += 1

        cycle_timestamps.append(ts)
        cycles.append(
            {
                "timestamp": int(ts),
                "time": _iso(ts),
                "selected_pairs": selected_pairs,
                "eligible_count": len(eligible),
                "btc_regime_ok": btc_regime_ok,
                "basket_regime_ok": basket_regime_ok,
                "before_equity_usd": before_equity,
                "after_equity_usd": after_equity,
                "cash_usd": cash_usd,
                "top_scores": [
                    {
                        "pair": signal.pair,
                        "score": signal.score,
                        "trailing_return_bps": signal.trailing_return_bps,
                        "volatility": signal.volatility,
                    }
                    for signal in sorted(
                        signals, key=lambda item: item.score, reverse=True
                    )[: max(params.top_n, 3)]
                ],
            }
        )

    final_prices = {
        pair: float(bars[-1].close)
        for pair, bars in cleaned_bars.items()
        if bars and float(bars[-1].close) > 0.0
    }
    ending_equity_usd = _mark_equity(cash_usd, positions, final_prices)
    absolute_pnl_usd = ending_equity_usd - params.starting_cash_usd
    return_pct = (absolute_pnl_usd / params.starting_cash_usd) * 100.0
    equal_weight_reference = _equal_weight_reference(
        cycle_timestamps=cycle_timestamps,
        bars_by_pair=cleaned_bars,
        timestamps_by_pair=timestamps_by_pair,
        params=params,
    )
    forward = _forward_diagnostics(
        cycles=cycles,
        bars_by_pair=cleaned_bars,
        timestamps_by_pair=timestamps_by_pair,
        params=params,
    )

    gates = {
        "positive_return_after_costs": {
            "passed": return_pct > 0.0,
            "value": return_pct,
            "threshold": 0.0,
        },
        "beats_equal_weight_reference": {
            "passed": (
                equal_weight_reference is not None
                and return_pct > float(equal_weight_reference["return_pct"])
            ),
            "value": return_pct,
            "threshold": (
                None
                if equal_weight_reference is None
                else equal_weight_reference["return_pct"]
            ),
        },
        "enough_active_cycles": {
            "passed": active_cycles >= params.min_active_cycles,
            "value": active_cycles,
            "threshold": params.min_active_cycles,
        },
        "drawdown_under_limit": {
            "passed": max_drawdown_pct <= params.max_drawdown_pct,
            "value": max_drawdown_pct,
            "threshold": params.max_drawdown_pct,
        },
    }
    gate_failures = [
        name
        for name, payload in gates.items()
        if isinstance(payload, dict) and not bool(payload.get("passed"))
    ]
    if not cycles:
        status = "insufficient_data"
    elif gate_failures:
        status = "research_fail"
    else:
        status = "research_pass"

    params_dict = asdict(params)
    params_dict["round_trip_cost_bps"] = params.round_trip_cost_bps
    params_dict["absolute_momentum_hurdle_bps"] = params.absolute_momentum_hurdle_bps
    summary = {
        "strategy_id": "rs_rotation_v2",
        "status": status,
        "gate_failures": gate_failures,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "pairs": pairs,
        "timeframe": params.timeframe,
        "starting_cash_usd": params.starting_cash_usd,
        "ending_equity_usd": ending_equity_usd,
        "absolute_pnl_usd": absolute_pnl_usd,
        "return_pct": return_pct,
        "max_drawdown_pct": max_drawdown_pct,
        "filled_orders": len(trades),
        "blocked_actions": 0,
        "execution_errors": 0,
        "total_cycles": len(cycles),
        "active_cycles": active_cycles,
        "cash_cycles": cash_cycles,
        "trade_count": len(trades),
        "buy_count": sum(1 for trade in trades if trade["side"] == "buy"),
        "sell_count": sum(1 for trade in trades if trade["side"] == "sell"),
        "turnover_usd": total_turnover_usd,
        "fees_usd": total_fees_usd,
        "slippage_estimate_usd": total_slippage_estimate_usd,
        "selection_counts": selection_counts,
        "forward_diagnostics": forward,
        "equal_weight_reference": equal_weight_reference,
        "gates": gates,
        "warnings": warnings_list,
        "notable_warnings": warnings_list,
        "cost_model": (
            "Cycle-close fills using configured taker fees, one-way slippage, "
            "and a cost-plus-buffer absolute momentum hurdle."
        ),
        "params": params_dict,
        "per_strategy": {
            "rs_rotation_v2": {
                "realized_pnl_usd": absolute_pnl_usd,
                "trade_count": len(trades),
                "winning_trades": None,
                "losing_trades": None,
            }
        },
        "replay_inputs": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "pairs": pairs,
            "timeframe": params.timeframe,
            "strict_data": False,
            "research_only": True,
            "params": params_dict,
        },
    }

    if preflight is None:
        preflight_payload = None
    elif isinstance(preflight, BacktestPreflight):
        preflight_payload = preflight.to_dict()
    else:
        preflight_payload = copy.deepcopy(dict(preflight))

    return RSRotationV2ResearchResult(
        generated_at=datetime.now(UTC),
        start=start,
        end=end,
        pairs=pairs,
        params=params,
        summary=summary,
        preflight=preflight_payload,
        cycles=cycles,
        trades=trades,
    )


def run_rs_rotation_v2_research(
    config: AppConfig,
    *,
    start: datetime,
    end: datetime,
    pairs: Sequence[str] | None = None,
    params: RSRotationV2ResearchParams | None = None,
    strict_data: bool = False,
) -> RSRotationV2ResearchResult:
    params = params or RSRotationV2ResearchParams(
        timeframe=default_rs_rotation_v2_timeframe(config),
        lookback_bars=default_rs_rotation_v2_lookback_bars(config),
        volatility_lookback_bars=default_rs_rotation_v2_lookback_bars(config),
        top_n=default_rs_rotation_v2_top_n(config),
        total_allocation_pct=default_rs_rotation_v2_allocation_pct(config),
        slippage_bps=float(config.execution.max_slippage_bps),
    )
    selected_pairs = _clean_pairs(list(pairs or _default_pairs(config)))
    if not selected_pairs:
        raise ValueError("No pairs configured for rs_rotation_v2 research")

    start = _as_utc(start)
    end = _as_utc(end)
    market_data = BacktestMarketData(
        config,
        pairs=selected_pairs,
        timeframes=[params.timeframe],
        start=start,
        end=end,
    )
    try:
        preflight = market_data.get_preflight()
        if strict_data and (preflight.missing_series or preflight.partial_series):
            problems = []
            if preflight.missing_series:
                problems.append("missing: " + ", ".join(preflight.missing_series))
            if preflight.partial_series:
                problems.append("partial: " + ", ".join(preflight.partial_series))
            raise ValueError(
                "rs_rotation_v2 research failed in strict mode: " + "; ".join(problems)
            )

        market_data.set_time(end)
        bars_by_pair = {
            pair: market_data.get_ohlc(pair, params.timeframe, lookback=1_000_000)
            for pair in selected_pairs
        }
        warnings = list(preflight.warnings)
        if preflight.missing_series:
            warnings.append(
                "Some requested OHLC series are missing: "
                + ", ".join(preflight.missing_series)
            )
        if preflight.partial_series:
            warnings.append(
                "Some requested OHLC series only partially cover the window: "
                + ", ".join(preflight.partial_series)
            )

        result = evaluate_rs_rotation_v2_bars(
            bars_by_pair,
            start=start,
            end=end,
            params=params,
            preflight=preflight,
            warnings=warnings,
        )
        result.summary["replay_inputs"]["strict_data"] = strict_data
        return result
    finally:
        shutdown = getattr(market_data, "shutdown", None)
        if callable(shutdown):
            shutdown()
