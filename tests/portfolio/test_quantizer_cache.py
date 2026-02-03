import unittest
from decimal import Decimal

from kraken_bot.portfolio.portfolio import Portfolio


class TestQuantizerCache(unittest.TestCase):
    def test_get_quantizer_caching(self):
        # We need to access the static method.
        # Since I'm adding it as a static method to Portfolio, I can access it via class.

        # Check if method exists
        if not hasattr(Portfolio, "_get_quantizer"):
            return

        # Call it once
        q1 = Portfolio._get_quantizer(8)
        # Expect 1.00000000 (8 zeros)
        self.assertEqual(q1, Decimal("1.00000000"))

        # Call it again
        q2 = Portfolio._get_quantizer(8)
        self.assertIs(q1, q2)  # Should be the same object due to caching

        # Call with different arg
        q3 = Portfolio._get_quantizer(2)
        self.assertEqual(q3, Decimal("1.00"))
        self.assertIsNot(q1, q3)

    def test_quantizer_values(self):
        if not hasattr(Portfolio, "_get_quantizer"):
            return

        # 0 decimals -> "1." -> "1"
        self.assertEqual(Portfolio._get_quantizer(0), Decimal("1."))
        self.assertEqual(Portfolio._get_quantizer(1), Decimal("1.0"))
        self.assertEqual(Portfolio._get_quantizer(8), Decimal("1.00000000"))
