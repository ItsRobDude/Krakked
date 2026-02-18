# Bolt: Cache trade pair metadata resolution

This optimization caches the result of `get_pair_metadata` and `normalize_asset` in `Portfolio._process_trade`.
These lookups are performed for every single trade ingested or processed. For large history syncing (e.g., 20k+ trades),
the repetitive string normalization and dictionary lookups add measurable overhead.

By caching `trade_pair -> (canonical_pair, base_asset, quote_asset)`, we skip:
1. `get_pair_metadata` (and its internal `normalize_pair` string ops).
2. Two calls to `normalize_asset` per trade.

Benchmark (100k trades):
- Before: ~1.54s
- After: ~1.44s
- Improvement: ~6-8% (~100ms per 100k trades)

The cache is unbounded but effectively limited by the size of the trading universe (typically < 100 pairs), so memory impact is negligible.

## 2025-02-18 - Optimize Pandas DataFrame to Object conversion
**Learning:** The method `df.to_dict('records')` is extremely slow for converting Pandas DataFrames to lists of objects (like `OHLCBar`) because it iterates row-by-row in Python. Vectorized column extraction with `tolist()` and `zip` is 2-3x faster for large datasets.
**Action:** When converting DataFrames to objects in performance-critical paths (e.g., market data ingestion, backtesting), use vectorized list extraction instead of row-wise iteration.

## 2025-02-18 - Mypy and Pandas
**Learning:** Mypy often fails to infer types for Pandas DataFrame/Series operations like `tolist()`, treating them as `DataFrame` or `Any` without correct attributes.
**Action:** Explicitly use `cast(Any, df['col'])` before calling methods like `tolist()` or `astype()` to suppress false positives in CI.
