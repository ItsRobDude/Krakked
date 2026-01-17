## 2024-05-24 - Python Version Mismatch **Gap:** README.md stated "Python 3.10+" but `pyproject.toml` enforces ">=3.11,<4.0". This could cause installation failures for users following the README. **Fix:** Updated README.md to specify Python 3.11+.

## 2026-01-17 - Missing UI Build Instructions **Gap:** README did not verify that users must build `ui/` assets (`npm run build`) for the dashboard to work, despite listing it as a feature. **Fix:** Added Node.js prerequisite and build steps to README.

## 2026-01-17 - Docker UI Path Mismatch **Gap:** `Dockerfile` sets `UI_DIST_DIR` environment variable, but `src/kraken_bot/ui/api.py` hardcodes the path relative to `__file__`, causing potential UI failures in containerized environments. **Fix:** Documented gap; requires code change in `api.py`.
