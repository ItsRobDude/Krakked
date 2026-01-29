import unittest
from decimal import Decimal

from kraken_bot.portfolio.portfolio import Portfolio


class TestQuantizerCache(unittest.TestCase):
    def test_get_quantizer_returns_correct_decimal(self):
        # 8 decimals -> "1.00000000"
        q = Portfolio._get_quantizer(8)
        self.assertEqual(q, Decimal("1.00000000"))

        # 2 decimals -> "1.00"
        q = Portfolio._get_quantizer(2)
        self.assertEqual(q, Decimal("1.00"))

        # 0 decimals -> "1."
        q = Portfolio._get_quantizer(0)
        self.assertEqual(q, Decimal("1."))

    def test_get_quantizer_caches_result(self):
        q1 = Portfolio._get_quantizer(5)
        q2 = Portfolio._get_quantizer(5)

        # Should be the same object because of caching
        self.assertIs(q1, q2)

        q3 = Portfolio._get_quantizer(6)
        self.assertIsNot(q1, q3)

    def test_cache_hits(self):
        # Clear cache to ensure clean state
        Portfolio._get_quantizer.cache_clear()

        Portfolio._get_quantizer(4)
        info = Portfolio._get_quantizer.cache_info()
        self.assertEqual(info.misses, 1)
        # hits might be > 0 if other tests ran, but we cleared it.
        self.assertEqual(info.hits, 0)

        Portfolio._get_quantizer(4)
        info = Portfolio._get_quantizer.cache_info()
        self.assertEqual(info.misses, 1)
        self.assertEqual(info.hits, 1)


if __name__ == "__main__":
    unittest.main()
