
import math
from typing import List, Optional

def compute_features_from_window(
    closes: List[float],
    short_window: int,
    long_window: int,
    lookback_bars: int
) -> Optional[List[float]]:
    """
    Compute ML features from a list of close prices.

    Features:
    1. pct_change: Return of the last bar (relative to T-2).
    2. trend_diff: (Short MA - Long MA) / Long MA.
    3. volatility: Coefficient of Variation (std/mean) over lookback window.

    Args:
        closes: List of close prices. Must include at least 3 bars.
                closes[-1] is the latest close (T-1).
        short_window: Period for short MA.
        long_window: Period for long MA.
        lookback_bars: Period for volatility calc.

    Returns:
        List of [pct_change, trend_diff, volatility] or None if insufficient data.
    """
    if not closes or len(closes) < 3:
        return None

    last_close, prev_close = closes[-1], closes[-2]
    if prev_close <= 0:
        return None

    pct_change = (last_close - prev_close) / prev_close

    short_len = min(short_window, len(closes))
    long_len = min(long_window, len(closes))
    short_ma = sum(closes[-short_len:]) / short_len if short_len > 0 else 0.0
    long_ma = sum(closes[-long_len:]) / long_len if long_len > 0 else 0.0
    trend_diff = ((short_ma - long_ma) / long_ma) if long_ma > 0 else 0.0

    window = closes[-lookback_bars:]
    mean_close = sum(window) / len(window)
    volatility = 0.0
    if mean_close > 0 and len(window) > 1:
        variance = sum((c - mean_close) ** 2 for c in window) / len(window)
        volatility = math.sqrt(variance) / mean_close

    return [pct_change, trend_diff, volatility]
