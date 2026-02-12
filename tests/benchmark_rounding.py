import time
from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal
from functools import lru_cache


@dataclass
class PairMetadata:
    volume_decimals: int
    price_decimals: int


class MockMarketData:
    def get_pair_metadata(self, pair: str):
        return PairMetadata(volume_decimals=8, price_decimals=2)


class PortfolioOriginal:
    def __init__(self, market_data):
        self.market_data = market_data

    def _round_vol(self, pair: str, vol: float) -> float:
        try:
            meta = self.market_data.get_pair_metadata(pair)
            d_vol = Decimal(str(vol))
            quantizer = Decimal("1." + "0" * meta.volume_decimals)
            return float(d_vol.quantize(quantizer, rounding=ROUND_FLOOR))
        except Exception:
            if vol < 1e-9:
                return 0.0
            return vol


class PortfolioOptimized:
    def __init__(self, market_data):
        self.market_data = market_data

    @staticmethod
    @lru_cache(maxsize=128)
    def _get_quantizer(decimals: int) -> Decimal:
        return Decimal("1." + "0" * decimals)

    def _round_vol(self, pair: str, vol: float) -> float:
        try:
            meta = self.market_data.get_pair_metadata(pair)
            d_vol = Decimal(str(vol))
            # Use cached quantizer
            quantizer = self._get_quantizer(meta.volume_decimals)
            return float(d_vol.quantize(quantizer, rounding=ROUND_FLOOR))
        except Exception:
            if vol < 1e-9:
                return 0.0
            return vol


def run_benchmark():
    market_data = MockMarketData()
    p_orig = PortfolioOriginal(market_data)
    p_opt = PortfolioOptimized(market_data)

    iterations = 200000
    pair = "XBTUSD"
    vol = 1.23456789123

    # Warmup
    for _ in range(100):
        p_orig._round_vol(pair, vol)
        p_opt._round_vol(pair, vol)

    times_orig = []
    times_opt = []

    for _ in range(5):
        start = time.time()
        for _ in range(iterations):
            p_orig._round_vol(pair, vol)
        end = time.time()
        times_orig.append(end - start)

        start = time.time()
        for _ in range(iterations):
            p_opt._round_vol(pair, vol)
        end = time.time()
        times_opt.append(end - start)

    avg_orig = sum(times_orig) / len(times_orig)
    avg_opt = sum(times_opt) / len(times_opt)

    print(f"Iterations: {iterations}")
    print(f"Original (avg 5 runs): {avg_orig:.4f}s")
    print(f"Optimized (avg 5 runs): {avg_opt:.4f}s")
    print(f"Improvement: {(avg_orig - avg_opt) / avg_orig * 100:.2f}%")


if __name__ == "__main__":
    run_benchmark()
