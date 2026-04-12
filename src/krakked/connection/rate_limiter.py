# src/krakked/connection/rate_limiter.py

import threading
import time


class RateLimiter:
    """
    A thread-safe token bucket rate limiter using a reservation pattern.
    """

    def __init__(self, calls_per_second: float):
        if calls_per_second <= 0:
            raise ValueError("Rate must be positive")
        self.rate = calls_per_second
        self.interval = 1.0 / self.rate
        # Initialize to 0 so the first call is always immediate
        self.last_call_time = 0.0
        self.lock = threading.Lock()

    def wait(self):
        """
        Blocks until it is safe to make the next call.
        Safe to call from multiple threads without head-of-line blocking.
        """
        with self.lock:
            now = time.monotonic()

            # Calculate the earliest time this specific call can execute.
            # It is either 'now' (if we've been idle) or 'interval' seconds
            # after the previous call (if we are busy).
            # This 'max' logic prevents burst credits from accumulating during idle time.
            target_time = max(now, self.last_call_time + self.interval)

            # Reserve this slot immediately by updating the state.
            # Future callers will see this updated time and schedule themselves after us.
            self.last_call_time = target_time

            # Calculate how long we need to wait
            sleep_duration = target_time - now

        # Crucial: We sleep OUTSIDE the lock.
        # This allows other threads to enter 'wait', reserve their own future slots,
        # and begin sleeping concurrently.
        if sleep_duration > 0:
            time.sleep(sleep_duration)
