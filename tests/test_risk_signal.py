import math
from datetime import datetime, timedelta, timezone

import pytest

from krakked.market_data.models import OHLCBar
from krakked.risk_signal import (
    EWMARiskSignalParams,
    build_ewma_risk_signal,
    ewma_per_bar_variances,
    squared_log_return,
)


def _bars(closes: list[float], *, start_ts: int = 1_700_000_000) -> list[OHLCBar]:
    return [
        OHLCBar(
            timestamp=start_ts + index * 14_400,
            open=close,
            high=close,
            low=close,
            close=close,
            volume=1.0,
        )
        for index, close in enumerate(closes)
    ]


def test_ewma_per_bar_variances_are_deterministic() -> None:
    bars = _bars([100.0, 110.0, 121.0])
    expected_return = math.log(1.1) ** 2

    values = ewma_per_bar_variances(bars, ewma_lambda=0.5, epsilon=1e-12)

    assert values[0] == pytest.approx(1e-12)
    assert values[1] == pytest.approx(expected_return)
    assert values[2] == pytest.approx(expected_return)
    assert squared_log_return(bars[0], bars[1]) == pytest.approx(expected_return)


def test_build_ewma_risk_signal_reports_insufficient_data() -> None:
    params = EWMARiskSignalParams(min_bars=5, lookback_bars=5)

    payload = build_ewma_risk_signal(_bars([100.0, 101.0]), params=params)

    assert payload["available"] is False
    assert payload["status"] == "insufficient_data"
    assert payload["bars_used"] == 2
    assert payload["display_only"] is True
    assert payload["trading_effect"] is False


def test_build_ewma_risk_signal_ready_payload_has_no_trading_effect() -> None:
    bars = _bars([100.0 + index for index in range(90)])
    generated_at = datetime.fromtimestamp(
        bars[-1].timestamp,
        tz=timezone.utc,
    ) + timedelta(hours=1)
    params = EWMARiskSignalParams(min_bars=20, lookback_bars=90)

    payload = build_ewma_risk_signal(
        bars,
        params=params,
        generated_at=generated_at,
    )

    assert payload["available"] is True
    assert payload["status"] == "ready"
    assert payload["source"] == "riskmetrics_ewma"
    assert payload["benchmark_pair"] == "BTC/USD"
    assert payload["timeframe"] == "4h"
    assert payload["ewma_horizon_volatility_pct"] > 0.0
    assert 0.0 <= payload["volatility_percentile"] <= 100.0
    assert payload["risk_level"] in {"normal", "elevated", "stressed"}
    assert payload["display_only"] is True
    assert payload["trading_effect"] is False
    assert payload["runtime_wiring_approved"] is False


def test_build_ewma_risk_signal_marks_stale_cache() -> None:
    bars = _bars([100.0 + index for index in range(30)])
    generated_at = datetime.fromtimestamp(
        bars[-1].timestamp,
        tz=timezone.utc,
    ) + timedelta(days=2)
    params = EWMARiskSignalParams(
        min_bars=20,
        lookback_bars=30,
        stale_after_seconds=3600.0,
    )

    payload = build_ewma_risk_signal(
        bars,
        params=params,
        generated_at=generated_at,
    )

    assert payload["available"] is False
    assert payload["status"] == "stale_data"
    assert payload["ewma_horizon_volatility_pct"] is not None
