import pytest
from kraken_trader.nonce import NonceGenerator

def test_nonce_generator_is_monotonic():
    """
    Tests that the NonceGenerator produces strictly increasing nonces.
    """
    generator = NonceGenerator()

    last_nonce = 0

    # Generate a batch of nonces in a tight loop to test monotonicity
    for _ in range(1000):
        new_nonce_str = generator.generate_nonce()
        new_nonce = int(new_nonce_str)

        assert new_nonce > last_nonce

        last_nonce = new_nonce

def test_nonce_generator_is_thread_safe():
    """
    Tests that the NonceGenerator is thread-safe and produces unique nonces
    across multiple threads.
    """
    import threading

    generator = NonceGenerator()
    results = []

    def generate_nonces_in_thread():
        thread_nonces = [generator.generate_nonce() for _ in range(100)]
        results.extend(thread_nonces)

    threads = [threading.Thread(target=generate_nonces_in_thread) for _ in range(10)]

    for t in threads:
        t.start()

    for t in threads:
        t.join()

    # Verify that all generated nonces are unique
    assert len(results) == 1000
    assert len(set(results)) == 1000
