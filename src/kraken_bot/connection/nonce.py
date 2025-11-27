# src/kraken_bot/connection/nonce.py

import time
import threading

class NonceGenerator:
    """
    Generates monotonically increasing nonces for Kraken API requests.
    Uses high-resolution timer to prevent collisions in quick succession.
    """
    def __init__(self):
        self._lock = threading.Lock()
        self._last_nonce = 0

    def generate(self) -> int:
        """
        Returns a unique, strictly increasing nonce (milliseconds based).
        """
        with self._lock:
            # Kraken expects milliseconds.
            # Using time.time_ns() / 1_000_000 gives us milliseconds with higher precision source.
            nonce = int(time.time_ns() / 1_000_000)

            # Ensure strict monotonicity
            if nonce <= self._last_nonce:
                nonce = self._last_nonce + 1

            self._last_nonce = nonce
            return nonce
