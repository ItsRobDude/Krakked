## 2024-05-24 - Python Version Mismatch **Gap:** README.md stated "Python 3.10+" but `pyproject.toml` enforces ">=3.11,<4.0". This could cause installation failures for users following the README. **Fix:** Updated README.md to specify Python 3.11+.

## 2025-05-25 - MarketDataAPI Docs **Gap:** `get_latest_price` and `get_best_bid_ask` in `MarketDataAPI` lacked docstrings explaining their fallback logic (WS -> REST), error guarantees (`DataStaleError`), and return semantics (Mid-price). **Fix:** Added comprehensive docstrings to both methods.
