import time
import threading

class NonceGenerator:
    """
    Generates strictly increasing, unique nonces for API requests.

    This class is thread-safe and ensures that even rapid, concurrent calls
    produce a unique, monotonically increasing nonce, preventing API errors
    related to nonce reuse. It uses a high-resolution timer and falls back
    to incrementing the last known value if the timer hasn't advanced.
    """
    def __init__(self):
        self._last_nonce = 0
        self._lock = threading.Lock()

    def generate_nonce(self) -> str:
        """
        Generates a new, unique, and monotonically increasing nonce.

        Returns:
            A string representation of the unique nonce.
        """
        with self._lock:
            # Get current time in nanoseconds for high resolution
            current_nonce = time.time_ns()

            # Ensure the new nonce is strictly greater than the last one
            if current_nonce <= self._last_nonce:
                self._last_nonce += 1
            else:
                self._last_nonce = current_nonce

            return str(self._last_nonce)
