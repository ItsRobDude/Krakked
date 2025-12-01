# src/kraken_bot/connection/rate_limiter.py

import time
import threading

class RateLimiter:
    """
    A simple token bucket rate limiter.
    """
    def __init__(self, calls_per_second: float):
        if calls_per_second <= 0:
            raise ValueError("Rate must be positive")
        self.rate = calls_per_second
        self.interval = 1.0 / self.rate
        self.last_call_time = 0
        self.lock = threading.Lock()

    def wait(self):
        """
        Blocks until it is safe to make the next call.
        """
        with self.lock:
            now = time.monotonic()
            elapsed = now - self.last_call_time

            if elapsed < self.interval:
                sleep_time = self.interval - elapsed
                time.sleep(sleep_time)
                # Anchor the next allowed time to the ideal schedule to avoid
                # cumulative drift from shorter-than-expected sleeps.
                self.last_call_time = max(self.last_call_time + self.interval, time.monotonic())
            else:
                self.last_call_time = now
