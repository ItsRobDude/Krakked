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

## 2026-02-13 - Vectorized DataFrame to Object Conversion
**Learning:** `df.to_dict("records")` followed by iteration is significantly slower (~40-50%) than extracting columns to lists and zipping them when converting Pandas DataFrames to dataclasses or objects.
**Action:** Prefer `zip(df[col].tolist(), ...)` for high-performance object instantiation from DataFrames, especially in hot paths like `OHLCStore`.
