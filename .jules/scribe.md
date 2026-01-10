## 2024-05-24 - Python Version Mismatch **Gap:** README.md stated "Python 3.10+" but `pyproject.toml` enforces ">=3.11,<4.0". This could cause installation failures for users following the README. **Fix:** Updated README.md to specify Python 3.11+.

## 2024-12-07 - Node.js Prerequisite Missing **Gap:** README.md omitted Node.js as a prerequisite, yet `npm ci` is required for the development workflow (building UI types for Pyright). **Fix:** Added "Node.js 20+" to Prerequisites in README.md.
