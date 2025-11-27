# tests/test_nonce.py

import time
import pytest
from kraken_bot.connection.nonce import NonceGenerator

def test_nonce_monotonicity():
    """Ensure generated nonces are strictly increasing."""
    generator = NonceGenerator()
    nonce1 = generator.generate()
    time.sleep(0.001)  # Ensure at least 1ms passes
    nonce2 = generator.generate()

    assert nonce2 > nonce1

def test_nonce_collision_avoidance():
    """Ensure nonces are increasing even if called faster than 1ms."""
    generator = NonceGenerator()

    # Generate a burst of nonces
    nonces = [generator.generate() for _ in range(100)]

    # Verify strict increase
    for i in range(len(nonces) - 1):
        assert nonces[i+1] > nonces[i]

def test_nonce_format():
    """Ensure nonce is an integer (epoch milliseconds)."""
    generator = NonceGenerator()
    nonce = generator.generate()
    assert isinstance(nonce, int)

    # Sanity check: nonce should be close to current time in ms
    now_ms = int(time.time() * 1000)
    assert abs(nonce - now_ms) < 1000 # Within 1 second
