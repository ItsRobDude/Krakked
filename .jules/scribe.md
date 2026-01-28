## 2024-05-24 - Python Version Mismatch **Gap:** README.md stated "Python 3.10+" but `pyproject.toml` enforces ">=3.11,<4.0". This could cause installation failures for users following the README. **Fix:** Updated README.md to specify Python 3.11+.

## 2026-01-28 - AuthMiddleware Security Mismatch **Gap:** `AuthMiddleware` uses standard string comparison for tokens, vulnerable to timing attacks, contradicting security best practices. **Fix:** Documented gap; requires code change to use `secrets.compare_digest`.

## 2026-01-28 - UI Static Asset Path Hardcoding **Gap:** `src/kraken_bot/ui/api.py` hardcodes `ui_dir` relative to `__file__`, ignoring `UI_DIST_DIR` or Docker environment variables. **Fix:** Documented gap; requires refactor to honor env vars.
