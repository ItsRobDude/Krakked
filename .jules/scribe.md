## 2024-05-24 - Python Version Mismatch **Gap:** README.md stated "Python 3.10+" but `pyproject.toml` enforces ">=3.11,<4.0". This could cause installation failures for users following the README. **Fix:** Updated README.md to specify Python 3.11+.

## 2025-02-12 - Missing UI Build Instructions **Gap:** README.md instructions for running the bot locally omit the necessary frontend build steps, leading to a missing UI (FastAPI serves API only). **Fix:** Added Node.js prerequisite and UI build instructions to the Installation & Setup section.
