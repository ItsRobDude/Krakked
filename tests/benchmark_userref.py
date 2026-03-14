import time

from kraken_bot.execution.userref import resolve_userref


def main():
    N = 100_000
    # Generate random strings to simulate tags, but repeat a few
    tags = ["alpha:1h", "beta:4h", "gamma:1d"] * (N // 3)
    # Ensure exact length
    tags.extend(["alpha:1h"] * (N - len(tags)))

    start_time = time.time()
    for tag in tags:
        resolve_userref(tag)
    end_time = time.time()

    print(f"Time for {N} calls: {end_time - start_time:.4f}s")


if __name__ == "__main__":
    main()
