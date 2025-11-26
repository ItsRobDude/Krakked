# tests/test_rate_limit.py

import time
import pytest
from kraken_bot.connection.rate_limiter import RateLimiter

def test_rate_limiter_enforces_delay():
    """
    Tests that the rate limiter enforces a delay between calls.
    """
    # 10 calls per second = 0.1s interval
    limiter = RateLimiter(calls_per_second=10)

    start_time = time.monotonic()

    # Make 5 calls
    for _ in range(5):
        limiter.wait()

    end_time = time.monotonic()

    duration = end_time - start_time

    # The total time should be at least 4 intervals (0.4s)
    # We use a small tolerance to account for timing inaccuracies.
    assert duration >= 4 * 0.1 - 0.01

def test_rate_limiter_no_delay_for_slow_calls():
    """
    Tests that if calls are naturally slower than the limit, no extra delay is added.
    """
    limiter = RateLimiter(calls_per_second=5) # 0.2s interval

    start_time = time.monotonic()

    limiter.wait()
    time.sleep(0.3) # Sleep longer than the interval
    limiter.wait()

    end_time = time.monotonic()

    duration = end_time - start_time

    # The duration should be dominated by the sleep, not the limiter.
    # It should be just over 0.3s.
    assert 0.3 <= duration < 0.35

def test_rate_limiter_init_with_zero_rate():
    """
    Tests that initializing with a zero or negative rate raises an error.
    """
    with pytest.raises(ValueError):
        RateLimiter(0)
    with pytest.raises(ValueError):
        RateLimiter(-1)
