from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Optional

FEE_ADJUSTED_CLASSIFICATION_LABEL_TYPE = "fee_adjusted_classification"
DEFAULT_LABEL_FEE_BPS = 25.0
DEFAULT_LABEL_SLIPPAGE_BPS = 50.0
DEFAULT_LABEL_COST_MULTIPLIER = 2.0
DEFAULT_EDGE_FEE_BPS = 25.0
DEFAULT_EDGE_COST_MULTIPLIER = 1.0


def _nonnegative_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(parsed):
        return default
    return max(parsed, 0.0)


def _format_key_number(value: float) -> str:
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return text.replace(".", "p")


def _execution_slippage_bps(ctx: Any) -> float:
    portfolio = getattr(ctx, "portfolio", None)
    app_config = getattr(portfolio, "app_config", None)
    execution = getattr(app_config, "execution", None)
    value = getattr(execution, "max_slippage_bps", None)
    if isinstance(value, (int, float)):
        return _nonnegative_float(value, DEFAULT_LABEL_SLIPPAGE_BPS)
    return DEFAULT_LABEL_SLIPPAGE_BPS


@dataclass(frozen=True)
class FeeAdjustedLabelConfig:
    fee_bps: float = DEFAULT_LABEL_FEE_BPS
    slippage_bps: float = DEFAULT_LABEL_SLIPPAGE_BPS
    cost_multiplier: float = DEFAULT_LABEL_COST_MULTIPLIER

    @property
    def round_trip_cost_bps(self) -> float:
        return 2.0 * (self.fee_bps + self.slippage_bps)

    @property
    def hurdle_bps(self) -> float:
        return self.round_trip_cost_bps * self.cost_multiplier

    @property
    def hurdle_pct(self) -> float:
        return self.hurdle_bps / 10_000.0

    def to_metadata(self) -> dict[str, float | str]:
        return {
            "label_type": FEE_ADJUSTED_CLASSIFICATION_LABEL_TYPE,
            "label_fee_bps": self.fee_bps,
            "label_slippage_bps": self.slippage_bps,
            "label_cost_multiplier": self.cost_multiplier,
            "label_round_trip_cost_bps": self.round_trip_cost_bps,
            "label_hurdle_bps": self.hurdle_bps,
        }

    def model_key_suffix(self) -> str:
        return (
            "fee_adj"
            f"_fee{_format_key_number(self.fee_bps)}"
            f"_slip{_format_key_number(self.slippage_bps)}"
            f"_x{_format_key_number(self.cost_multiplier)}"
        )


@dataclass(frozen=True)
class FeeAdjustedClassificationLabel:
    value: int
    realized_return_pct: float
    hurdle_pct: float
    hurdle_bps: float


@dataclass(frozen=True)
class MLEdgeCostConfig:
    fee_bps: float = DEFAULT_EDGE_FEE_BPS
    slippage_bps: float = DEFAULT_LABEL_SLIPPAGE_BPS
    cost_multiplier: float = DEFAULT_EDGE_COST_MULTIPLIER

    @property
    def round_trip_cost_bps(self) -> float:
        return 2.0 * (self.fee_bps + self.slippage_bps)

    @property
    def round_trip_cost_pct(self) -> float:
        return self.round_trip_cost_bps / 10_000.0

    @property
    def hurdle_pct(self) -> float:
        return self.round_trip_cost_pct * self.cost_multiplier

    def effective_min_edge_pct(self, configured_min_edge_pct: float) -> float:
        return max(configured_min_edge_pct, self.hurdle_pct)

    def to_metadata(self) -> dict[str, float]:
        return {
            "edge_fee_bps": self.fee_bps,
            "edge_slippage_bps": self.slippage_bps,
            "edge_cost_multiplier": self.cost_multiplier,
            "round_trip_cost_pct": self.round_trip_cost_pct,
        }


def label_config_from_context(
    params: Mapping[str, object],
    ctx: Any,
) -> FeeAdjustedLabelConfig:
    fee_bps = _nonnegative_float(params.get("label_fee_bps"), DEFAULT_LABEL_FEE_BPS)
    slippage_source = params.get("label_slippage_bps")
    slippage_bps = (
        _nonnegative_float(slippage_source, DEFAULT_LABEL_SLIPPAGE_BPS)
        if slippage_source is not None
        else _execution_slippage_bps(ctx)
    )
    cost_multiplier = _nonnegative_float(
        params.get("label_cost_multiplier"), DEFAULT_LABEL_COST_MULTIPLIER
    )
    return FeeAdjustedLabelConfig(
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
        cost_multiplier=cost_multiplier,
    )


def edge_cost_config_from_context(
    params: Mapping[str, object],
    ctx: Any,
) -> MLEdgeCostConfig:
    fee_bps = _nonnegative_float(params.get("edge_fee_bps"), DEFAULT_EDGE_FEE_BPS)
    slippage_source = params.get("edge_slippage_bps")
    slippage_bps = (
        _nonnegative_float(slippage_source, DEFAULT_LABEL_SLIPPAGE_BPS)
        if slippage_source is not None
        else _execution_slippage_bps(ctx)
    )
    cost_multiplier = _nonnegative_float(
        params.get("edge_cost_multiplier"), DEFAULT_EDGE_COST_MULTIPLIER
    )
    return MLEdgeCostConfig(
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
        cost_multiplier=cost_multiplier,
    )


def classify_fee_adjusted_return(
    previous_close: float,
    next_close: float,
    label_config: FeeAdjustedLabelConfig,
) -> Optional[FeeAdjustedClassificationLabel]:
    if previous_close <= 0:
        return None

    realized_return = (next_close - previous_close) / previous_close
    value = 1 if realized_return > label_config.hurdle_pct else 0
    return FeeAdjustedClassificationLabel(
        value=value,
        realized_return_pct=realized_return,
        hurdle_pct=label_config.hurdle_pct,
        hurdle_bps=label_config.hurdle_bps,
    )


__all__ = [
    "DEFAULT_LABEL_COST_MULTIPLIER",
    "DEFAULT_EDGE_COST_MULTIPLIER",
    "DEFAULT_EDGE_FEE_BPS",
    "DEFAULT_LABEL_FEE_BPS",
    "DEFAULT_LABEL_SLIPPAGE_BPS",
    "FEE_ADJUSTED_CLASSIFICATION_LABEL_TYPE",
    "FeeAdjustedClassificationLabel",
    "FeeAdjustedLabelConfig",
    "MLEdgeCostConfig",
    "classify_fee_adjusted_return",
    "edge_cost_config_from_context",
    "label_config_from_context",
]
